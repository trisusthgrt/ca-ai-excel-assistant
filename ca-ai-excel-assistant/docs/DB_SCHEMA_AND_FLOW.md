# DB schemas, connection, and how data is stored on Excel upload

## 1. DB connection

| Setting | Source | Purpose |
|--------|--------|--------|
| **MONGODB_URI** | `.env` or Streamlit Secrets | Atlas connection string (required to persist). |
| **MONGODB_DB_NAME** | `.env` (default: `ca_ai_excel`) | Database name. |

- Connection is **lazy**: first use of `get_db()` creates the client and DB.
- If `MONGODB_URI` is not set, `get_db()` returns `None` and all insert/find helpers return empty/zero (no crash).
- Collections used: **files**, **data_rows**, **chat_history**.

---

## 2. Collections and schemas

### 2.1 `files` (one document per uploaded file)

Stores **file-level** metadata only (no row data).

| Field | Type | Meaning |
|-------|------|--------|
| **fileId** | string (UUID) | Unique id for this upload. |
| **uploadDate** | string (YYYY-MM-DD) | **Upload date** chosen by the user in the sidebar (e.g. “report as of” date). |
| **clientTag** | string or null | **Company/client** from the sidebar (optional). Same for all rows of this file. |
| **filename** | string | Original Excel file name. |
| **rowCount** | int | Number of rows in this file. |
| **columnCount** | int | Number of columns (for schema queries). |
| **columnNames** | array of strings | List of column names (for “what attributes” / schema). |
| **createdAt** | datetime | When the document was inserted. |

**Upload date** here = “when did the user say this file applies to?” (one value per file).

---

### 2.2 `data_rows` (one document per Excel row)

Stores **each row** of the Excel, with both **file-level** and **row-level** fields.

| Field | Type | Meaning |
|-------|------|--------|
| **fileId** | string | Links to the file document (same for all rows of that file). |
| **uploadDate** | string (YYYY-MM-DD) | Same as in `files`: **upload date** from the sidebar (one per file). |
| **clientTag** | string or null | Same as in `files`: **company/client** from the sidebar (one per file). |
| **rowDate** | string (YYYY-MM-DD) or null | **Date from the Excel row** (e.g. transaction date, invoice date). **Different per row.** |
| **+ all other columns** | (dynamic) | Rest of the normalized Excel columns (e.g. amount, gst, category, description). |

- **uploadDate** = user’s “upload date” (sidebar) → same for every row of that file.
- **rowDate** = date **inside** the dataset for that row (e.g. per company/transaction) → can be different for each row.
- **clientTag** = company/client from the sidebar → same for every row of that file.

So for each company (client) you can have:
- One **uploadDate** per file (when the file was “reported”).
- One **rowDate** per row (the date in the Excel for that row).

---

### 2.3 `chat_history` (one document per Q&A)

| Field | Type | Meaning |
|-------|------|--------|
| **question** | string | User’s question. |
| **answer** | string | Assistant’s answer. |
| **dateContext** | string or null | Optional date context. |
| **clientTag** | string or null | Optional client from session. |
| **createdAt** | datetime | When the chat was stored. |

---

## 3. How data is stored when an Excel file is uploaded

Step-by-step flow (from `app.py` + `db/models` + `utils/normalizer`).

### 3.1 User input (sidebar)

- **Upload date** (required): e.g. `2025-02-02` → becomes **uploadDate** everywhere for this file.
- **Client tag** (optional): e.g. `ABC Pvt Ltd` → becomes **clientTag** everywhere for this file.
- **File**: one `.xlsx` (e.g. has columns: Date, Company, Amount, GST).

### 3.2 Parse and normalize

1. **Parse** Excel → DataFrame (all sheets combined).
2. **Normalize** (`utils/normalizer.py`):
   - Column names: lowercase, aliases (e.g. `date` → `rowdate`, `transaction date` → `rowdate`).
   - Date-like columns → ISO **YYYY-MM-DD**.
   - Amount-like columns → float.
3. **Row-date column** is detected: first column whose name is “date”, “rowdate”, “transaction date”, etc. (see `get_rowdate_column_name()`).

### 3.3 Build file document

- **fileId** = new UUID.
- **uploadDate** = user’s chosen upload date (e.g. `2025-02-02`).
- **clientTag** = user’s client tag (e.g. `ABC Pvt Ltd`) or null.
- **filename**, **rowCount**, **columnCount**, **columnNames** from the normalized DataFrame.
- One document is inserted into **files**.

### 3.4 Build row documents (one per Excel row)

For **each row** of the normalized DataFrame:

1. **row_dict** = all columns as a dict (e.g. `rowdate`, `amount`, `gst`, `category`).
2. **row_date_val**:
   - If the row-date column exists and has a value in this row → that value (already ISO YYYY-MM-DD) is taken.
   - That column is **removed** from `row_dict` so it is not stored twice.
3. **doc** = `row_doc(file_id, upload_date_str, row_dict, client_tag, row_date_val)`:
   - **fileId** = same for all rows of this file.
   - **uploadDate** = same for all rows (sidebar upload date).
   - **clientTag** = same for all rows (sidebar client/company).
   - **rowDate** = **that row’s date from the Excel** (e.g. transaction date); can be different per row.
   - Remaining keys = rest of the row (amount, gst, category, etc.).

So:

- **uploadDate** = “when this file was uploaded / reported” (one per file).
- **rowDate** = “the date in the Excel for this row” (per row; e.g. per company/transaction).
- **clientTag** = “which company this file is for” (one per file).

### 3.5 Insert and ChromaDB

- **insert_rows(rows)** inserts all row documents into **data_rows**.
- Optionally, each row is also sent to ChromaDB for vector search (with metadata: uploadDate, rowDate, clientTag, fileId).

---

## 4. How the two dates are used

The app supports **two kinds of date filters**:

| Concept | Stored in | Set from | Used for |
|--------|-----------|----------|----------|
| **Upload date** | `files.uploadDate`, `data_rows.uploadDate` | Sidebar “Upload date” | Queries like **“data uploaded on 2 Feb”**, **“upload date 2 Feb 2025”**, **“file uploaded on X”**. Planner sets `date_filter_type: "upload_date"`; DataAgent filters by **uploadDate** in MongoDB. |
| **Row date (data date)** | `data_rows.rowDate` only | Excel column (date/rowdate/transaction date) | Queries like **“GST on 12 Jan”**, **“data on 12 Jan”**, **“trend for January”**. Planner sets `date_filter_type: "row_date"` (default); DataAgent filters by **rowDate** (row_date_from/row_date_to). |

So:

- **By upload date** → “Show data for files **uploaded on** 2 Feb 2025” → filter on **uploadDate**.
- **By data date** → “Show GST **on** 12 Jan 2025” / “trend **for** January” → filter on **rowDate** (date in the Excel for each row).
- **Company (client)** = **clientTag** from the sidebar; filter “data for client X” uses **clientTag** (with either date type).

---

## 5. Example

- **Sidebar:** Upload date = `2025-02-02`, Client tag = `ABC Pvt Ltd`.
- **Excel:**  
  Date         | Amount | GST  
  ------------|--------|-----  
  2025-01-10  | 1000   | 180  
  2025-01-11  | 2000   | 360  

**Stored:**

**files** (1 doc):

- fileId, uploadDate=`2025-02-02`, clientTag=`ABC Pvt Ltd`, filename, rowCount=2, columnNames=[...], etc.

**data_rows** (2 docs):

- Row 1: fileId, uploadDate=`2025-02-02`, clientTag=`ABC Pvt Ltd`, **rowDate=`2025-01-10`**, amount=1000, gst=180.
- Row 2: fileId, uploadDate=`2025-02-02`, clientTag=`ABC Pvt Ltd`, **rowDate=`2025-01-11`**, amount=2000, gst=360.

Query “GST for ABC Pvt Ltd on 10 Jan 2025” uses **clientTag** = `ABC Pvt Ltd` and **rowDate** = `2025-01-10` (from planner date filter → row_date_from/row_date_to in `find_rows`).

---

## 6. Quick reference

| You want… | Field / collection | Note |
|-----------|--------------------|------|
| “When was this file uploaded/reported?” | **uploadDate** (files + data_rows) | From sidebar. |
| “What date is this row about?” | **rowDate** (data_rows only) | From Excel date column. |
| “Which company/client?” | **clientTag** (files + data_rows) | From sidebar; same for whole file. |
| “How many rows/columns?” | **files.rowCount**, **files.columnCount** | Schema queries. |
| “List of columns?” | **files.columnNames** | Schema queries. |

All DB access goes through `db/mongo.py`; document shapes are defined in `db/models.py`.
