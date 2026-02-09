## Project: CA AI Excel Assistant — Interview Q&A

> Use this as a script. Each question has a concise, structured answer you can expand verbally.

---

### 1. High‑level overview

**Q:** What does this project do, in one or two sentences?  
**A:** It is a CA‑focused Excel assistant. You upload an Excel file with transactional data, then ask natural‑language questions like “GST breakdown by customer” or “trend of net amount”, and it returns accurate numbers, tables, and charts computed from the latest uploaded file only.

---

### 2. End‑to‑end workflow

**Q:** Can you walk me through the end‑to‑end workflow of the system?  
**A:**  
- The **user** uploads an Excel file via the **Streamlit UI** (`app.py`).  
- The file is **parsed** (`utils/excel_parser.py`) and **normalized** (`utils/normalizer.py`): column names, dates, amounts are standardized and the row‑date column is detected.  
- I then **store metadata** (fileId, original Excel headers, row/column counts, min/max date) and **rows** in **MongoDB** (`db/mongo.py`, `db/models.py`), and create **row embeddings** in **ChromaDB** (`vector/chroma_client.py`) tagged with `fileId`.  
- The uploaded file becomes the **single authoritative dataset** (“latest file”) for all queries.  
- When the user asks a question in chat, it goes to the **Orchestrator** (`agents/orchestrator.py`), which:  
  - Normalizes the text (`utils/query_normalizer.py`),  
  - Routes schema vs data vs explain queries (`utils/query_router.py`),  
  - Runs **Semantic Resolution** (`utils/semantic_column_resolver.py`) to map user terms to real columns,  
  - Uses the **Planner** (`agents/planner.py`) to get intent, date range, and metric,  
  - Applies a **Policy Guard** (`utils/policy_guard.py`) for safety,  
  - Calls the **DataAgent** (`agents/data_agent.py`) to fetch rows from Mongo (strictly scoped to the latest file),  
  - Passes rows to the **Analyst** (`agents/analyst.py`) to compute totals / breakdowns / time series,  
  - And finally uses the **Responder** (`agents/responder.py`) to generate a natural‑language answer, plus chart/table data that the UI renders.

---

### 3. Tech stack

**Q:** What technologies and libraries did you use?  
**A:**  
- **Frontend / UI:** Streamlit (`app.py`).  
- **Backend / Orchestration:** Pure Python modules and “agents” in the `agents/` and `utils/` packages.  
- **Database:** MongoDB (through `pymongo`), with collections for `files`, `data_rows`, and `chat_history`.  
- **Vector store:** ChromaDB for row‑level semantic retrieval.  
- **LLM:** Groq’s LLM API for planning and natural‑language responses, with deterministic fallbacks.  
- **Data processing:** pandas, plus custom normalization and aggregation code.  
- **Charting:** Plotly (used via pandas/Streamlit).  
- **Config:** `.env`, `.streamlit/config.toml`, `.streamlit/secrets`.

---

### 4. Agents and responsibilities

**Q:** What are the main “agents” and what is each responsible for?  
**A:**  
- **Orchestrator (`agents/orchestrator.py`)**: Central coordinator. It wires together normalization, routing, semantic resolution, planning, policy, data fetch, analysis, and response generation. It enforces single‑file authority and logging.  
- **Planner (`agents/planner.py`)**: Turns a normalized query into a structured “plan”: intent (trend, breakdown, summarize, explain, etc.), date filters, metric, and chart needs. Uses Groq when available, otherwise a heuristic fallback.  
- **Semantic Resolver (`utils/semantic_column_resolver.py`)**: Maps user words like “customer”, “agency”, “GST”, “subtype”, “region” to **canonical concepts**, then to actual columns in the latest schema using fuzzy matching. It returns a structured object with `resolved_columns`, `group_by`, `filters`, and unresolved/ambiguous concepts.  
- **Policy Guard (`utils/policy_guard.py`)**: Applies domain safety rules; can block, clarify, or reframe high‑risk queries before data is touched.  
- **DataAgent (`agents/data_agent.py`)**: Turns the planner’s filters into Mongo queries, always scoping to the **latest fileId**, computes daily/monthly totals, and optionally triggers ChromaDB retrieval for explanation/summarize queries.  
- **Analyst (`agents/analyst.py`)**: Performs all **numeric** work: totals, breakdowns, trends, and comparisons, based on the data rows and the resolved `amount_column` and `breakdown_by`.  
- **Responder (`agents/responder.py`)**: Converts the analyst’s computed JSON (totals, breakdowns, series) plus context into a user‑friendly answer. It uses Groq only to phrase the explanation, never to compute numbers.

---

### 5. Semantic column resolution

**Q:** How do you resolve user terms like “customer” or “agency” to actual column names in the Excel?  
**A:**  
- This is done in three explicit stages in `utils/semantic_column_resolver.py`:  
  1. **Stage 1 – Term → Canonical Concept**: Normalize the query and look for variants like “customer”, “client”, “party”, “agency”, “vendor”, etc. and map them to canonical concepts such as `customer`, `gst_amount`, `net_amount`, `total_amount`, `region`, `subcategory`, etc.  
  2. **Stage 2 – Canonical Concept → Column**: For each concept, I compare its variants to the **normalized column names** from the latest file schema using fuzzy similarity (RapidFuzz). If there is exactly one match above a similarity threshold (85), it is resolved; if multiple, the concept is ambiguous; if none, it is unresolved.  
  3. **Stage 3 – Structured Output**: I build and return a pure data structure:
     - `resolved_columns` (concept → actual column name),  
     - `group_by` (columns to group by when the query says “by X”),  
     - `filters` (reserved for future filters),  
     - `unresolved_concepts`, `ambiguous_concepts`.  
- The Planner and Orchestrator never try to parse column names out of raw text – they only use this structured resolution.

---

### 6. Metric safety and correctness

**Q:** How do you make sure metrics like GST or net amount are correct and not hallucinated?  
**A:**  
- Metric resolution is **schema‑driven** and **LLM‑independent**:
  - The Semantic Resolver resolves concepts like `gst_amount`, `net_amount`, `total_amount`, `discount`, etc. to actual columns in the latest file, or reports them as unresolved/ambiguous.  
  - The Orchestrator checks `unresolved_concepts` against what the user requested. If the user asked for “discount amount” and no `discount` column exists, it returns an **explicit error** instead of substituting a different metric.  
  - `get_amount_column_for_metric()` in the resolver uses hints like “gst”, “net”, “total” to pick the right resolved concept, and never substitutes GST for NetValue or vice versa.  
- All numbers are computed by deterministic code in `agents/analyst.py` using the filtered rows from MongoDB. The LLM only receives a JSON summary of these numbers and is instructed never to invent or change them.

---

### 7. Single dataset authority (latest file only)

**Q:** How do you ensure a query only uses the latest uploaded file and doesn’t mix datasets?  
**A:**  
- In the **upload flow**, each file gets a unique `fileId`, and all rows and embeddings are tagged with that `fileId`.  
- `db.mongo.get_latest_file_schema()` and `db.mongo.get_latest_file_meta()` always return the schema and meta for the **most recently inserted** file.  
- In the Orchestrator, before calling the DataAgent, I force `planner_output["file_id"]` to be the latest `fileId` for any **row‑date‑based** queries.  
- The DataAgent itself (`agents/data_agent.fetch_data`) enforces that for non‑upload‑date queries you **must** have a `file_id`; if not, it aborts and returns no rows.  
- Chroma queries in `vector/chroma_client.py` are always called with `where = {"fileId": file_id}` when explanations or summaries need RAG, and I discard any retrieved chunk that doesn’t match the current `fileId`.  
- This guarantees that all answers are based on a **single, latest dataset** and there is no mixing between old and new uploads.

---

### 8. RAG vs direct DB access

**Q:** When do you use the vector store (Chroma) instead of Mongo, and how do you keep that safe?  
**A:**  
- By default, numeric questions (totals, breakdowns, trends) are answered purely from **MongoDB** rows and the `Analyst`’s computations.  
- I only consider vector search (RAG) for **explain / summarize** style queries, as decided by the Planner and Query Router.  
- When RAG is used:
  - The DataAgent asks Chroma for semantically similar rows, filtered by `fileId` and sometimes by `rowDate`/`clientTag`.  
  - The Orchestrator passes only the **computed analyst summary** (and optionally a subset of retrieved row text) to the Responder.  
  - The Responder’s system prompt explicitly forbids the LLM from computing new numbers or inventing dates; it can only rephrase and explain.  
- So RAG is purely a **contextual explanation layer**; all core calculations still come from deterministic DB queries and Python code.

---

### 9. Cloud and scalability

**Q:** How would you deploy and scale this system in the cloud?  
**A:**  
- The app is naturally **stateless** at the compute layer: `app.py`, the Orchestrator, and all agents don’t store session state; they rely on MongoDB and ChromaDB.  
- In the cloud I would:
  - Containerize the app (UI + Orchestrator + agents) and run it as a **Kubernetes Deployment** (or ECS/Cloud Run service) with multiple replicas behind a cloud load balancer.  
  - Use a **managed MongoDB** (e.g. MongoDB Atlas) for the `files` and `data_rows` collections, with replica sets and optional sharding.  
  - Run ChromaDB as a service with a persistent volume, or use a managed vector database, and partition data by `fileId` and potentially `tenantId`.  
  - Use Groq as a managed LLM service and rely on its own scaling, while I monitor and rate‑limit calls.  
  - Hook all logs (router decisions, file_id, concepts, row counts, rag_used) into a central logging/metrics system so I can autoscale based on latency and load.  
- This gives horizontal scalability at the app layer, and vertical/horizontal scalability at the DB/vector/LLM layers using their cloud‑native features.

---

### 10. Is it production‑grade and stable for 6 months?

**Q:** Do you consider this production‑grade, and would it run without major issues for 6 months?  
**A:**  
- **Architecturally**, it is close to production‑grade:
  - Clear separation of concerns (UI, orchestrator, domain agents, data access, vector store).  
  - Strong invariants: single dataset authority, schema authority, metric safety, and strict RAG usage.  
  - Logs enough metadata per query to debug and audit behavior.  
- To be fully comfortable with a 6‑month production SLA, I’d invest in:  
  - **Automated tests**: unit tests for semantic resolution, planner, analyst logic, and integration tests driven by `test_queries_and_expected_answers.csv`.  
  - **Robust observability**: dashboards for error rates, latency per pipeline stage, and data freshness for each fileId.  
  - **Operational runbooks**: clear procedures for rotating .env secrets, handling Mongo/Chroma outages, and upgrading dependencies.  
  - **Auth and multi‑tenant isolation** if used by multiple clients.  
- So my honest answer is: the design and code quality are aligned with production‑grade patterns, but I would add testing, observability, and auth/tenant isolation before promising “no problems in 6 months” in a real business setting.

---

### 11. Possible improvements and future work

**Q:** What are the main improvements or next steps you would make?  
**A:**  
- **Testing and QA**:  
  - Turn `test_queries_and_expected_answers.csv` into automated regression tests.  
  - Add property‑based tests around the semantic resolver and metric safety logic.  
- **Auth and multi‑tenant support**:  
  - Add signup/signin with JWTs, associate `tenantId` with files and rows, and enforce tenant isolation in Mongo and Chroma queries.  
- **Performance**:  
  - Profile and optimize heavy queries and aggregations.  
  - Improve caching strategies for commonly requested ranges and breakdowns.  
- **UI/UX**:  
  - Add clearer explanations when certain metrics or columns don’t exist.  
  - Provide saved query templates for common CA workflows (GST returns, monthly summaries, client‑wise analysis).  
- **Resilience**:  
  - Add retries and fallback behavior for external services (Mongo, Chroma, Groq).  
  - Implement circuit‑breaker patterns around LLM calls if needed.

---

### 12. Difference from ChatGPT or a generic LLM

**Q:** How is this different from just asking ChatGPT about my Excel file?  
**A:**  
- ChatGPT is a **general‑purpose LLM** that doesn’t know your schema or your latest file boundaries by default. It can hallucinate columns and metrics unless you engineer prompts and tooling extremely carefully.  
- This app is a **domain‑specific assistant** that:
  - Only operates on the **latest uploaded file** and never mixes datasets.  
  - Uses a **semantic column resolver** tied to your actual schema and fails fast if a metric or column doesn’t exist.  
  - Computes all numbers via deterministic Python code and uses the LLM only as a language layer on top of those results.  
  - Logs detailed metadata per query for auditability in a CA or compliance context.  
- In short, it’s not “just ChatGPT”; it’s a structured, production‑style analytics pipeline that happens to use an LLM for phrasing but not for data access or calculation.

