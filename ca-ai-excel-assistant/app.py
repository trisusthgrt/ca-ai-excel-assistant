"""
CA AI Excel Assistant — Streamlit entry point (Step 10: UI polish).
"""
import logging
import uuid
from io import BytesIO

import streamlit as st

# Ensure orchestrator logs (original query, normalized query, corrections) are visible
logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")

from agents.orchestrator import run as orchestrator_run
from db import mongo
from db.models import row_doc
from utils.excel_parser import parse_excel
from utils.normalizer import get_rowdate_column_name, normalize
from vector import chroma_client

st.set_page_config(page_title="CA AI Excel Assistant", page_icon="📊", layout="wide", initial_sidebar_state="expanded")
st.title("CA AI Excel Assistant")
st.caption("Upload Excel, then ask date-specific questions. Answers use your uploaded data only.")

# ---------------------------------------------------------------------------
# Sidebar (Step 10): Upload, context date, client tag, instructions
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Upload Excel")
    upload_date = st.date_input("Upload date (mandatory)", value=None, key="upload_date", help="Date to associate with this file (e.g. report date).")
    client_tag = st.text_input("Client tag (optional)", value="", key="client_tag", placeholder="e.g. ABC Corp").strip() or None
    uploaded_file = st.file_uploader("Choose Excel file (.xlsx)", type=["xlsx", "xls"], key="excel_upload", help="Supported: .xlsx, .xls. Dates and amounts are normalized.")

def _serialize_value(v):
    """Convert NaN/NaT/pd types to JSON-serializable for MongoDB."""
    if v is None:
        return None
    if str(v) == "NaT":
        return None
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    if hasattr(v, "isoformat") and str(v) != "NaT":
        return v.isoformat()[:10] if hasattr(v, "strftime") else str(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)

def _dataframe_row_to_dict(row, columns):
    """One row of DataFrame as dict; NaN -> None."""
    return {c: _serialize_value(row[c]) for c in columns}


def _render_chart(chart_type: str, chart_data: dict) -> bool:
    """Step 9: render Plotly line or bar from chart_data (x, y, labels, optional title). Returns True if rendered."""
    if not chart_data or not chart_type or chart_type not in ("line", "bar"):
        return False
    x = chart_data.get("x") or []
    y = chart_data.get("y") or []
    if not x or not y or len(x) != len(y):
        return False
    labels = chart_data.get("labels") or ["x", "y"]
    title = chart_data.get("title") or ""
    try:
        import plotly.express as px
        if chart_type == "line":
            fig = px.line(x=x, y=y, labels={"x": labels[0] if len(labels) > 0 else "date", "y": labels[1] if len(labels) > 1 else "value"})
        else:
            fig = px.bar(x=x, y=y, labels={"x": labels[0] if len(labels) > 0 else "category", "y": labels[1] if len(labels) > 1 else "amount"})
        if title:
            fig.update_layout(title=title)
        st.plotly_chart(fig, use_container_width=True)
        return True
    except Exception:
        return False

if uploaded_file is not None and upload_date is not None:
    with st.sidebar:
        if st.button("Parse and save to database"):
            try:
                raw = uploaded_file.read()
                df = parse_excel(BytesIO(raw))
                if df is None or df.empty:
                    st.error("No data in the Excel file.")
                else:
                    norm_df, col_names = normalize(df)
                    if norm_df.empty:
                        st.error("Normalization produced no rows.")
                    else:
                        file_id = str(uuid.uuid4())
                        upload_date_str = upload_date.strftime("%Y-%m-%d")
                        filename = uploaded_file.name or "upload.xlsx"
                        row_count = len(norm_df)
                        rowdate_col = get_rowdate_column_name(col_names)

                        # Insert file metadata
                        fid = mongo.insert_file(file_id, upload_date_str, filename, row_count, client_tag)
                        if fid == "" and mongo.get_db() is None:
                            st.warning("MongoDB not connected. Set MONGODB_URI in .env to persist data.")
                        elif fid != "":
                            rows = []
                            for _, r in norm_df.iterrows():
                                row_dict = _dataframe_row_to_dict(r, col_names)
                                row_date_val = None
                                if rowdate_col and row_dict.get(rowdate_col):
                                    row_date_val = row_dict.pop(rowdate_col, None)
                                doc = row_doc(file_id, upload_date_str, row_dict, client_tag, row_date_val)
                                rows.append(doc)
                            inserted = mongo.insert_rows(rows)
                            # Step 5: embed and store in ChromaDB (one text per row, metadata: uploadDate, rowDate, clientTag, fileId)
                            texts = []
                            metadatas = []
                            ids = []
                            for i, doc in enumerate(rows):
                                parts = [f"{k}: {v}" for k, v in doc.items() if v is not None]
                                texts.append(" ".join(parts))
                                meta = {
                                    "uploadDate": upload_date_str,
                                    "clientTag": client_tag or "",
                                    "fileId": file_id,
                                }
                                if doc.get("rowDate"):
                                    meta["rowDate"] = doc["rowDate"]
                                metadatas.append(meta)
                                ids.append(f"{file_id}_{i}")
                            try:
                                chroma_client.add_documents(texts, metadatas, ids)
                            except Exception as emb_err:
                                st.warning(f"Saved to MongoDB. Embeddings skipped: {emb_err}")
                            else:
                                st.success(f"Saved: {filename} — {inserted} rows (upload date: {upload_date_str}). Embeddings stored.")
                            if client_tag:
                                st.caption(f"Client tag: {client_tag}")
            except Exception as e:
                st.error(f"Upload failed: {e}")

with st.sidebar:
    st.divider()
    st.subheader("Context")
    st.caption("Client tag filters chat answers when you mention a client.")
    st.divider()
    with st.expander("How to use", expanded=False):
        st.markdown("""
1. **Upload** an Excel file with a date and optional client tag.
2. **Ask** in natural language, e.g.:
   - *GST on 12 Jan 2025*
   - *Expenses for client ABC on 10 Jan*
   - *Show GST trend for January*
   - *Compare 10 Jan vs 11 Jan*
3. Answers and charts use only your uploaded data. No tax/legal advice.
        """)
    if mongo.get_db() is None:
        st.warning("MongoDB not connected. Set **MONGODB_URI** in `.env` to save data and chat history.")

# ---------------------------------------------------------------------------
# Main area (Step 10): Chat, empty state, error handling
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Chat")
if "messages" not in st.session_state:
    st.session_state.messages = []

if not st.session_state.messages:
    st.info("Upload an Excel file (sidebar) to get started, then ask date-specific questions below. Example: *GST on 12 Jan 2025* or *Show trend for January*.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        # Show query corrections (original → normalized) for assistant messages when any correction was applied
        correction_map = msg.get("correction_map") or {}
        if msg["role"] == "assistant" and correction_map:
            with st.expander("Query corrected (log)", expanded=False):
                st.caption("**Original:** " + (msg.get("original_query") or ""))
                st.caption("**Normalized:** " + (msg.get("normalized_query") or ""))
                st.caption("**Corrections applied:** " + ", ".join(f"'{k}' → '{v}'" for k, v in correction_map.items()))
        st.write(msg["content"])
        _render_chart(msg.get("chart_type"), msg.get("chart_data") or {})

prompt = st.chat_input("Ask a question (e.g. GST on 12 Jan 2025, expenses for client ABC)...")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt, "chart_data": None})
    try:
        with st.spinner("Thinking..."):
            result = orchestrator_run(prompt)
        answer = result.get("answer", "")
        chart_type = result.get("chart_type")
        chart_data = result.get("chart_data")
        original_query = result.get("original_query", "")
        normalized_query = result.get("normalized_query", "")
        correction_map = result.get("correction_map") or {}
        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "chart_type": chart_type,
            "chart_data": chart_data,
            "original_query": original_query,
            "normalized_query": normalized_query,
            "correction_map": correction_map,
        })
        if mongo.get_db() is not None:
            mongo.insert_chat(prompt, answer, date_context=None, client_tag=client_tag)
    except Exception as e:
        err_msg = str(e)
        if "GROQ" in err_msg.upper() or "api" in err_msg.lower() or "key" in err_msg.lower():
            fallback = "Check GROQ_API_KEY in .env and try again."
        elif "mongo" in err_msg.lower() or "pymongo" in err_msg.lower():
            fallback = "Check MONGODB_URI in .env. You can still ask; answers won’t be saved."
        else:
            fallback = "Something went wrong. Check your data and try again."
        st.session_state.messages.append({"role": "assistant", "content": f"Error: {fallback}", "chart_type": None, "chart_data": None})
    st.rerun()
