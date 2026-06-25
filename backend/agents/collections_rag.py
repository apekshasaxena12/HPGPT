import os
import re
import json
import logging
from collections import defaultdict
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
import pdfplumber
from dotenv import load_dotenv

load_dotenv()

from backend.utils.groq_client import groq_client as _groq_client


def _groq_chat(messages: list, temperature: float = 0.3, max_tokens: int = 4096) -> str:
    """Call Llama 3.3 70B via Groq (same model used by the rest of the app)."""
    response = _groq_client.client.chat.completions.create(
        model=_groq_client.model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


logger = logging.getLogger(__name__)

# Global vectorstore for all collection documents
collection_vectorstore = None
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

# Registry of all structured (xlsx/csv) files for direct lookup
_structured_file_registry = []


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


def direct_lookup(question: str, department: str = None, subcategory: str = None) -> tuple:
    """
    Scan xlsx/csv files directly for any ID-like tokens found in the question.
    Returns (context_strings, source_dicts) for rows that matched.
    Results are grouped by entity ID so the LLM sees all data for one entity
    together — preventing cross-entity confusion.
    """
    import pandas as pd

    # Extract candidate lookup tokens: alphanumeric codes and 3+ digit numbers
    tokens = list(
        re.findall(r'\b[A-Z]{0,4}\d{3,}\b', question.upper()) +
        re.findall(r'\b\d{3,}\b', question)
    )

    # Also catch short numeric IDs (1-4 digits) that follow "id" / "employee" keywords.
    # "employee id 20" → extract "20", then generate zero-padded "0020" so it matches "EMP0020".
    short_ids = re.findall(
        r'(?:employee\s+id|emp(?:loyee)?\s*id|id)\s*[:#]?\s*(\d{1,4})\b',
        question, re.IGNORECASE
    )
    for sid in short_ids:
        tokens.append(sid)
        if len(sid) <= 4:
            tokens.append(sid.zfill(4))   # "20" → "0020" matches inside "EMP0020"

    tokens = list(set(tokens))
    if not tokens:
        return [], []

    # token -> list of formatted row lines (one line per file that matched)
    token_rows: dict = {t: [] for t in tokens}
    source_map = {}  # path -> source dict, to deduplicate

    for reg in _structured_file_registry:
        path = reg['path']
        meta = reg['metadata']

        # Apply department/subcategory filter if provided
        if department and meta.get('department', '') != department:
            continue
        if subcategory and meta.get('subcategory', '') != subcategory:
            continue

        try:
            if path.endswith('.xlsx'):
                df = pd.read_excel(path, engine="openpyxl")
                if len(df.columns) == 1 and ',' in df.columns[0]:
                    import io
                    raw = df.columns[0] + '\n' + df.iloc[:, 0].str.cat(sep='\n')
                    df = pd.read_csv(io.StringIO(raw))
            else:
                df = pd.read_csv(path)

            filename = os.path.basename(path)
            cols = df.columns.tolist()
            dept = meta.get('department', '')
            sub  = meta.get('subcategory', '')

            for token in tokens:
                # Digit-boundary pattern: "0009" matches "EMP0009" but not "PF100090"
                pattern = r'(?<![0-9])' + re.escape(token) + r'(?![0-9])'
                matched_indices = set()
                for col in cols:
                    mask = df[col].astype(str).str.upper().str.contains(
                        pattern, na=False, regex=True
                    )
                    matched_indices.update(df[mask].index.tolist())

                for idx in sorted(matched_indices):
                    row = df.loc[idx]
                    row_line = (
                        f"  [{dept}/{sub} - {filename}]: "
                        + " | ".join(f"{c}: {str(row[c])}" for c in cols)
                    )
                    token_rows[token].append(row_line)
                    if path not in source_map:
                        source_map[path] = {
                            "name": filename,
                            "department": dept,
                            "subcategory": sub,
                            "path": path
                        }

        except Exception as e:
            logger.error(f"Direct lookup failed for {path}: {e}")

    # Build one block per entity so the LLM reads all fields for entity X before
    # moving on to entity Y — eliminates cross-entity mixing.
    context_parts = []
    for token in tokens:
        if token_rows[token]:
            block = f"=== Entity ID: {token} ===\n" + "\n".join(token_rows[token])
            context_parts.append(block)

    return context_parts, list(source_map.values())


def index_document(file_path: str, metadata: dict = {}):
    """Index a document into the global collections vectorstore."""
    global collection_vectorstore, _structured_file_registry

    if file_path.endswith(('.xlsx', '.csv')):
        import pandas as pd
        try:
            if file_path.endswith('.xlsx'):
                df = pd.read_excel(file_path, engine="openpyxl")
                if len(df.columns) == 1 and ',' in df.columns[0]:
                    import io
                    raw = df.columns[0] + '\n' + df.iloc[:, 0].str.cat(sep='\n')
                    df = pd.read_csv(io.StringIO(raw))
            else:
                df = pd.read_csv(file_path)

            filename = os.path.basename(file_path)
            cols = df.columns.tolist()
            # One vector per row for semantic search (column names, types, etc.)
            chunks = []
            for _, row in df.iterrows():
                row_text = f"File: {filename} | " + " | ".join(
                    f"{col}: {str(row[col])}" for col in cols
                )
                chunks.append(row_text)

            # Register for direct keyword lookup
            if not any(r['path'] == file_path for r in _structured_file_registry):
                _structured_file_registry.append({'path': file_path, 'metadata': metadata})

        except Exception as e:
            logger.error(f"Row-level indexing failed for {file_path}: {e}. Falling back to text split.")
            text = extract_text(file_path)
            if not text:
                raise ValueError("No text extracted from file.")
            splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
            chunks = splitter.split_text(text)
    else:
        text = extract_text(file_path)
        if not text:
            raise ValueError("No text extracted from file.")
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
        chunks = splitter.split_text(text)

    metadatas = [metadata] * len(chunks)

    if collection_vectorstore is None:
        collection_vectorstore = FAISS.from_texts(chunks, embedding=embeddings, metadatas=metadatas)
    else:
        new_vs = FAISS.from_texts(chunks, embedding=embeddings, metadatas=metadatas)
        collection_vectorstore.merge_from(new_vs)

    logger.info(f"✅ Indexed collection document: {file_path} ({len(chunks)} chunks)")


def get_files_with_column(column_hint: str, department: str = None, subcategory: str = None) -> list:
    """Scan all indexed docs for files whose text contains a given column name."""
    global collection_vectorstore
    if collection_vectorstore is None:
        return []

    docs = collection_vectorstore.similarity_search(column_hint, k=50)

    if department:
        docs = [d for d in docs if d.metadata.get("department", "") == department]
    if subcategory:
        docs = [d for d in docs if d.metadata.get("subcategory", "") == subcategory]

    seen = set()
    matches = []
    for d in docs:
        path = d.metadata.get("path", "")
        if path and path not in seen:
            if column_hint.lower().replace(" ", "_") in d.page_content.lower().replace(" ", "_"):
                seen.add(path)
                matches.append({
                    "name": d.metadata.get("filename", ""),
                    "department": d.metadata.get("department", ""),
                    "subcategory": d.metadata.get("subcategory", ""),
                    "path": path
                })
    return matches


def query_collections(question: str, k: int = 20, department: str = None, subcategory: str = None, history: list = None) -> dict:
    global collection_vectorstore

    if collection_vectorstore is None:
        return {"answer": "No documents have been indexed in collections yet.", "sources": []}

    # --- Step 1: Direct keyword lookup for exact values (IDs, codes, numbers) ---
    direct_context, direct_sources = direct_lookup(question, department, subcategory)

    # For follow-up questions, combine current question + recent user history
    # so short IDs mentioned earlier ("employee id 20") are still found.
    if not direct_context and history:
        history_user_text = question + " " + " ".join(
            turn.get("content", "")
            for turn in history[-6:]
            if turn.get("role") == "user"
        )
        hist_context, hist_sources = direct_lookup(history_user_text, department, subcategory)
        if hist_context:
            direct_context = hist_context
            direct_sources = hist_sources

    # --- Step 2: Semantic vector search for broader context ---
    # When exact direct matches exist, keep vector search small to avoid burying
    # the direct-match rows under hundreds of loosely-related chunks.
    search_k = 30 if direct_context else max(200, k * 10)
    docs = collection_vectorstore.similarity_search(question, k=search_k)

    if department or subcategory:
        docs = [
            d for d in docs
            if (not department or d.metadata.get("department", "") == department)
            and (not subcategory or d.metadata.get("subcategory", "") == subcategory)
        ]
        if not docs and not direct_context:
            dept_label = (department or "").replace("_", " ")
            sub_label = (subcategory or "").replace("_", " ")
            scope = f"{dept_label} > {sub_label}" if dept_label and sub_label else dept_label or sub_label
            return {
                "answer": f"This information doesn't exist in the **{scope}** collection.",
                "sources": [],
                "not_in_collection": True
            }

    # Boost docs from files whose name contains keywords from the question.
    # e.g. "where is india" → "india" matches "India.docx"; "what is my pf" → "pf" matches "employee_pf_info.xlsx".
    # Iterate over the FAISS docs (not just _structured_file_registry) so docx/pdf files are also considered.
    _stop = {'what', 'is', 'my', 'the', 'are', 'was', 'were', 'will', 'how', 'many',
             'much', 'for', 'and', 'or', 'to', 'in', 'of', 'a', 'an', 'this', 'that',
             'get', 'show', 'me', 'tell', 'give', 'find', 'list', 'all', 'about', 'from',
             'where', 'which', 'when', 'do', 'does', 'did', 'can', 'could', 'would',
             'should', 'have', 'has', 'had', 'been', 'be', 'on', 'at', 'by', 'with'}
    q_words = set(re.findall(r'\b\w{2,}\b', question.lower())) - _stop
    name_matched_paths = set()
    for d in docs:
        path = d.metadata.get('path', '')
        if path:
            fname_words = set(re.split(r'[_.\-\s]+', os.path.basename(path).lower())) - {''}
            if q_words & fname_words:
                name_matched_paths.add(path)
    if name_matched_paths:
        boosted = [d for d in docs if d.metadata.get('path', '') in name_matched_paths]
        rest = [d for d in docs if d.metadata.get('path', '') not in name_matched_paths]
        docs = boosted + rest

    # Ensure every source file gets representation in vector results
    file_buckets = defaultdict(list)
    for d in docs:
        file_buckets[d.metadata.get("path", "")].append(d)

    # If file-name boosted files exist, only include those in context to avoid noise.
    # Fall back to all files when there are no name-matched hits.
    if name_matched_paths:
        file_buckets = {p: b for p, b in file_buckets.items() if p in name_matched_paths}

    rows_per_file = max(5, k // max(len(file_buckets), 1))
    diverse_docs = []
    for bucket in file_buckets.values():
        diverse_docs.extend(bucket[:rows_per_file])

    # --- Step 3: Merge — direct hits first (exact matches), then semantic results ---
    vector_context = "\n\n".join([
        f"[Source: {d.metadata.get('department', '')}/{d.metadata.get('subcategory', '')} - {d.metadata.get('filename', '')}]\n{d.page_content}"
        for d in diverse_docs
    ])

    if direct_context:
        context = "=== DIRECT MATCHES (exact lookup) ===\n" + "\n\n".join(direct_context) + "\n\n=== ADDITIONAL CONTEXT ===\n" + vector_context
    else:
        context = vector_context

    # Merge sources: direct sources + vector sources (deduplicated)
    all_source_paths = {s['path'] for s in direct_sources}
    merged_sources = list(direct_sources)
    for d in diverse_docs:
        path = d.metadata.get("path", "")
        if path and path not in all_source_paths:
            all_source_paths.add(path)
            merged_sources.append({
                "name": d.metadata.get("filename", ""),
                "department": d.metadata.get("department", ""),
                "subcategory": d.metadata.get("subcategory", ""),
                "path": path
            })

    # --- Ambiguity detection ---
    source_files = list({d.metadata.get("path", "") for d in diverse_docs if d.metadata.get("path")})
    if len(source_files) > 1 and not direct_context:
        col_hints = re.findall(r'\b[A-Z][A-Za-z_]{3,}\b', question)
        for hint in col_hints:
            matching = get_files_with_column(hint, department, subcategory)
            if len(matching) > 1:
                return {
                    "clarifying": True,
                    "answer": f"I found **{len(matching)} files** that all contain a column matching '{hint}'.\n\nWhich file should I use for this analysis?",
                    "sources": matching
                }

    scope_instruction = ""
    if department or subcategory:
        dept_label = (department or "").replace("_", " ")
        sub_label = (subcategory or "").replace("_", " ")
        scope = f"{dept_label} > {sub_label}" if dept_label and sub_label else dept_label or sub_label
        scope_instruction = f"\nYou are answering from the '{scope}' subcollection only."

    system_prompt = (
        "You are a helpful HPCL document assistant. "
        "Answer the user's question directly and concisely using ONLY the context provided below.\n"
        "Rules:\n"
        "- Never generate questions or ask the user to answer anything.\n"
        "- Never list sub-questions. Just answer.\n"
        "- If the user does not specify an employee ID, summarise the relevant data from the most relevant file.\n"
        "- Report exact values from the data. Do not estimate or calculate unless asked.\n"
        "- Mention which file each value comes from.\n"
        f"- If the answer is genuinely not in the context, say so in one sentence.{scope_instruction}"
    )

    # Build the history turns once (shared across all LLM calls)
    history_turns = []
    if history:
        for turn in history[-6:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                history_turns.append({"role": role, "content": content})

    try:
        msgs = [{"role": "system", "content": system_prompt}]
        msgs.extend(history_turns)
        msgs.append({
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {question}"
        })
        answer = _groq_chat(msgs, temperature=0.3, max_tokens=4096)
        return {"answer": answer, "sources": merged_sources}
    except Exception as e:
        logger.error(f"Q&A failed: {e}")
        return {"answer": f"Error generating answer: {str(e)}", "sources": []}


def rebuild_index_from_disk():
    global collection_vectorstore, _structured_file_registry
    collection_vectorstore = None
    _structured_file_registry = []

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
