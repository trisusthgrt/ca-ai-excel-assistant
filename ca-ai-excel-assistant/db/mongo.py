"""
MongoDB connection and helpers (Step 3).
Uses MONGODB_URI from environment; collections: files, data_rows, chat_history.
"""
import os
from typing import Any, List, Optional

from dotenv import load_dotenv

from db.models import chat_doc, file_doc

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGODB_DB_NAME", "ca_ai_excel")

_client = None
_db = None


def _get_client():
    """Lazy connection to MongoDB."""
    global _client
    if _client is None and MONGODB_URI:
        from pymongo import MongoClient
        _client = MongoClient(MONGODB_URI)
    return _client


def get_db():
    """Return database instance. None if MONGODB_URI not set."""
    global _db
    client = _get_client()
    if client is None:
        return None
    if _db is None:
        _db = client[DB_NAME]
    return _db


def _files():
    db = get_db()
    return db["files"] if db is not None else None


def _data_rows():
    db = get_db()
    return db["data_rows"] if db is not None else None


def _chat_history():
    db = get_db()
    return db["chat_history"] if db is not None else None


def insert_file(
    file_id: str,
    upload_date: str,
    filename: str,
    row_count: int,
    client_tag: Optional[str] = None,
) -> str:
    """Insert file metadata. Returns fileId or empty string if not connected."""
    coll = _files()
    if coll is None:
        return ""
    doc = file_doc(file_id, upload_date, filename, row_count, client_tag)
    coll.insert_one(doc)
    return file_id


def insert_rows(rows: List[dict]) -> int:
    """Insert row-level documents. Returns count inserted, or 0 if not connected."""
    coll = _data_rows()
    if coll is None or not rows:
        return 0
    coll.insert_many(rows)
    return len(rows)


def insert_chat(
    question: str,
    answer: str,
    date_context: Optional[str] = None,
    client_tag: Optional[str] = None,
) -> str:
    """Insert chat entry. Returns inserted document id as string, or empty if not connected."""
    coll = _chat_history()
    if coll is None:
        return ""
    doc = chat_doc(question, answer, date_context, client_tag)
    result = coll.insert_one(doc)
    return str(result.inserted_id)


def find_files(
    upload_date: Optional[str] = None,
    client_tag: Optional[str] = None,
) -> List[dict]:
    """Find files by optional uploadDate and clientTag."""
    coll = _files()
    if coll is None:
        return []
    q = {}
    if upload_date is not None:
        q["uploadDate"] = upload_date
    if client_tag is not None:
        q["clientTag"] = client_tag
    return list(coll.find(q))


def find_rows(
    upload_date: Optional[str] = None,
    client_tag: Optional[str] = None,
    row_date_from: Optional[str] = None,
    row_date_to: Optional[str] = None,
    file_id: Optional[str] = None,
    limit: int = 1000,
) -> List[dict]:
    """Find rows by optional filters. row_date_from/to are inclusive (ISO date strings)."""
    coll = _data_rows()
    if coll is None:
        return []
    q = {}
    if upload_date is not None:
        q["uploadDate"] = upload_date
    if client_tag is not None:
        q["clientTag"] = client_tag
    if file_id is not None:
        q["fileId"] = file_id
    if row_date_from is not None or row_date_to is not None:
        q["rowDate"] = {}
        if row_date_from is not None:
            q["rowDate"]["$gte"] = row_date_from
        if row_date_to is not None:
            q["rowDate"]["$lte"] = row_date_to
    cursor = coll.find(q).limit(limit)
    return list(cursor)
