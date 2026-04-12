# Setup Guide

## Prerequisites

| Tool | Minimum version | Check with |
|------|----------------|------------|
| **Node.js** | 18.18+ (recommended: 24.x) | `node --version` |
| **npm** | 9+ (ships with Node 18+) | `npm --version` |
| **Python** | 3.10+ | `python3 --version` |

Next.js 15 requires Node 18.18 or later. The production yourpeer.nyc app uses Node 24.x.

**If your Node version is too old**, install or update via [nvm](https://github.com/nvm-sh/nvm):

```
nvm install 24
nvm use 24
```

Or download directly from [nodejs.org](https://nodejs.org/).

## Architecture

The app runs as **two processes** locally:

| Process | Port | What it serves |
|---------|------|----------------|
| **FastAPI backend** | `:8000` | Chat API (`/chat/`), Admin API (`/admin/api/*`), health check |
| **Next.js frontend** | `:3000` | All pages (`/chat`, `/admin/*`, existing yourpeer.nyc routes) |

Next.js proxies API calls to FastAPI via route handlers in `src/app/api/`. The chat and feedback routes add IP-based rate limiting before proxying.

```
Browser (:3000)
  ├── /chat              → Next.js renders ChatContainer
  ├── /admin/overview    → Next.js renders admin dashboard
  ├── /api/chat          → route handler (rate limit) → FastAPI :8000/chat/
  ├── /api/admin/stats   → route handler (auth) → FastAPI :8000/admin/api/stats
  └── / (yourpeer.nyc)   → existing Next.js pages
```

## 1. Backend Setup

### Create virtual environment

```
python3 -m venv backend/venv
```

### Activate and install dependencies

**Mac/Linux:**

```
source backend/venv/bin/activate
pip install -r backend/requirements.txt
```

**Windows:**

```
backend\venv\Scripts\activate
pip install -r backend/requirements.txt
```

### Configure environment

Create a `.env` file in the repo root:

```
DATABASE_URL="postgresql://user:password@host:port/streetlives"
ANTHROPIC_API_KEY="your-anthropic-api-key"
```

**Database URL:** Contact the Streetlives team for PostgreSQL staging credentials. The RDS instance requires IP whitelisting.

**Anthropic API key:** Required for all LLM features (conversational responses, slot extraction, crisis detection). Get a key at [console.anthropic.com](https://console.anthropic.com/). Without this key, the chatbot falls back to regex-only slot extraction, regex-only crisis detection, and static fallback responses.

**Semantic routing model:** The `sentence-transformers` package (included in `requirements.txt`) downloads the `all-MiniLM-L6-v2` model (~80 MB) on first startup. This requires internet access to `huggingface.co`. After the first download, the model is cached locally. If the model cannot be downloaded, the system degrades gracefully — Tier 2 semantic routing is disabled and messages fall through from regex (Tier 1) directly to LLM (Tier 3).

**Optional (local dev):** The following security variables are required in production but default to open/disabled for local development:

```
# SESSION_SECRET — signs session tokens. Unset = unsigned tokens (dev only).
# ADMIN_API_KEY — protects /admin/ endpoints. Unset = open access (dev only).
# CORS_ALLOWED_ORIGINS — defaults to localhost:3000 for local dev.
# PILOT_DB_PATH — SQLite path for pilot data persistence (e.g., data/pilot.db).
#   When set, audit events and sessions survive server restarts.
#   When unset (default), all data is in-memory only.
```

### Run the backend

```
cd backend
uvicorn app.main:app --reload
```

Verify it's running: `curl http://localhost:8000/api/health` should return `{"status": "ok"}`.

## 2. Frontend Setup

### Verify Node version

```
node --version
```

Must be 18.18 or later. If not, see [Prerequisites](#prerequisites) above.

### Install dependencies

```
cd frontend-next
npm install
```

### Configure environment

Create a `.env.local` in the `frontend-next/` directory:

```
CHAT_BACKEND_URL=http://localhost:8000
```

### Run the frontend

```
cd frontend-next
npm run dev
```

Open `http://localhost:3000`. The chat is at `/chat`, the admin console is at `/admin`.

## 3. Running Both Together

Open two terminals:

**Terminal 1 — Backend:**

```
source backend/venv/bin/activate
cd backend
uvicorn app.main:app --reload
```

**Terminal 2 — Frontend:**

```
cd frontend-next
npm run dev
```

That's it. Changes to either backend Python or frontend TypeScript will hot-reload.

## 4. Running Tests

All backend tests run without external services (database and LLM calls are mocked):

```
pytest
```

That's it. `pyproject.toml` configures the test paths and Python path automatically. To run a single file or see full output:

```
pytest tests/unit/test_slot_extractor.py  # one file
pytest tests/unit/ -k reset              # one test by name
pytest -s                             # show print output
```

To run LLM integration tests against the real Claude API:

```
ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_llm_slot_extractor.py -k live
```

## IDE Setup (Cursor / VS Code)

1. Open Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`)
2. Run **Python: Select Interpreter**
3. Choose the interpreter from `backend/venv`

TypeScript/Next.js support works automatically via `tsconfig.json`.

## 5. Deploying to Render (Staging)

The `render.yaml` deploys two services from this single repo:

| Service | Name | Root dir | Access |
|---------|------|----------|--------|
| FastAPI backend | `yourpeer-chatbot-api` | `backend/` | Private (internal only) |
| Next.js frontend | `yourpeer-chatbot` | `frontend-next/` | Public |

The backend is a private service — not accessible from the public internet. All traffic flows through the Next.js frontend, which adds rate limiting and auth headers. The frontend's `CHAT_BACKEND_URL` env var points at the backend's internal Render URL.

**First deploy:**

1. Push the repo to GitHub
2. Go to Render → **New** → **Blueprint** → connect the repo
3. Render reads `render.yaml` and creates both services
4. Set the secret env vars (database URL, API keys) on the backend service
5. After the backend deploys, find its internal URL in the Render dashboard (under the private service's settings) and set `CHAT_BACKEND_URL` on the frontend service to that URL (format: `http://yourpeer-chatbot-api:PORT`)

**After deploy:** The chat is at `https://<frontend-url>.onrender.com/chat` and the admin console is at `https://<frontend-url>.onrender.com/admin`. The backend has no public URL.

## Repo Structure

```
yourpeer-chatbot/
├── backend/              # FastAPI backend (headless API)
│   ├── app/
│   │   ├── main.py       # pure API — no static file serving
│   │   ├── routes/       # /chat/, /admin/api/*
│   │   ├── services/     # chatbot, crisis detector, etc.
│   │   └── ...
│   └── requirements.txt
├── frontend-next/        # Next.js frontend
│   ├── src/
│   │   ├── app/          # routes: /chat, /admin/*
│   │   ├── components/   # chat/ and admin/ components
│   │   ├── hooks/        # use-chat, use-speech-recognition
│   │   └── lib/          # store, API wrappers, types
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.ts
│   └── tsconfig.json
├── tests/                # Backend test suite (unchanged)
├── render.yaml           # Deploys both services
└── SETUP.md              # This file
```
