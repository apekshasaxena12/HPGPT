import aiofiles
import os
import uuid
from werkzeug.utils import secure_filename

async def save_document_to_disk(session_id: str, original_name: str, file_bytes: bytes) -> str:
    upload_dir = "uploads_document"
    os.makedirs(upload_dir, exist_ok=True)

    file_extension = os.path.splitext(original_name)[1]
    filename_base = os.path.splitext(original_name)[0]
    unique_id = uuid.uuid4().hex[:16]
    safe_filename = secure_filename(f"{session_id}_{unique_id}_{filename_base}{file_extension}")
    file_path = os.path.join(upload_dir, safe_filename)

    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(file_bytes)

    return file_path
