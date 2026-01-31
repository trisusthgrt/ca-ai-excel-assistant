# CA AI Excel Assistant — Step-by-Step Implementation Guide

This guide walks you through building the full project in order. Complete each step and verify acceptance before moving on.

---

## Prerequisites (Before You Start)

- [ ] **Python 3.10+** installed
- [ ] **Git** installed (for deployment)
- [ ] **MongoDB Atlas** account (free M0 cluster)
- [ ] **Groq** account and API key (free tier)
- [ ] **GitHub** account (for Streamlit Cloud deployment)
- [ ] Code editor (e.g. Cursor / VS Code)

---

## STEP 1 — Project Skeleton

**Goal:** Create the folder structure and empty modules so the app runs without AI logic.

### 1.1 Create directory structure

```
ca-ai-excel-assistant/
├── app.py
├── api.py
├── agents/
│   ├── __init__.py
│   ├── planner.py
│   ├── data_agent.py
│   ├── analyst.py
│   ├── responder.py
│   └── orchestrator.py
├── db/
│   ├── __init__.py
│   ├── mongo.py
│   └── models.py
├── vector/
│   ├── __init__.py
│   └── chroma_client.py
├── utils/
│   ├── __init__.py
│   ├── excel_parser.py
│   ├── normalizer.py
│   └── policy_guard.py
├── requirements.txt
└── README.md
```

### 1.2 Add minimal runnable code

- **app.py:** `import streamlit as st` and `st.title("CA AI Excel Assistant")` plus `st.write("Welcome.")` so `streamlit run app.py` works.
- **api.py:** Create FastAPI app with a root route returning `{"status": "ok"}` so `uvicorn api:app` runs.
- **agents/** — Each file: define an empty function or placeholder (e.g. `def plan(query): return {}`).
- **db/mongo.py** — Empty client or `None` placeholder; **db/models.py** — Pydantic or dict models for file, row, chat.
- **vector/chroma_client.py** — Placeholder function.
- **utils/** — Placeholder functions for `parse_excel`, `normalize`, `check_policy`.

### 1.3 Verification

- [ ] `streamlit run app.py` runs and shows the title
- [ ] `uvicorn api:app --reload` runs and `GET /` returns `{"status": "ok"}`
- [ ] No import errors when loading any module

---

## STEP 2 — Environment & Dependencies

**Goal:** Lock the tech stack and ensure `pip install` works.

### 2.1 Create requirements.txt

Add (with or without version pins):

```
fastapi
uvicorn
streamlit
pandas
python-multipart
pymongo
chromadb
autogen
plotly
groq
```

Optional for embeddings: `sentence-transformers` if you use local embeddings; otherwise you may use Groq or ChromaDB defaults.

### 2.2 Create virtual environment and install

```bash
cd ca-ai-excel-assistant
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

### 2.3 Verification

- [ ] `pip install -r requirements.txt` completes without errors
- [ ] `python -c "import streamlit, fastapi, pandas, pymongo, chromadb, autogen, plotly, groq"` runs without errors

---

## STEP 3 — MongoDB Connection

**Goal:** Connect to MongoDB Atlas (M0) and use three collections: `files`, `data_rows`, `chat_history`.

### 3.1 MongoDB Atlas setup

1. Create a free M0 cluster at [cloud.mongodb.com](https://cloud.mongodb.com).
2. Create a database user (username + password).
3. Add your IP to Network Access (or `0.0.0.0/0` for Cloud deployment).
4. Get connection string: `mongodb+srv://<user>:<password>@<cluster>.mongodb.net/<dbname>?retryWrites=true&w=majority`.

### 3.2 Implement db layer

- **db/models.py:** Define structures for:
  - File document: `fileId`, `uploadDate`, `clientTag`, `filename`, `rowCount`, `createdAt`.
  - Row document: `fileId`, `uploadDate`, `clientTag`, `rowDate` (optional), plus dynamic fields from Excel.
  - Chat document: `question`, `answer`, `dateContext`, `clientTag`, `createdAt`.
- **db/mongo.py:**
  - Read connection string from environment variable (e.g. `MONGODB_URI`).
  - Connect to the database and get collections: `files`, `data_rows`, `chat_history`.
  - Implement: `insert_file()`, `insert_rows()`, `insert_chat()`, `find_files()`, `find_rows()` with filter params (e.g. `uploadDate`, `clientTag`, `rowDate` range).

### 3.3 Verification

- [ ] Set `MONGODB_URI` in `.env` or environment
- [ ] Run a test script: insert one document into `files`, one into `data_rows`, one into `chat_history`, then fetch them
- [ ] No connection errors; data appears in Atlas UI

---

## STEP 4 — Excel Upload & Normalization

**Goal:** Upload Excel via Streamlit, collect `uploadDate` (required) and `clientTag` (optional), parse with pandas, normalize, and store in MongoDB.

### 4.1 Excel parser (utils/excel_parser.py)

- Accept file path or file-like object.
- Use `pandas.read_excel()` (support `.xlsx`; optionally `.xls`).
- Return a DataFrame; optionally return per-sheet DataFrames or a combined one.
- Handle missing columns gracefully.

### 4.2 Normalizer (utils/normalizer.py)

- **Column names:** Lowercase, strip spaces, replace special characters; optionally map known aliases (e.g. "GST", "Amount", "Date").
- **Dates:** Detect date-like columns; convert to ISO format (YYYY-MM-DD).
- **Amounts:** Detect numeric/currency columns; strip symbols and commas; convert to float.
- Return normalized DataFrame and list of detected field names.

### 4.3 Wire into app and DB

- In **app.py** (or via **api.py**):  
  - File upload widget.  
  - Date input for `uploadDate` (mandatory).  
  - Text input for `clientTag` (optional).  
- On submit:  
  1. Parse Excel with `excel_parser`.  
  2. Normalize with `normalizer`.  
  3. Generate `fileId` (e.g. UUID).  
  4. Call `db.mongo.insert_file()` with metadata.  
  5. Call `db.mongo.insert_rows()` with each row plus `fileId`, `uploadDate`, `clientTag`; set `rowDate` from a date column if present.

### 4.4 Verification

- [ ] Upload a sample Excel with date and amount columns
- [ ] Data appears in `files` and `data_rows` with correct `uploadDate`, `clientTag`, normalized dates and numbers
- [ ] No user IDs in the data model; `rowDate` populated when column exists

---

## STEP 5 — Vector Embeddings (ChromaDB)

**Goal:** Create embeddings (Groq or ChromaDB default), store in ChromaDB with metadata, persist locally, support semantic search and metadata filters.

### 5.1 ChromaDB client (vector/chroma_client.py)

- Create or load a persistent ChromaDB collection (e.g. `persist_directory="./chroma_db"`).
- **Metadata to store:** `uploadDate`, `rowDate`, `clientTag`, `fileId` (and optionally row id).
- Implement:
  - `add_documents(texts, metadatas, ids)` — e.g. one text per row (e.g. JSON or concatenated key fields).
  - `query(embedding_or_text, n_results, where=metadata_filter)` — return ids and metadatas.
- If using Groq for embeddings: call Groq API to get embeddings for each text, then add to ChromaDB with `add(ids, embeddings, metadatas)`. If using ChromaDB’s default, use `add(texts, metadatas, ids)`.

### 5.2 When to embed

- After successful Excel upload and MongoDB insert: for each normalized row, build a text representation (e.g. key columns as string), then call `chroma_client.add_documents()` with metadata (`uploadDate`, `rowDate`, `clientTag`, `fileId`).

### 5.3 Verification

- [ ] Upload an Excel, then run a semantic query (e.g. "GST amount") and get relevant rows
- [ ] Filter by `uploadDate` and `clientTag` in ChromaDB query; results match MongoDB filters
- [ ] ChromaDB data persists after restart (use same `persist_directory`)

---

## STEP 6 — AutoGen Agents (Core Logic)

**Goal:** Implement a fixed pipeline: Planner → Data → Analyst → Response. No free conversation; deterministic flow.

### 6.1 Planner (agents/planner.py)

- Input: user query (string).
- Use LLM (Groq) to extract:
  - **Intent:** e.g. "gst_summary", "expense_breakdown", "trend", "compare_dates".
  - **Dates:** list of dates or date range (e.g. 12 Jan 2025, or January 2025).
  - **clientTag:** if mentioned.
  - **Risk flag:** whether the query suggests evasion or inappropriate request.
- Output: structured dict (e.g. Pydantic model or dict) passed to policy guard and Data agent.

### 6.2 Data agent (agents/data_agent.py)

- Input: planner output (intent, dates, clientTag).
- Query **MongoDB** with filters: `uploadDate`, `clientTag`, `rowDate` range as needed.
- Query **ChromaDB** with same metadata filters plus semantic query (e.g. rewritten from intent).
- Output: combined list of relevant documents/rows (and optionally snippets) for the Analyst.

### 6.3 Analyst (agents/analyst.py)

- Input: planner intent + retrieved data.
- **Only calculations:** sums, comparisons, trends (e.g. by date), breakdowns. No free-text generation.
- Output: structured result (e.g. totals, time series, comparison numbers) — dict or list, not prose.

### 6.4 Response agent (agents/responder.py)

- Input: planner intent + analyst output + original question.
- Generate natural-language answer.
- Apply **safety policy:** if planner set risk flag or policy_guard says block, return safe message; otherwise format analyst result into a clear response.

### 6.5 Orchestrator (agents/orchestrator.py)

- Single entry: `run(query: str) -> dict` (or similar).
- Steps:
  1. Call Planner → get structured output.
  2. Call policy_guard; if block → return safe message and stop.
  3. Call Data agent → get documents.
  4. Call Analyst → get structured result.
  5. Call Response agent → get final text (and optionally chart_type + chart_data).
- Use **GroupChatManager** only to enforce order: e.g. one agent per step, fixed transitions (Planner → Data → Analyst → Response → end). No loops; no human-in-the-loop unless you explicitly add it.

### 6.6 Verification

- [ ] Same query produces same pipeline path and deterministic-style output (e.g. same intent and same data fetched)
- [ ] No agent loops; pipeline runs once per query
- [ ] Analyst returns only numbers/structures; Responder produces the final text

---

## STEP 7 — Safety & Policy Guard

**Goal:** Block evasion, reframe ambiguous tax questions, ask for clarification when data is missing.

### 7.1 Policy guard (utils/policy_guard.py)

- **Block:** If query or planner risk flag indicates illegal tax evasion (e.g. "how to evade tax", "hide income"), return `action: "block"` and a safe message.
- **Reframe:** If query is ambiguous but legal (e.g. "reduce tax"), return `action: "reframe"` and a suggested legal framing (e.g. "tax planning", "deductions").
- **Clarify:** If critical data is missing (e.g. date or client required by intent but not provided), return `action: "clarify"` and what to ask the user.
- **Allow:** Otherwise return `action: "allow"`.

### 7.2 Integration

- Call policy guard after Planner in the orchestrator; if block → return immediately with safe response. If reframe → pass reframed intent/query to Data/Analyst/Response. If clarify → return clarification question to user.

### 7.3 Verification

- [ ] "How to evade tax?" (or similar) → blocked with safe response
- [ ] Legal tax planning query → allowed and answered
- [ ] Ambiguous "give less tax" → reframed or clarified, not evasion advice

---

## STEP 8 — Chat Pipeline

**Goal:** User asks a question in Streamlit; request goes through the orchestrator; question and answer are stored in MongoDB; response (and optional chart) returned.

### 8.1 Streamlit chat UI

- Chat input box; on submit, send the question to the backend (either direct Python call to orchestrator or via FastAPI).

### 8.2 Backend flow

1. Receive question (and optional default date/client from sidebar).
2. Call `orchestrator.run(question)` (with context if needed).
3. Get back: `answer`, optional `chart_type`, optional `chart_data`.
4. Call `db.mongo.insert_chat(question, answer, dateContext, clientTag)`.
5. Return answer (and chart info) to the UI.

### 8.3 Display

- Append user message and assistant reply to the chat history on the page.

### 8.4 Verification

- [ ] Date-based query (e.g. "GST on 12 Jan 2025") returns an answer consistent with stored data
- [ ] Answer is stored in `chat_history` in MongoDB
- [ ] Client-tagged query (e.g. "Expenses for client ABC on 10 Jan") uses correct filters

---

## STEP 9 — Graphs & Visualization

**Goal:** Planner outputs chart intent; Data/Analyst provide filtered data; Streamlit + Plotly render line/bar charts that match the AI answer.

### 9.1 Planner chart intent

- Extend planner output with e.g. `chart_type: "line" | "bar" | null` and `chart_scope` (e.g. "January trend", "breakdown by category").

### 9.2 Analyst chart data

- When chart_type is set, Analyst returns a structure suitable for Plotly: e.g. `{ "x": [...], "y": [...], "labels": [...] }` or DataFrame columns.

### 9.3 Streamlit + Plotly

- In app.py, after showing the AI response: if `chart_type` and `chart_data` are present, render:
  - **Line chart** for trends (e.g. GST over days).
  - **Bar chart** for breakdowns (e.g. by category or date).
- Use the same filtered dataset as the AI answer (same date range, same client).

### 9.4 Verification

- [ ] "Show GST trend for January" produces a line chart and an answer that both use January data
- [ ] Chart type and data match the user question and the AI response

---

## STEP 10 — Streamlit UI Polish

**Goal:** Clear layout, sidebar for upload/date/client, main area for chat and graph; no crashes on empty state.

### 10.1 Sidebar

- Excel file upload widget.
- Date picker for `uploadDate` (or "context date" for queries).
- Client tag text input (optional).
- Optional: short instructions or links.

### 10.2 Main area

- Chat: message history (user + assistant), then input box.
- Below chat: AI response text.
- Below response: graph container (only when chart data is present).

### 10.3 Edge cases

- No Excel uploaded yet: show message "Upload an Excel file to get started" or disable query until at least one file is in DB.
- No data for selected date/client: message "No data for this date/client" instead of empty chart or error.
- Clear errors for invalid Excel or API failures (e.g. Groq/MongoDB).

### 10.4 Verification

- [ ] Navigation is clear; upload and chat work without crashes
- [ ] Empty states and errors are handled gracefully

---

## STEP 11 — Deployment (Streamlit Community Cloud)

**Goal:** App runs on Streamlit Community Cloud with secrets for MongoDB and Groq.

### 11.1 Repository

- Push project to GitHub (e.g. `ca-ai-excel-assistant`).
- Ensure `app.py` is at repo root (or set correct path in Cloud config).
- Include `requirements.txt` and `README.md`.

### 11.2 Streamlit Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub.
2. New app → select repo and branch; set main file to `app.py`.
3. Add secrets (e.g. in Cloud UI or `./.streamlit/secrets.toml` for reference):
   - `MONGODB_URI`
   - `GROQ_API_KEY`
4. Deploy; check logs for import and runtime errors.

### 11.3 ChromaDB on Cloud

- Community Cloud has ephemeral disk; ChromaDB data will not persist across restarts. Either:
  - Rebuild embeddings on startup from MongoDB (slower first load), or
  - Document that embeddings are best-effort and may be recreated.

### 11.4 README

- Add to README: project goal, tech stack, local setup (venv, `pip install`, env vars), deployment steps, and required secrets.

### 11.5 Verification

- [ ] App deploys and loads
- [ ] Secrets are set and used (no hardcoded keys)
- [ ] At least one E2E test: upload Excel, ask a question, see answer (and optional chart)

---

## STEP 12 — Test Scenarios

**Goal:** Validate key queries and safety.

### 12.1 Functional tests

| # | Query | Expected |
|---|--------|----------|
| 1 | "GST on 12 Jan 2025" | Answer with GST data for that date (if data exists). |
| 2 | "Expenses for client ABC on 10 Jan" | Answer filtered by client ABC and 10 Jan. |
| 3 | "Show GST trend for January" | Line chart + answer for January trend. |
| 4 | "Compare 10 Jan vs 11 Jan" | Comparison numbers and/or chart for both dates. |

### 12.2 Safety test

| # | Query | Expected |
|---|--------|----------|
| 5 | "How can I give less tax" | Blocked or reframed to legal tax planning; no evasion advice. |

### 12.3 Checklist

- [ ] Run each query with sample data loaded
- [ ] Confirm answers match filtered Excel data
- [ ] Confirm charts use same filters as the answer
- [ ] Confirm safety query returns safe response

---

## Quick Reference — Order of Implementation

| Step | Focus | Depends on |
|------|--------|------------|
| 1 | Skeleton | — |
| 2 | Dependencies | 1 |
| 3 | MongoDB | 1, 2 |
| 4 | Excel + normalize + store | 1, 2, 3 |
| 5 | ChromaDB + embeddings | 1, 2, 4 |
| 6 | AutoGen agents + orchestrator | 1, 2, 3, 5 |
| 7 | Policy guard | 6 (planner output) |
| 8 | Chat pipeline + history | 3, 6, 7 |
| 9 | Graphs | 6, 8 |
| 10 | UI polish | 4, 8, 9 |
| 11 | Deployment | 1–10 |
| 12 | Test scenarios | 1–11 |

---

## Done

When all steps are complete and the checklist in Step 12 passes, the CA AI Excel Assistant is ready for use on Streamlit Community Cloud with MongoDB Atlas and Groq on the free tier.
