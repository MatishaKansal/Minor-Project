# Ledgerline audit frontend

A React frontend for searching persisted financial documents through the FastAPI backend. Uploaded files are parsed and indexed in Supabase; retrieved chunks and sources are shown in chat. LLM answer generation is not connected.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

In another terminal, from the project root, start the API:

```powershell
pip install -r requirements.txt
uvicorn backend.api:app --reload
```

Open the local URL printed by Vite. The development server proxies `/api` to FastAPI at `127.0.0.1:8000`. To create a production build, run `npm run build`.
