# Deployment Guide

## Platform: Render

The app deploys as **two Render services** from a single repo:

| Service | Type | Root dir | What it runs |
|---------|------|----------|-------------|
| `yourpeer-chatbot-api` | Private (Python) | `backend/` | FastAPI — internal API (not publicly accessible) |
| `yourpeer-chatbot` | Web (Node) | `frontend-next/` | Next.js — chat UI + admin console (public-facing) |

The backend is a **private service** — only reachable via Render's internal networking from the Next.js frontend. All public traffic flows through the frontend, which adds IP-based rate limiting and auth headers before proxying to the backend.

### Prerequisites

- All changes pushed to GitHub
- A [Render](https://render.com) account (sign up with GitHub)
- Node.js 20.9+ (set via `NODE_VERSION` env var in `render.yaml`)
- Your `ANTHROPIC_API_KEY` from [Anthropic Console](https://console.anthropic.com/)
- Your `DATABASE_URL` in the format: `postgresql://user:password@host:port/streetlives`

### Files required

These files must be in your repo before deploying:

- `render.yaml` in the repo root (defines both services)
- `backend/app/main.py` as a headless API (no static file serving)
- `frontend-next/` directory with `package.json`, `next.config.js`, and all source files
- `frontend-next/src/lib/chat/` directory with `types.ts`, `api.ts`, `store.ts`

### Deploy steps

1. Go to [render.com](https://render.com) and sign in with GitHub
2. Click **New → Blueprint**
3. Connect the `ianlau20/yourpeer-chatbot` repository and select the `frontend` branch
4. Render reads `render.yaml` and creates both services automatically
5. On the **backend service** (`yourpeer-chatbot-api`), set the secret environment variables:
   - `ANTHROPIC_API_KEY` = your Anthropic API key
   - `DATABASE_URL` = your full PostgreSQL connection string
   - `SESSION_SECRET` = a random string for signing session tokens (generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
   - `ADMIN_API_KEY` = a random string to protect the admin API (generate the same way)
   - `CORS_ALLOWED_ORIGINS` = your frontend URL (e.g. `https://yourpeer-chatbot-gjn7.onrender.com`)
6. On the **frontend service** (`yourpeer-chatbot`), set:
   - `CHAT_BACKEND_URL` = the backend's **internal** Render URL (find this in the Render dashboard under the private service's settings — format: `http://yourpeer-chatbot-api:PORT`)
   - `ADMIN_API_KEY` = same value as the backend's `ADMIN_API_KEY` (the Next.js proxy forwards this to the backend)
7. Deploy both services

The backend build takes 2–3 minutes. The frontend build takes 1–2 minutes. When both finish, your app will be live.

### After deploy

- The chat interface is at `https://<frontend-url>.onrender.com/chat`
- The staff review console is at `https://<frontend-url>.onrender.com/admin`
- The backend has **no public URL** — it's only accessible internally from the frontend
- Verify the old public backend URL is not reachable (if upgrading from free tier)

### Auto-deploy

Render automatically redeploys both services when you push to the branch configured in the Blueprint. No manual action needed after initial setup.

### Starter tier notes

- Both services run on Render's Starter plan ($7/month each) — always-on, no cold starts
- The backend is a private service — not reachable from the public internet
- The semantic routing model (`all-MiniLM-L6-v2`, ~80 MB) is downloaded from Hugging Face on first startup and cached. The build environment must have outbound access to `huggingface.co`. First startup adds ~1-2 seconds for model loading (amortized across all requests). Memory footprint: ~100 MB for the model + embeddings. If the model download fails, the system degrades gracefully — semantic routing is disabled
- Set `PILOT_DB_PATH=data/pilot.db` to persist audit log and session data across deploys. When unset, data is in-memory only and resets on each deploy
- Rate limiting runs at two layers: the Next.js frontend (per-IP) and the FastAPI backend (per-session + per-IP)

### Environment variables reference

#### Backend service (`yourpeer-chatbot-api`)

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key — powers all LLM features (conversational responses via Haiku, slot extraction via Haiku, crisis detection via Sonnet). Without this, the system falls back to regex-only slot extraction, regex-only crisis detection, and static fallback responses |
| `DATABASE_URL` | Yes | PostgreSQL connection string for Streetlives DB |
| `SESSION_SECRET` | Production | Random string for HMAC-signing session tokens. Without this, session tokens are unsigned (fine for local dev, required for production) |
| `ADMIN_API_KEY` | Production | Random string for admin API authentication. Without this, admin endpoints are open (fine for local dev, required for production) |
| `CORS_ALLOWED_ORIGINS` | Production | Comma-separated list of allowed origins (e.g. `https://yourpeer-chatbot-gjn7.onrender.com`). Defaults to `http://localhost:3000,http://127.0.0.1:3000` for local dev |
| `PYTHON_VERSION` | No | Python version (e.g. `3.12.0`) — set automatically by `render.yaml`. Render uses its default if not set |

#### Frontend service (`yourpeer-chatbot`)

| Variable | Required | Description |
|---|---|---|
| `CHAT_BACKEND_URL` | Yes | Internal URL of the private backend service (e.g. `http://yourpeer-chatbot-api:PORT`) — find this in the Render dashboard under the private service's settings |
| `ADMIN_API_KEY` | Production | Must match the backend's `ADMIN_API_KEY`. The Next.js admin proxy forwards this as a Bearer token to the backend |
| `NODE_VERSION` | No | Node.js version (e.g. `24.0.0`) — set automatically by `render.yaml`. Render uses its default if not set |

### Outbound IPs

If the Streetlives database has an IP allowlist, you'll need to add your **backend** service's outbound IPs. Find them in the Render Dashboard: open the `yourpeer-chatbot-api` service → click **Connect** (upper right) → switch to the **Outbound** tab → copy the IP ranges and add them to the database's security group or access rules.

The frontend service does not connect to the database directly.

### Troubleshooting

**Backend build fails with missing module:** Make sure `backend/requirements.txt` includes `sqlalchemy`, `psycopg2-binary`, and `anthropic`.

**Frontend build fails with Node version error:** The `render.yaml` sets `NODE_VERSION` to `24.0.0`. If you see syntax errors like `Unexpected token '??='`, Render may be using an older Node. Check the `NODE_VERSION` env var in the Render dashboard.

**Frontend build fails with missing modules:** Run `cd frontend-next && rm package-lock.json && npm install` locally, commit the regenerated `package-lock.json`, and push.

**Chat page loads but no responses:** Check that `CHAT_BACKEND_URL` on the frontend service points to the backend's internal URL (format: `http://yourpeer-chatbot-api:PORT`). Since the backend is a private service, you can't test it directly in the browser — use the frontend's `/api/health` proxy or check the backend service logs in the Render dashboard. The health endpoint returns per-component status (database, LLM, semantic router) — a `"status": "unhealthy"` response indicates the database is unreachable, while `"degraded"` means the LLM or semantic router is unavailable but the service still works in reduced mode.

**Database connection errors:** Verify the `DATABASE_URL` is correct in the Render dashboard under the backend service's Environment Variables.

**LLM slot extraction not working:** Check that `ANTHROPIC_API_KEY` is set on the backend service. The app logs whether LLM extraction is enabled on startup. Without the key, everything still works — it just uses regex-only extraction.

**Semantic routing not loading:** Check the backend logs for "Semantic router initialization failed" or "sentence-transformers not installed". The model requires outbound access to `huggingface.co` on first startup to download (~80 MB). After the first download, it's cached locally. If the model can't be downloaded, the system works without it — messages go from regex directly to LLM fallback.

**Staff console empty after deploy:** The audit log is in-memory and starts empty on each deploy. Data populates as users chat. For persistent audit data, replace the in-memory store with a database.

**CORS errors in browser console:** Make sure `CORS_ALLOWED_ORIGINS` on the backend includes the frontend's public URL. Defaults to `http://localhost:3000,http://127.0.0.1:3000` for local dev.
