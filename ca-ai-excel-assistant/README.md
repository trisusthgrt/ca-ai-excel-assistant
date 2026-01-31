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
5. Run the app:
   ```bash
   streamlit run app.py
   ```
6. (Optional) Run the API:
   ```bash
   uvicorn api:app --reload
   ```

## Project structure

```
ca-ai-excel-assistant/
├── app.py           # Streamlit entry
├── api.py           # FastAPI app
├── agents/          # AutoGen agents (planner, data, analyst, responder, orchestrator)
├── db/              # MongoDB (mongo.py, models.py)
├── vector/          # ChromaDB client
├── utils/           # Excel parser, normalizer, policy guard
├── requirements.txt
├── verify_install.py   # Step 2: dependency check
└── README.md
```

## Status

- Step 1 — project skeleton; no AI or DB logic yet.
- Step 2 — dependencies locked in `requirements.txt`; run `python verify_install.py` to verify.
- Follow `STEP_BY_STEP_GUIDE.md` in the repo for full implementation.
