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

## Project structure

```
ca-ai-excel-assistant/
├── app.py           # Streamlit entry
├── api.py           # FastAPI app
├── agents/          # AutoGen agents (planner, data, analyst, responder, orchestrator)
├── db/              # MongoDB (mongo.py, models.py) — Step 3
├── vector/          # ChromaDB client
├── utils/           # Excel parser, normalizer, policy guard
├── requirements.txt
├── .env.example     # Copy to .env and set MONGODB_URI (Step 3)
├── verify_install.py   # Step 2: dependency check
├── verify_mongo.py     # Step 3: MongoDB connection check
└── README.md
```

## Status

- Step 1 — project skeleton; no AI or DB logic yet.
- Step 2 — dependencies locked in `requirements.txt`; run `python verify_install.py` to verify.
- Step 3 — MongoDB connection (files, data_rows, chat_history); set `MONGODB_URI` in `.env`, run `python verify_mongo.py` to verify.
- Step 4 — Excel upload in sidebar (upload date mandatory, client tag optional); parse with pandas, normalize columns/dates/amounts, store in MongoDB (files + data_rows); rowDate from column when present.
- Follow `STEP_BY_STEP_GUIDE.md` in the repo for full implementation.
