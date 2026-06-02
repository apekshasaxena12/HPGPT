# backend/utils/file_uploader.py

import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_BASE_URL = os.getenv("API_BASE_URL")
RAG_API_KEY = os.getenv("RAG_API_KEY")

HEADERS = {"Authorization": f"Bearer {RAG_API_KEY}"}

def upload_single_file(path: str) -> str:
    url = f"{API_BASE_URL}/upload/"
    with open(path, "rb") as f:
        files = {"files": (os.path.basename(path), f.read(), "application/pdf")}
        res = requests.post(url, headers=HEADERS, files=[files])
    if res.status_code != 200:
        raise Exception(f"Upload failed: {res.text}")
    return res.json().get("filenames", [])[0]
