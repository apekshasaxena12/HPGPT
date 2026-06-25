import os
import shutil
import logging
from typing import List
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

import requests as _requests
from langchain_community.embeddings import HuggingFaceEmbeddings
import pdfplumber
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
RAG_API_KEY = os.getenv("RAG_API_KEY")
UPLOAD_DIR  = "rag_uploads"
OLLAMA_URL  = "http://localhost:11434/v1/chat/completions"
QWEN_MODEL  = "qwen2.5:7b"

os.makedirs(UPLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="RAG Server")

# In-memory store: { filename -> FAISS vectorstore }
vectorstores: dict = {}


# ── Auth ─────────────────────────────────────────────────────────────────────
def verify_token(authorization: str = Header(...)):
    if not RAG_API_KEY:
        return  # No key set → skip auth
    expected = f"Bearer {RAG_API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Helpers ──────────────────────────────────────────────────────────────────
def extract_text(file_path: str) -> str:
    """Extract text from PDF or plain text file."""
    if file_path.endswith(".pdf"):
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        return text.strip()
    else:
        with open(file_path, "r", errors="ignore") as f:
            return f.read()


def build_vectorstore(text: str, filename: str) -> FAISS:
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    chunks = splitter.split_text(text)
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    return FAISS.from_texts(chunks, embedding=embeddings)

def qwen_generate(prompt: str) -> str:
    """Call Qwen2.5 via Ollama to generate a response."""
    import re
    payload = {
        "model": QWEN_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": False,
    }
    resp = _requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    text = re.sub(r'#{1,3}\s*', '', text)
    text = re.sub(r'---+', '\n', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


# ── Routes ───────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload(
    files: List[UploadFile] = File(...),
    chat_id: str = Form("default-session"),
    _: None = Depends(verify_token)
):
    """Upload one or more files, extract text, and index into FAISS."""
    saved_filenames = []

    for upload_file in files:
        safe_name = f"{chat_id}__{upload_file.filename}"
        save_path = os.path.join(UPLOAD_DIR, safe_name)

        with open(save_path, "wb") as f:
            shutil.copyfileobj(upload_file.file, f)

        try:
            text = extract_text(save_path)
            if not text:
                raise ValueError("No text extracted from file.")
            vectorstores[safe_name] = build_vectorstore(text, safe_name)
            saved_filenames.append(safe_name)
            logger.info(f"✅ Indexed: {safe_name}")
        except Exception as e:
            logger.error(f"❌ Failed to index {safe_name}: {e}")
            raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")

    return JSONResponse({"filenames": saved_filenames})


@app.post("/summarize")
async def summarize(
    filenames: List[str] = Form(...),
    _: None = Depends(verify_token)
):
    """Summarize one or more already-uploaded documents."""
    results = []

    for filename in filenames:
        vs = vectorstores.get(filename)
        if not vs:
            results.append({"filename": filename, "summary": "❌ File not found. Upload it first."})
            continue

        # Retrieve the most representative chunks
        docs = vs.similarity_search("summary overview main points", k=6)
        context = "\n\n".join([d.page_content for d in docs])

        prompt = (
            "You are a document summarization assistant.\n"
            "Summarize the following document content clearly and concisely "
            "in 5-8 sentences covering the key points:\n\n"
            f"{context}"
        )

        try:
            summary = qwen_generate(prompt)
            results.append({"filename": filename, "summary": summary})
            logger.info(f"✅ Summarized: {filename}")
        except Exception as e:
            logger.error(f"❌ Summarize error for {filename}: {e}")
            results.append({"filename": filename, "summary": f"❌ Error: {str(e)}"})

    return JSONResponse(results)


@app.post("/query")
async def query(
    prompt: str = Form(...),
    doc_id: str = Form(...),
    chat_id: str = Form("default-session"),
    _: None = Depends(verify_token)
):
    """Answer a question based on an uploaded document."""
    vs = vectorstores.get(doc_id)
    if not vs:
        raise HTTPException(status_code=404, detail="Document not found. Upload it first.")

    # Retrieve relevant chunks
    docs = vs.similarity_search(prompt, k=5)
    context = "\n\n".join([d.page_content for d in docs])

    full_prompt = (
        "You are a helpful document assistant. "
        "Answer the user's question using ONLY the context below. "
        "If the answer isn't in the context, say so.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {prompt}"
    )

    try:
        answer = qwen_generate(full_prompt)
        logger.info(f"✅ Query answered for {doc_id}")
        return JSONResponse({"result": answer})
    except Exception as e:
        logger.error(f"❌ Query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok", "indexed_docs": list(vectorstores.keys())}


@app.post("/compare")
async def compare(
    filenames: List[str] = Form(...),
    answer_mode: str = Form("specific"),
    _: None = Depends(verify_token)
):

    """Compare two uploaded documents."""
    if len(filenames) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 filenames to compare.")

    contexts = []
    for filename in filenames:
        vs = vectorstores.get(filename)
        if not vs:
            raise HTTPException(status_code=404, detail=f"'{filename}' not found. Upload it first.")
        docs = vs.similarity_search("main points overview", k=5)
        contexts.append("\n".join([d.page_content for d in docs]))

    if answer_mode == "specific":
        prompt = (
            f"Briefly compare these two documents in 3-4 sentences covering only the most important differences.\n\n"
            f"Document 1 ({filenames[0]}):\n{contexts[0]}\n\n"
            f"Document 2 ({filenames[1]}):\n{contexts[1]}"
        )
    else:
        prompt = (
            f"Compare these two documents in detail.\n"
            f"Structure your response with an intro, Similarities section, and Differences section.\n"
            f"Each point on a new line with blank lines between sections.\n\n"
            f"Document 1 ({filenames[0]}):\n{contexts[0]}\n\n"
            f"Document 2 ({filenames[1]}):\n{contexts[1]}"
        )

    try:
        result = qwen_generate(prompt)
        return JSONResponse({"comparison": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))