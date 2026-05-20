import os
import requests
from typing import Dict, Any
from dotenv import load_dotenv
import logging

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

def upload_file_to_server(file_path: str, chat_id: str) -> str:
    upload_url = f"{API_BASE_URL}/upload"
    with open(file_path, "rb") as file_data:
        files = {"files": (os.path.basename(file_path), file_data)}
        data = {"chat_id": chat_id}
        response = requests.post(upload_url, headers=HEADERS, files=files, data=data)

    if response.status_code != 200:
        raise RuntimeError(f"❌ Upload failed: {response.status_code} - {response.text}")

    uploaded = response.json()
    if not uploaded or not isinstance(uploaded.get("filenames"), list):
        raise ValueError("❌ Invalid upload response")

    return uploaded["filenames"][0]  # server-side filename

async def query_task(state: Dict[str, Any]) -> Dict[str, Any]:
    try:
        prompt = state.get("input", "")
        chat_id = state.get("chat_id", "default-session")

        # Upload latest file and extract server-side doc_id
        local_path = get_latest_uploaded_file_path()
        doc_id = upload_file_to_server(local_path, chat_id)
        state["doc_id"] = doc_id  # Save for future nodes

        query_url = f"{API_BASE_URL}/query"
        form_data = [
            ("prompt", prompt),
            ("doc_id", doc_id),
            ("chat_id", chat_id),
        ]

        response = requests.post(query_url, headers=HEADERS, data=form_data)
        response.raise_for_status()

        result = response.json()
        if not isinstance(result, dict) or "result" not in result:
            raise ValueError("❌ Invalid query response")

        logger.info(f"✅ Query successful for '{doc_id}'")
        return {**state, "response": result["result"]}

    except Exception as e:
        logger.error(f"❌ Error in query_task: {e}")
        return {**state, "response": f"Query error: {str(e)}"}
