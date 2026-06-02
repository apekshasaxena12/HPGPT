# backend/agents/rag_api/summarize.py

import os
import requests
from dotenv import load_dotenv
from typing import Dict, Any
import logging
import asyncio

logger = logging.getLogger(__name__)
load_dotenv()

RAG_API_KEY = os.getenv("RAG_API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL")
HEADERS = {"Authorization": f"Bearer {RAG_API_KEY}"}


def get_latest_uploaded_file_path() -> str:
    uploads_dir = os.path.abspath("uploads")
    if not os.path.exists(uploads_dir) or not os.listdir(uploads_dir):
        raise FileNotFoundError("❌ No file found in the 'uploads/' directory.")

    return max(
        [os.path.join(uploads_dir, f) for f in os.listdir(uploads_dir)],
        key=os.path.getmtime
    )


def upload_file_to_server(file_path: str, chat_id: str = "default-session") -> str:
    upload_url = f"{API_BASE_URL}/upload"
    with open(file_path, "rb") as file_data:
        files = {
            "files": (os.path.basename(file_path), file_data),
            "chat_id": (None, chat_id)
        }
        response = requests.post(upload_url, headers=HEADERS, files=files)

    if response.status_code != 200:
        raise RuntimeError(f"❌ Upload failed: {response.status_code} - {response.text}")

    uploaded = response.json()
    if not uploaded or "filenames" not in uploaded or not uploaded["filenames"]:
        raise ValueError("❌ Invalid upload response")

    return uploaded["filenames"][0]  # Server-side filename


# LangGraph-compatible async summarization task
async def summarize_task(state: Dict[str, Any]) -> Dict[str, Any]:
    try:
        chat_id = state.get("chat_id", "default-session")
        local_path = get_latest_uploaded_file_path()
        filename_on_server = upload_file_to_server(local_path, chat_id)

        state["doc_id"] = filename_on_server  # Save for downstream

        summarize_url = f"{API_BASE_URL}/summarize"
        form_data = [("filenames", filename_on_server)]
        res = requests.post(summarize_url, headers=HEADERS, data=form_data)

        res.raise_for_status()
        result = res.json()

        if not isinstance(result, list) or not result:
            raise ValueError("❌ Empty summarization result")

        summary = result[0].get("summary") or str(result[0])
        logger.info(f"✅ Summarized '{filename_on_server}'")

        return {**state, "response": summary}

    except Exception as e:
        logger.error(f"❌ Error in summarize_task: {e}")
        return {**state, "response": f"Summarization error: {str(e)}"}


if __name__ == "__main__":
    try:
        file_path = get_latest_uploaded_file_path()
        upload_url = f"{API_BASE_URL}/upload"

        # Upload file to server
        with open(file_path, "rb") as f:
            files = {
                "files": (os.path.basename(file_path), f, "application/pdf"),
                "chat_id": (None, "dev-main")  # Or any default/test session ID
            }
            upload_res = requests.post(upload_url, headers=HEADERS, files=files)

        if upload_res.status_code != 200:
            print(f"Upload failed: {upload_res.status_code} - {upload_res.text}")
            exit(1)

        uploaded_filenames = upload_res.json().get("filenames", [])
        if not uploaded_filenames:
            print("No filenames returned from upload.")
            exit(1)

        # Build state and run summarization
        state = {
            "chat_id": "dev-main",  # Static chat_id for testing
            "input": ""
        }

        result = asyncio.run(summarize_task(state))
        print(f"Summary: {result.get('response')}")

    except Exception as e:
        print(f"❌ Error: {e}")
