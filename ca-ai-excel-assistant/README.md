# CA AI Excel Assistant

AI system for a Chartered Accountant firm: upload Excel files, ask date-specific questions, get answers and charts. Built with Streamlit, FastAPI, AutoGen, MongoDB, and ChromaDB.

## Tech stack

- **UI:** Streamlit  
- **API:** FastAPI  
- **AI:** AutoGen (Planner → Data → Analyst → Response)  
- **LLM:** Groq  
- **DB:** MongoDB Atlas (M0)  
- **Vector:** ChromaDB (local)  
- **Charts:** Plotly  

## Setup (local) — Step 2

1. Clone or open this project.
2. Create and activate a virtual environment (e.g. in project root):
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # macOS/Linux:
   source venv/bin/activate
   ```
3. Install dependencies (from `ca-ai-excel-assistant` or project root):
   ```bash
   cd ca-ai-excel-assistant
   pip install -r requirements.txt
   ```
4. **Verify install (Step 2):**
   ```bash
   python verify_install.py
   ```
   All packages should show `OK`; if any show `FAIL`, run `pip install -r requirements.txt` again.
5. **MongoDB (Step 3):** Copy `.env.example` to `.env` and set `MONGODB_URI` (Atlas connection string). Then:
   ```bash
   python verify_mongo.py
   ```
   You should see "Step 3 verification passed."
6. Run the app:
   ```bash
   streamlit run app.py
   ```
7. (Optional) Run the API:
   ```bash
   uvicorn api:app --reload
   ```

## Deploy on Streamlit Community Cloud (Step 11)

1. **Repository:** Use a repo whose **root contains** `app.py` (this folder as the repo root). Push to GitHub.
2. **Streamlit Cloud:** Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub.
3. **New app:** Click *New app* → select your repo and branch → set **Main file path** to `app.py` → Deploy.
4. **Secrets:** In the app’s *Settings* → *Secrets*, add (as TOML or key/value):
   - `MONGODB_URI` — your MongoDB Atlas connection string (required for saving data and chat).
   - `GROQ_API_KEY` — your Groq API key (required for full LLM behavior; app works with fallbacks if missing).
   Optional: `MONGODB_DB_NAME`, `GROQ_MODEL`, `CHROMA_PERSIST_DIR`.
5. **ChromaDB on Cloud:** Community Cloud has ephemeral disk. ChromaDB data does **not** persist across restarts. Embeddings are recreated when users upload new Excel files; existing embeddings are lost on redeploy. For persistent embeddings you’d need to rebuild from MongoDB on startup (not included here).
6. **Verify:** After deploy, open the app URL. If MongoDB is set, upload an Excel file and ask a question; check that the answer appears and (in Atlas) that `chat_history` has a new document.

## Project structure

```
ca-ai-excel-assistant/
├── app.py              # Streamlit entry (Step 11: deploy with this as main file)
├── api.py              # FastAPI app
├── agents/              # AutoGen agents (planner, data, analyst, responder, orchestrator)
├── db/                  # MongoDB (mongo.py, models.py)
├── vector/              # ChromaDB client
├── utils/               # Excel parser, normalizer, policy guard
├── .streamlit/
│   ├── config.toml      # Streamlit config (Step 11)
│   └── secrets.example.toml   # Reference for Cloud secrets
├── requirements.txt
├── .env.example         # Copy to .env locally; on Cloud use Secrets
├── verify_install.py    # Step 2: dependency check
├── verify_mongo.py      # Step 3: MongoDB connection check
├── verify_policy.py     # Step 7: policy guard check
└── README.md
```

## Status

- Step 1 — project skeleton; no AI or DB logic yet.
- Step 2 — dependencies locked in `requirements.txt`; run `python verify_install.py` to verify.
- Step 3 — MongoDB connection (files, data_rows, chat_history); set `MONGODB_URI` in `.env`, run `python verify_mongo.py` to verify.
- Step 4 — Excel upload in sidebar (upload date mandatory, client tag optional); parse with pandas, normalize columns/dates/amounts, store in MongoDB (files + data_rows); rowDate from column when present.
- Step 5 — ChromaDB: after each upload, one embedding per row (text = key: value string); metadata uploadDate, rowDate, clientTag, fileId; persist in `chroma_db/` (or `CHROMA_PERSIST_DIR`). Semantic search via `chroma_client.query(text, n_results, where=...)`.
- Step 6 — AutoGen-style pipeline (Planner → Policy → Data → Analyst → Response): Planner (Groq) extracts intent, dates, clientTag, risk; Data agent queries MongoDB + ChromaDB; Analyst does calculations only; Responder (Groq) formats answer and applies safety. Orchestrator runs once per query; returns answer + optional chart_type/chart_data. Set `GROQ_API_KEY` in `.env` for full LLM behavior.
- Step 7 — Policy guard: block evasion phrases and planner risk_flag; clarify when date or client missing for intent; reframe ambiguous legal phrasing (e.g. "reduce tax") to legal tax planning; allow otherwise. Integrated in orchestrator and responder.
- Step 8 — Chat pipeline: Streamlit chat input → orchestrator.run(question) → store question + answer in MongoDB chat_history → display answer and optional chart in chat. Session state holds message history; charts (line/bar) shown when chart_data present.
- Step 9 — Graphs: Planner outputs chart_type (line/bar) and chart_scope (title); Analyst returns series/breakdown/compare; orchestrator builds chart_data { x, y, labels, title }; app renders Plotly line/bar after each answer using the same filtered data as the AI response. Helper _render_chart() centralizes rendering.
- Step 10 — UI polish: Sidebar (upload, date, client tag, Context, “How to use” expander, MongoDB connection warning); main area (chat, empty-state hint, Plotly charts when present); errors for upload and orchestrator (Groq/MongoDB) shown clearly; no crashes on empty state.
- Step 11 — Deployment: `app.py` is the Streamlit entry; `.streamlit/config.toml` for server/theme; `.streamlit/secrets.example.toml` documents Cloud secrets. Deploy on Streamlit Community Cloud with repo root = this folder; add `MONGODB_URI` and `GROQ_API_KEY` in Cloud Secrets. ChromaDB on Cloud is ephemeral (see README).
- Follow `STEP_BY_STEP_GUIDE.md` in the repo for full implementation.
