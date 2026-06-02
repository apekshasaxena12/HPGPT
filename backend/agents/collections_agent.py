import os
import logging
from dotenv import load_dotenv
from backend.utils.groq_client import groq_client

load_dotenv()
logger = logging.getLogger(__name__)


CATEGORIES = {
    "Refineries": ["refinery", "R&D", "International_trade"],
    "Marketing": ["Retail", "LPG", "Lubes", "Pipeline", "Aviation", "Finance_Marketing"],
    "Human_Resources": ["Human_Resources", "Quality_Assurance"],
    "Finance": ["Corporate_Finance", "Tax"],
    "Others": ["Information_System"]
}

def get_all_subcategories() -> list:
    result = []
    for dept, subs in CATEGORIES.items():
        for sub in subs:
            result.append(f"{dept}/{sub}")
    return result




def suggest_category(text: str) -> dict:
    """Use Groq agent to suggest department and subcategory for a document."""
    all_subs = get_all_subcategories()
    options = "\n".join(all_subs)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a document classification agent for HPCL (Hindustan Petroleum Corporation Limited).\n"
                "Your job is to read a document and classify it into the correct department and subcategory.\n"
                f"Available categories (Department/Subcategory):\n{options}\n\n"
                "Respond with ONLY the category in format: Department/Subcategory\n"
                "Example: Refineries/refinery\n"
                "If unsure, respond with: Others/Information_System"
            )
        },
        {
            "role": "user",
            "content": f"Classify this document:\n\n{text[:1000]}"
        }
    ]

    try:
        response = groq_client.client.chat.completions.create(
            model=groq_client.model,
            messages=messages,
            temperature=0,
            max_tokens=20,
        )
        suggestion = response.choices[0].message.content.strip()

        if suggestion in all_subs:
            parts = suggestion.split("/")
            return {"department": parts[0], "subcategory": parts[1], "full_path": suggestion}
        else:
            return {"department": "Others", "subcategory": "Information_System", "full_path": "Others/Information_System"}
    except Exception as e:
        logger.error(f"Category suggestion failed: {e}")
        return {"department": "Others", "subcategory": "Information_System", "full_path": "Others/Information_System"}

def save_document_to_collection(file_path: str, department: str, subcategory: str, original_filename: str) -> str:
    """Copy document to the correct collection folder."""
    dest_dir = os.path.join("collections", department, subcategory)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, original_filename)
    
    import shutil
    shutil.copy2(file_path, dest_path)
    logger.info(f"✅ Saved to collection: {dest_path}")
    return dest_path

def list_documents(department: str = None, subcategory: str = None) -> list:
    """List documents in a collection."""
    if department and subcategory:
        folder = os.path.join("collections", department, subcategory)
    elif department:
        folder = os.path.join("collections", department)
    else:
        folder = "collections"

    if not os.path.exists(folder):
        return []

    docs = []
    for root, dirs, files in os.walk(folder):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, "collections")
            parts = rel_path.split(os.sep)
            docs.append({
                "name": file,
                "department": parts[0] if len(parts) > 0 else "",
                "subcategory": parts[1] if len(parts) > 1 else "",
                "path": full_path
            })
    return docs