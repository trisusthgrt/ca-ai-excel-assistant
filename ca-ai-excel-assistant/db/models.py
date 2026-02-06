"""
Data models for MongoDB documents (Step 3).
Structures for files, data_rows, and chat_history collections.
"""
from datetime import datetime
from typing import Any, Optional

# ---------------------------------------------------------------------------
# File metadata (collection: files)
# Schema awareness: column_names, column_count, row_count persisted at upload.
# ---------------------------------------------------------------------------
FILE_FIELDS = ["fileId", "uploadDate", "clientTag", "filename", "rowCount", "columnCount", "columnNames", "createdAt"]


def file_doc(
    file_id: str,
    upload_date: str,
    filename: str,
    row_count: int,
    client_tag: Optional[str] = None,
    column_names: Optional[list] = None,
    column_count: Optional[int] = None,
    created_at: Optional[datetime] = None,
    original_column_names: Optional[list] = None,
    semantic_match_columns: Optional[list] = None,
    min_row_date: Optional[str] = None,
    max_row_date: Optional[str] = None,
) -> dict:
    """
    Build a file document for insertion.

    Stored fields:
    - columnNames: normalized column names used by the rest of the pipeline.
    - columnCount: number of columns.
    - originalColumnNames: raw Excel headers as uploaded.
    - semanticMatchColumns: additional normalized forms (lowercase, no spaces/underscores)
      used by the Semantic Column Resolver for fuzzy matching.
    """
    doc = {
        "fileId": file_id,
        "uploadDate": upload_date,
        "clientTag": client_tag,
        "filename": filename,
        "rowCount": row_count,
        "createdAt": created_at or datetime.utcnow(),
    }
    if column_names is not None:
        doc["columnNames"] = list(column_names)
    if column_count is not None:
        doc["columnCount"] = int(column_count)
    elif column_names is not None:
        doc["columnCount"] = len(column_names)
    if original_column_names is not None:
        doc["originalColumnNames"] = list(original_column_names)
    if semantic_match_columns is not None:
        doc["semanticMatchColumns"] = list(semantic_match_columns)
    if min_row_date is not None:
        doc["minRowDate"] = str(min_row_date)
    if max_row_date is not None:
        doc["maxRowDate"] = str(max_row_date)
    return doc


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
