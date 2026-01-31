"""
Data models for MongoDB documents.
Step 1 skeleton — structure only, no DB usage yet.
"""

# File metadata (collection: files)
FILE_FIELDS = ["fileId", "uploadDate", "clientTag", "filename", "rowCount", "createdAt"]

# Row-level data (collection: data_rows)
ROW_FIELDS = ["fileId", "uploadDate", "clientTag", "rowDate"]  # + dynamic Excel columns

# Chat history (collection: chat_history)
CHAT_FIELDS = ["question", "answer", "dateContext", "clientTag", "createdAt"]
