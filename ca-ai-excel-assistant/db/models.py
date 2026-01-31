"""
Data models for MongoDB documents (Step 3).
Structures for files, data_rows, and chat_history collections.
"""
from datetime import datetime
from typing import Any, Optional

# ---------------------------------------------------------------------------
# File metadata (collection: files)
# ---------------------------------------------------------------------------
FILE_FIELDS = ["fileId", "uploadDate", "clientTag", "filename", "rowCount", "createdAt"]


def file_doc(
    file_id: str,
    upload_date: str,
    filename: str,
    row_count: int,
    client_tag: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> dict:
    """Build a file document for insertion."""
    return {
        "fileId": file_id,
        "uploadDate": upload_date,
        "clientTag": client_tag,
        "filename": filename,
        "rowCount": row_count,
        "createdAt": created_at or datetime.utcnow(),
    }


# ---------------------------------------------------------------------------
# Row-level data (collection: data_rows)
# fileId, uploadDate, clientTag, rowDate (optional) + dynamic Excel columns
# ---------------------------------------------------------------------------
ROW_FIELDS = ["fileId", "uploadDate", "clientTag", "rowDate"]


def row_doc(
    file_id: str,
    upload_date: str,
    row_data: dict,
    client_tag: Optional[str] = None,
    row_date: Optional[str] = None,
) -> dict:
    """Build a row document. row_data contains normalized Excel columns."""
    doc = {
        "fileId": file_id,
        "uploadDate": upload_date,
        "clientTag": client_tag,
        "rowDate": row_date,
    }
    doc.update({k: v for k, v in row_data.items() if k not in doc})
    return doc


# ---------------------------------------------------------------------------
# Chat history (collection: chat_history)
# ---------------------------------------------------------------------------
CHAT_FIELDS = ["question", "answer", "dateContext", "clientTag", "createdAt"]


def chat_doc(
    question: str,
    answer: str,
    date_context: Optional[str] = None,
    client_tag: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> dict:
    """Build a chat document for insertion."""
    return {
        "question": question,
        "answer": answer,
        "dateContext": date_context,
        "clientTag": client_tag,
        "createdAt": created_at or datetime.utcnow(),
    }
