"""
CA AI Excel Assistant — Streamlit entry point (Step 4: Excel upload + store).
"""
import uuid
from io import BytesIO

import streamlit as st

from db import mongo
from db.models import row_doc
from utils.excel_parser import parse_excel
from utils.normalizer import get_rowdate_column_name, normalize

st.set_page_config(page_title="CA AI Excel Assistant", page_icon="📊", layout="wide")
st.title("CA AI Excel Assistant")
st.write("Upload Excel files and ask date-specific questions.")

# ---------------------------------------------------------------------------
# Sidebar: Excel upload, uploadDate (mandatory), clientTag (optional)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Upload Excel")
    upload_date = st.date_input("Upload date (mandatory)", value=None, key="upload_date")
    client_tag = st.text_input("Client tag (optional)", value="", key="client_tag").strip() or None
    uploaded_file = st.file_uploader("Choose Excel file (.xlsx)", type=["xlsx", "xls"], key="excel_upload")

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
                            st.success(f"Saved: {filename} — {inserted} rows (upload date: {upload_date_str}).")
                            if client_tag:
                                st.caption(f"Client tag: {client_tag}")
            except Exception as e:
                st.error(f"Error: {e}")

# ---------------------------------------------------------------------------
# Main: placeholder for chat (Step 8)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Chat")
st.info("Ask date-specific questions here after uploading Excel. (Chat pipeline in Step 8.)")
