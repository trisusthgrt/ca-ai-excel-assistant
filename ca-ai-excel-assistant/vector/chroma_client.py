"""
ChromaDB client for semantic embeddings (Step 5).
Persistent storage; metadata: uploadDate, rowDate, clientTag, fileId.
Uses ChromaDB default embedding function (OnnxRuntime / all-MiniLM-L6-v2).
"""
import os
from typing import List, Optional

import chromadb
from chromadb.config import Settings

# Persist under project directory; safe for Streamlit Cloud (ephemeral) or local
PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", os.path.join(os.path.dirname(__file__), "..", "chroma_db"))
COLLECTION_NAME = "ca_excel_rows"

_client = None
_collection = None


def _get_client():
    global _client
    if _client is None:
        os.makedirs(PERSIST_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(path=PERSIST_DIR, settings=Settings(anonymized_telemetry=False))
    return _client


def _get_collection():
    global _collection
    if _collection is None:
        client = _get_client()
        # Default embedding function (OnnxRuntime all-MiniLM-L6-v2) — no sentence-transformers required
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"description": "CA Excel row embeddings"},
        )
    return _collection


def add_documents(
    texts: List[str],
    metadatas: List[dict],
    ids: Optional[List[str]] = None,
) -> None:
    """
    Add documents to ChromaDB. One text per row; metadata must have uploadDate, rowDate (optional), clientTag, fileId.
    ChromaDB metadata values must be str, int, float, or bool.
    """
    if not texts or not metadatas or len(texts) != len(metadatas):
        return
    coll = _get_collection()
    # Normalize metadata: only scalar types
    clean_metadatas = []
    for m in metadatas:
        clean = {}
        for k, v in m.items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                clean[k] = v
            else:
                clean[k] = str(v)
        clean_metadatas.append(clean)
    if ids is None:
        ids = [f"row_{i}" for i in range(len(texts))]
    coll.add(ids=ids, documents=texts, metadatas=clean_metadatas)


def query(
    text: str,
    n_results: int = 10,
    where: Optional[dict] = None,
) -> List[dict]:
    """
    Semantic search. Returns list of dicts with 'id' and 'metadata' (and optionally 'document').
    where: metadata filter, e.g. {"uploadDate": "2025-01-31"}, {"clientTag": "ABC"}.
    """
    coll = _get_collection()
    kwargs = {"query_texts": [text], "n_results": n_results}
    if where is not None and where:
        kwargs["where"] = where
    result = coll.query(**kwargs)
    out = []
    ids = result.get("ids", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    documents = result.get("documents", [[]])[0]
    for i, doc_id in enumerate(ids):
        item = {"id": doc_id, "metadata": metadatas[i] if i < len(metadatas) else {}}
        if i < len(documents):
            item["document"] = documents[i]
        out.append(item)
    return out
