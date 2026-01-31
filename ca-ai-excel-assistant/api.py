"""
CA AI Excel Assistant — FastAPI app.
"""
from fastapi import FastAPI

app = FastAPI(title="CA AI Excel Assistant API", version="0.1.0")


@app.get("/")
def root():
    return {"status": "ok", "message": "CA AI Excel Assistant API"}


@app.get("/returns")
def returns():
    """Placeholder: tax/report returns. Will return stored data once DB is connected (Step 3+)."""
    return {"returns": [], "message": "No returns data yet. Upload Excel files via the Streamlit app."}
