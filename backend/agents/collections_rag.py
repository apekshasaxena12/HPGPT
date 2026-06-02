import os
import logging
from typing import List
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
import pdfplumber
from dotenv import load_dotenv
from backend.utils.groq_client import groq_client

load_dotenv()
logger = logging.getLogger(__name__)

# Global vectorstore for all collection documents
collection_vectorstore = None
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

# REPLACE extract_text function with this
def extract_text(file_path: str) -> str:
    if file_path.endswith(".pdf"):
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        return text.strip()
    
    elif file_path.endswith(".docx"):
        from docx import Document
        doc = Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    
    elif file_path.endswith(".csv"):
        import pandas as pd
        df = pd.read_csv(file_path)
        return f"CSV File: {os.path.basename(file_path)}\nColumns: {', '.join(df.columns.tolist())}\n\n{df.to_string(index=False)}"
    
    elif file_path.endswith(".xlsx"):
        import pandas as pd
        df = pd.read_excel(file_path, engine="openpyxl")
        # Detect CSV saved as xlsx
        if len(df.columns) == 1 and ',' in df.columns[0]:
            import io
            raw = df.columns[0] + '\n' + df.iloc[:, 0].str.cat(sep='\n')
            df = pd.read_csv(io.StringIO(raw))
        return f"Excel File: {os.path.basename(file_path)}\nColumns: {', '.join(df.columns.tolist())}\n\n{df.to_string(index=False)}"
    
    elif file_path.endswith(".db"):
        import sqlite3
        conn = sqlite3.connect(file_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        text = f"SQLite Database: {os.path.basename(file_path)}\n\n"
        for (table_name,) in tables:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 100")
            rows = cursor.fetchall()
            cursor.execute(f"PRAGMA table_info({table_name})")
            cols = [col[1] for col in cursor.fetchall()]
            text += f"Table: {table_name}\nColumns: {', '.join(cols)}\n"
            for row in rows:
                text += str(row) + "\n"
            text += "\n"
        conn.close()
        return text
    
    elif file_path.endswith(".sql"):
        with open(file_path, "r", errors="ignore") as f:
            return f.read()
    
    else:
        with open(file_path, "r", errors="ignore") as f:
            return f.read()

def index_document(file_path: str, metadata: dict = {}):
    """Index a document into the global collections vectorstore."""
    global collection_vectorstore
    
    text = extract_text(file_path)
    if not text:
        raise ValueError("No text extracted from file.")

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    chunks = splitter.split_text(text)

    # Add metadata to each chunk
    texts_with_meta = chunks
    metadatas = [metadata] * len(chunks)

    if collection_vectorstore is None:
        collection_vectorstore = FAISS.from_texts(texts_with_meta, embedding=embeddings, metadatas=metadatas)
    else:
        new_vs = FAISS.from_texts(texts_with_meta, embedding=embeddings, metadatas=metadatas)
        collection_vectorstore.merge_from(new_vs)

    logger.info(f"✅ Indexed collection document: {file_path}")

def query_collections(question: str, k: int = 5) -> dict:
    global collection_vectorstore

    if collection_vectorstore is None:
        return {"answer": "No documents have been indexed in collections yet.", "sources": []}

    docs = collection_vectorstore.similarity_search(question, k=k)
    context = "\n\n".join([
        f"[Source: {d.metadata.get('department', '')}/{d.metadata.get('subcategory', '')} - {d.metadata.get('filename', '')}]\n{d.page_content}"
        for d in docs
    ])

    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful HPCL document assistant.\n"
                "Answer the user's question using ONLY the context below from HPCL department documents.\n"
                "Always mention which department/document the information comes from.\n"
                "If the answer isn't in the context, say so."
            )
        },
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {question}"
        }
    ]

    try:
        response = groq_client.client.chat.completions.create(
            model=groq_client.model,
            messages=messages,
            temperature=0.3,
            max_tokens=1024,
        )
        answer = response.choices[0].message.content.strip()
        sources = []
        seen = set()
        for d in docs:
            path = d.metadata.get("path", "")
            if path and path not in seen:
                seen.add(path)
                sources.append({
                    "name": d.metadata.get("filename", ""),
                    "department": d.metadata.get("department", ""),
                    "subcategory": d.metadata.get("subcategory", ""),
                    "path": path
                })
        return {"answer": answer, "sources": sources}
    except Exception as e:
        logger.error(f"Q&A failed: {e}")
        return {"answer": f"Error generating answer: {str(e)}", "sources": []}

def rebuild_index_from_disk():
    global collection_vectorstore
    collections_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "collections")
    collections_dir = os.path.normpath(collections_dir)
    if not os.path.exists(collections_dir):
        logger.warning(f"Collections dir not found: {collections_dir}")
        return

    for root, dirs, files in os.walk(collections_dir):
        for file in files:
            file_path = os.path.join(root, file)
            parts = os.path.relpath(file_path, collections_dir).split(os.sep)
            metadata = {
                "department": parts[0] if len(parts) > 0 else "",
                "subcategory": parts[1] if len(parts) > 1 else "",
                "filename": file,
                "path": file_path
            }
            try:
                index_document(file_path, metadata)
            except Exception as e:
                logger.error(f"Failed to index {file_path}: {e}")

    logger.info("✅ Collections index rebuilt from disk.")