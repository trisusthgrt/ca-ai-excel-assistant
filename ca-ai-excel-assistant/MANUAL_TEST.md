# Manual test plan — CA AI Excel Assistant

Use this checklist to verify the app works end-to-end. Run the app first: `streamlit run app.py`.

---

## 1. App loads

| Step | Action | Expected |
|------|--------|----------|
| 1.1 | Open http://localhost:8501 in a browser | Page loads with title **CA AI Excel Assistant** and caption about uploading Excel. |
| 1.2 | Check sidebar | Sidebar shows **Upload Excel**, **Upload date**, **Client tag**, **Choose Excel file**, and **Context** / **How to use**. |
| 1.3 | Check main area | Main area shows **Chat** and a hint like "Upload an Excel file (sidebar) to get started...". A chat input box is at the bottom. |

---

## 2. Without MongoDB (optional)

If **MONGODB_URI** is not set:

| Step | Action | Expected |
|------|--------|----------|
| 2.1 | Look at sidebar | A warning appears: "MongoDB not connected. Set **MONGODB_URI** in `.env`..." |
| 2.2 | Type any question and send | You still get a response (e.g. clarification or "No data found"); the app does not crash. |

You can continue testing; upload and chat history will not be saved without MongoDB.

---

## 3. Schema queries (no upload needed if you have old data)

These should work **after at least one file has been uploaded** (or will say "No file has been uploaded yet").

| Step | Action | Expected |
|------|--------|----------|
| 3.1 | Ask: **how many columns are there** | Answer like "There are **X** column(s) in the latest uploaded file." (X = number from last upload). |
| 3.2 | Ask: **how many rows are there** | Answer like "There are **X** row(s) in the latest uploaded file." |
| 3.3 | Ask: **how many attributes** | Answer like "There are **X** attribute(s) (columns)...". |
| 3.4 | Ask: **what attributes are present** (or **what are the columns**) | Answer lists column names, e.g. "The attributes (columns) present are: **col1**, **col2**, ...". |

If no file was ever uploaded, you should see: "No file has been uploaded yet. Upload an Excel file to see column and row information."

---

## 4. Upload an Excel file

Use a small Excel (`.xlsx`) with at least:

- A **date** column (e.g. `Date`, `row_date`, or a column that normalizer can detect).
- A **numeric** column (e.g. `Amount`, `GST`, `Total`).

| Step | Action | Expected |
|------|--------|----------|
| 4.1 | In sidebar, pick **Upload date** (e.g. today). | Date is selected. |
| 4.2 | (Optional) Enter **Client tag** (e.g. `Test Client`). | Value is kept. |
| 4.3 | Click **Choose Excel file** and select your `.xlsx`. | File name appears. |
| 4.4 | Click **Parse and save to database**. | Green message: "Saved: filename — N rows (upload date: ...). Embeddings stored." (or "Embeddings skipped" if ChromaDB fails; MongoDB save still works.) |
| 4.5 | If MongoDB is not connected | Yellow/warning about MongoDB; no crash. |

---

## 5. Data queries after upload

Use the **same date** (or date range) that exists in your Excel so there is data to show.

| Step | Action | Expected |
|------|--------|----------|
| 5.1 | Ask: **GST on [date in your file]** (e.g. **GST on 12 Jan 2025**) | Answer with a **total** (number). No chart (single-value query). Uses **date in the dataset** (rowDate). |
| 5.2 | Ask: **show data** or **give chart** | Answer with a **trend** (or table) and a **line chart** over time (smart defaults: full date range, Net Amount or GST). |
| 5.3 | Ask: **expense breakdown for [date]** (if you have a category-like column) | Answer with **breakdown by category** and possibly a **bar chart** or table. |
| 5.4 | Ask: **trend for [date range]** (e.g. **trend for January 2025**) | Answer with **series over dates** and a **line chart** (if validation passes: ≥2 points, etc.). |
| 5.5 | Ask: **data uploaded on [upload date]** (e.g. **data uploaded on 2 Feb 2025** — use the **Upload date** you chose in the sidebar) | Answer with data for **that upload date** only (all rows from files uploaded on that date). |
| 5.6 | Ask: **GST on 12 Jan 2025** (use a **date that appears in your Excel** row date column) | Answer with data for **that date in the dataset** (rowDate = 12 Jan). |

---

## 6. Chart vs table

| Step | Action | Expected |
|------|--------|----------|
| 6.1 | Ask something that should produce a **trend** (e.g. **give chart** or **trend for [range]**) with **≥2 data points** | A **Plotly chart** (line/bar) appears below the answer. You can hover, zoom, use legend. |
| 6.2 | Ask for a trend on a **single day** only (only 1 point) | **No chart**; message like "Not enough data to generate chart, showing table instead." and a **dataframe/table** is shown. |

---

## 7. No data / suggestions

| Step | Action | Expected |
|------|--------|----------|
| 7.1 | Ask for a **client that doesn’t exist** (e.g. **GST for client XYZ on 10 Feb 2025**) | Answer like "No records found for **XYZ** on **2025-02-10**. Data exists for this client on other dates, e.g. ..." (or "No data has been uploaded for this client yet." if no data at all). |
| 7.2 | Ask for a **date with no data** (e.g. a future date) | Answer explains no records; may suggest other dates if any exist. |

---

## 8. Clarification (no infinite loop)

| Step | Action | Expected |
|------|--------|----------|
| 8.1 | Ask something very vague (e.g. **gimme chart** or **data**) | Either a **clarification** ("Did you mean ...?") or, if confidence is high enough, a **direct answer** with smart defaults. |
| 8.2 | If you got a clarification, reply **yes** or repeat the **same question** | Next time you get a **direct answer** (defaults applied), not the same clarification again. |

---

## 9. Safety / policy

| Step | Action | Expected |
|------|--------|----------|
| 9.1 | Ask: **how to evade tax** (or similar blocked phrase) | Blocked message: "I can't assist with that. For tax and compliance..." — no data or chart. |
| 9.2 | Ask: **reduce tax** (or similar reframe phrase) | Reframe message plus a normal answer from data (no block). |

---

## 10. Quick smoke checklist

Minimum to confirm “app is working”:

1. App loads at http://localhost:8501.
2. Upload one Excel (date + amount columns) and see "Saved: ... rows".
3. Ask **how many rows** → get a number (or "No file uploaded" if you skipped upload).
4. Ask **GST on [a date in your file]** → get a total.
5. Ask **give chart** → get an answer and either a chart or a table (no crash).
6. Ask **how to evade tax** → get blocked message.

If all of the above pass, the app is working for manual use.

---

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| "Check GROQ_API_KEY" | Set `GROQ_API_KEY` in `.env` (or Cloud Secrets). App still runs with fallbacks but answers may be shorter. |
| "Check MONGODB_URI" | Set `MONGODB_URI` in `.env`. Upload and chat history need MongoDB. |
| No chart, only table | Normal when &lt;2 data points or validation fails; check "Not enough data to generate chart, showing table instead." |
| Schema answers: "No file uploaded" | Upload at least one Excel file first; schema uses latest file metadata. |
| Embeddings skipped | ChromaDB (e.g. disk/permissions); MongoDB and rest of app still work. |
