#!/usr/bin/env python3
"""
Step 3 verification: insert one document into files, data_rows, chat_history and fetch them.
Requires MONGODB_URI in .env or environment.
Run from ca-ai-excel-assistant:  python verify_mongo.py
"""
import os
import sys
from datetime import datetime

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

def main():
    if not os.getenv("MONGODB_URI"):
        print("MONGODB_URI not set. Copy .env.example to .env and set your Atlas connection string.")
        return 1

    from db import mongo
    from db.models import file_doc, chat_doc

    db = mongo.get_db()
    if db is None:
        print("Could not connect to MongoDB. Check MONGODB_URI.")
        return 1

    print("Connected to MongoDB.")
    file_id = "verify-step3-file"
    upload_date = "2025-01-31"
    client_tag = "verify"

    # Insert one file
    doc_file = file_doc(file_id, upload_date, "verify.xlsx", 1, client_tag)
    db["files"].insert_one(doc_file)
    print("  Inserted 1 document into files")

    # Insert one row
    doc_row = {
        "fileId": file_id,
        "uploadDate": upload_date,
        "clientTag": client_tag,
        "rowDate": upload_date,
        "amount": 100.0,
        "description": "Step 3 verify",
    }
    db["data_rows"].insert_one(doc_row)
    print("  Inserted 1 document into data_rows")

    # Insert one chat
    doc_chat = chat_doc("Test question?", "Test answer.", upload_date, client_tag)
    db["chat_history"].insert_one(doc_chat)
    print("  Inserted 1 document into chat_history")

    # Fetch and verify
    files = list(db["files"].find({"fileId": file_id}))
    rows = list(db["data_rows"].find({"fileId": file_id}))
    chats = list(db["chat_history"].find({"clientTag": client_tag}).limit(1))

    if files and rows and chats:
        print("\nFetched: 1 file, 1 row, 1 chat. Step 3 verification passed.")
        # Optional: delete test docs so they don't clutter Atlas
        db["files"].delete_one({"fileId": file_id})
        db["data_rows"].delete_one({"fileId": file_id})
        db["chat_history"].delete_one({"_id": chats[0]["_id"]})
        print("Test documents removed.")
        return 0
    print("\nFetch failed. Check collections in Atlas.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
