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

## Setup (local)

1. Clone or open this project.
2. Create and activate a virtual environment (e.g. in project root):
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # macOS/Linux:
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   cd ca-ai-excel-assistant
   pip install -r requirements.txt
   ```
4. Run the app:
   ```bash
   streamlit run app.py
   ```
5. (Optional) Run the API:
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
└── README.md
```

## Status

Step 1 complete — project skeleton; no AI or DB logic yet. Follow `STEP_BY_STEP_GUIDE.md` in the repo for full implementation.
