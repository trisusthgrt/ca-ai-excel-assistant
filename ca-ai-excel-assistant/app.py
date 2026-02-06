"""
CA AI Excel Assistant — Streamlit entry point (Step 10: UI polish).
"""
import logging
import uuid
from io import BytesIO

import streamlit as st

# Ensure orchestrator logs (original query, normalized query, corrections) are visible
logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

from agents.orchestrator import run as orchestrator_run
from db import mongo
from db.models import row_doc
from utils.excel_parser import parse_excel
from utils.normalizer import get_rowdate_column_name, normalize
from utils.semantic_column_resolver import _normalize_for_match
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


def _plotly_default_layout(fig, title: str = "", is_pie: bool = False):
    """Apply hover, zoom, and legend so all charts support them."""
    fig.update_layout(
        title=title or None,
        hovermode="closest",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=50, b=50, l=50, r=50),
    )
    if not is_pie:
        fig.update_xaxes(rangeslider_visible=False, fixedrange=False)
        fig.update_yaxes(fixedrange=False)
    return fig


def _render_chart(chart_type: str, chart_data: dict) -> bool:
    """
    Render Plotly chart: line → trends, bar → comparisons, pie → distribution, stacked_bar → breakdown.
    Supports hover, zoom, legends. Returns True if rendered.
    """
    if not chart_data or not chart_type or chart_type not in ("line", "bar", "pie", "stacked_bar"):
        return False
    x = chart_data.get("x") or []
    y = chart_data.get("y") or []
    if not x or not y or len(x) != len(y):
        return False
    labels = chart_data.get("labels") or ["x", "y"]
    x_name = labels[0] if len(labels) > 0 else "x"
    y_name = labels[1] if len(labels) > 1 else "y"
    title = chart_data.get("title") or ""
    try:
        import plotly.express as px

        if chart_type == "line":
            fig = px.line(x=x, y=y, labels={"x": x_name, "y": y_name}, title=title)
        elif chart_type == "bar":
            fig = px.bar(x=x, y=y, labels={"x": x_name, "y": y_name}, title=title)
        elif chart_type == "pie":
            fig = px.pie(values=y, names=x, title=title)
            fig.update_traces(textposition="inside", textinfo="percent+label")
        elif chart_type == "stacked_bar":
            fig = px.bar(x=x, y=y, labels={"x": x_name, "y": y_name}, title=title)
            fig.update_layout(barmode="stack")
        else:
            return False

        fig = _plotly_default_layout(fig, title, is_pie=(chart_type == "pie"))
        st.plotly_chart(
            fig,
            use_container_width=True,
            config=dict(
                displayModeBar=True,
                displaylogo=False,
                modeBarButtonsToInclude=["zoom2d", "pan2d", "select2d", "lasso2d", "zoomIn2d", "zoomOut2d", "autoScale2d", "resetScale2d"],
            ),
        )
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
                    # Preserve original column names as they appear in Excel
                    original_columns = list(df.columns)
                    norm_df, col_names = normalize(df)
                    if norm_df.empty:
                        st.error("Normalization produced no rows.")
                    else:
                        file_id = str(uuid.uuid4())
                        upload_date_str = upload_date.strftime("%Y-%m-%d")
                        filename = uploaded_file.name or "upload.xlsx"
                        row_count = len(norm_df)
                        column_count = len(col_names)
                        column_names = list(col_names)
                        # Additional normalized forms for semantic matching (lowercase, no spaces/underscores)
                        semantic_match_columns = [_normalize_for_match(c) for c in column_names]
                        rowdate_col = get_rowdate_column_name(col_names)
                        
                        # Compute min/max dates for logging and metadata
                        min_row_date = None
                        max_row_date = None
                        if rowdate_col and rowdate_col in norm_df.columns:
                            date_series = norm_df[rowdate_col].dropna()
                            if not date_series.empty:
                                valid_dates = [d for d in date_series if d is not None and str(d).strip()]
                                if valid_dates:
                                    min_row_date = min(valid_dates)
                                    max_row_date = max(valid_dates)
                                    logger.info("upload_date_range: file_id=%s min_row_date=%s max_row_date=%s", 
                                                file_id, min_row_date, max_row_date)

                        # Insert file metadata (column_names, column_count for schema_query)
                        fid = mongo.insert_file(
                            file_id,
                            upload_date_str,
                            filename,
                            row_count,
                            client_tag,
                            column_names=column_names,
                            column_count=column_count,
                            original_column_names=original_columns,
                            semantic_match_columns=semantic_match_columns,
                            min_row_date=min_row_date,
                            max_row_date=max_row_date,
                        )
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
        needs_chart = msg.get("needs_chart", False)
        chart_type = msg.get("chart_type")
        chart_data = msg.get("chart_data") or {}
        chart_fallback_table = msg.get("chart_fallback_table") or False
        show_data_table = msg.get("show_data_table") or False
        table_data = msg.get("table_data")
        # Render chart ONLY if needs_chart and validation passed; otherwise show dataframe
        if needs_chart and chart_type and chart_data and not chart_fallback_table:
            _render_chart(chart_type, chart_data)
        if show_data_table and table_data:
            st.caption("**Sample of uploaded data** (first 200 rows)")
            st.dataframe(table_data, use_container_width=True)
        elif chart_fallback_table and table_data:
            st.caption(msg.get("chart_fallback_message") or "Not enough data to generate chart, showing table instead.")
            st.dataframe(table_data, use_container_width=True)
        elif table_data and not show_data_table:
            st.dataframe(table_data, use_container_width=True)

prompt = st.chat_input("Ask a question (e.g. GST on 12 Jan 2025, expenses for client ABC)...")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt, "chart_data": None})
    # Clarification state: only one clarification per query; if user confirms (yes/same query), use defaults
    clarification_context = None
    if st.session_state.messages:
        last_msg = st.session_state.messages[-2] if len(st.session_state.messages) >= 2 else None  # previous assistant
        if last_msg and last_msg.get("role") == "assistant" and last_msg.get("is_clarification"):
            last_norm = (last_msg.get("normalized_query") or "").strip().lower()
            prompt_lower = prompt.strip().lower()
            if last_norm and (prompt_lower == last_norm or prompt_lower in ("yes", "ok", "y")):
                clarification_context = {"normalized_query": last_msg.get("normalized_query", ""), "confirmed": True}
    try:
        with st.spinner("Thinking..."):
            result = orchestrator_run(prompt, clarification_context=clarification_context)
        answer = result.get("answer", "")
        needs_chart = result.get("needs_chart", False)
        chart_type = result.get("chart_type")
        chart_data = result.get("chart_data")
        chart_fallback_table = result.get("chart_fallback_table") or False
        chart_fallback_message = result.get("chart_fallback_message") or ""
        show_data_table = result.get("show_data_table") or False
        table_data = result.get("table_data")
        original_query = result.get("original_query", "")
        normalized_query = result.get("normalized_query", "")
        correction_map = result.get("correction_map") or {}
        is_clarification = result.get("is_clarification", False)
        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "needs_chart": needs_chart,
            "chart_type": chart_type,
            "chart_data": chart_data,
            "chart_fallback_table": chart_fallback_table,
            "chart_fallback_message": chart_fallback_message,
            "show_data_table": show_data_table,
            "table_data": table_data,
            "original_query": original_query,
            "normalized_query": normalized_query,
            "correction_map": correction_map,
            "is_clarification": is_clarification,
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
