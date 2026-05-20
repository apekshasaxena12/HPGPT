import os
import requests
from dotenv import load_dotenv
from typing import List

load_dotenv()
RAG_API_KEY = os.getenv("RAG_API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL")

if not RAG_API_KEY or not API_BASE_URL:
    raise EnvironmentError("Missing RAG_API_KEY or API_BASE_URL in .env")

HEADERS = {"Authorization": f"Bearer {RAG_API_KEY}"}

UPLOAD_DIR = "uploads"

def get_latest_files(n: int) -> List[str]:
    files = [os.path.join(UPLOAD_DIR, f) for f in os.listdir(UPLOAD_DIR) if os.path.isfile(os.path.join(UPLOAD_DIR, f))]
    files.sort(key=os.path.getmtime, reverse=True)
    return files[:n]

def upload_file_to_server(filepath: str, chat_id: str = "default-session") -> str:
    url = f"{API_BASE_URL}/upload"
    with open(filepath, "rb") as f:
        files = [("files", (os.path.basename(filepath), f.read(), "application/pdf"))]
        data = {"chat_id": chat_id}
        response = requests.post(url, headers=HEADERS, files=files, data=data)

    if response.status_code != 200:
        raise RuntimeError(f"Upload failed for {filepath}: {response.text}")

    return response.json().get("filenames", [])[0]  # Return only the uploaded filename

def compare_uploaded_files(filenames: List[str], answer_mode: str = "specific") -> dict:
    url = f"{API_BASE_URL}/compare"
    form_data = [("filenames", name) for name in filenames]
    form_data.append(("answer_mode", answer_mode))
    response = requests.post(url, headers=HEADERS, data=form_data)

    if response.status_code != 200:
        raise RuntimeError(f"Comparison failed: {response.text}")

    return response.json()

# Entry function for your compare sub-agent
def run_compare_agent(chat_id: str = "default-session", answer_mode: str = "specific") -> dict:
    latest_files = get_latest_files(2)
    if len(latest_files) < 2:
        return {"response": "❌ Need at least 2 files to compare."}
    uploaded_filenames = []
    for file_path in latest_files:
        uploaded_name = upload_file_to_server(file_path, chat_id)
        uploaded_filenames.append(uploaded_name)
    return compare_uploaded_files(uploaded_filenames, answer_mode=answer_mode)

if __name__ == "__main__":
    result = run_compare_agent()
    print("🧾 Compare Result:", result)
