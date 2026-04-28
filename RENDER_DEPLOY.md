# Deploy to Render

One Web Service runs both the agent worker (background) and the Flask UI (foreground).

## Plan

- **Starter plan ($7/mo)** — required. Free tier sleeps after 15 min, which kills the agent and breaks dispatches. Starter stays awake 24/7.
- **Region: Singapore** — closest to LiveKit India South. Render doesn't have an India region.

## Steps

### 1. Push this directory to a GitHub repo

```bash
cd /Users/rohankumarpandey/Finn/personal/MAS/livekit
git init
git add -A
git commit -m "Initial LiveKit voice assistant"
gh repo create livekit-voice-assistant --private --source=. --push
```

### 2. Create the service on Render

Either:

**Option A — Blueprint (recommended)**
1. Go to https://dashboard.render.com/blueprints
2. Click **New Blueprint Instance**
3. Connect the GitHub repo
4. Render will read `render.yaml` and create the service automatically

**Option B — Manual**
1. https://dashboard.render.com → New → Web Service
2. Connect the GitHub repo
3. Settings:
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `bash start.sh`
   - Plan: Starter
   - Region: Singapore

### 3. Set environment variables

In the Render dashboard → your service → **Environment**:

| Variable | Value |
|---|---|
| `LIVEKIT_URL` | `wss://test-u76zygyi.livekit.cloud` |
| `LIVEKIT_API_KEY` | (from `.env`) |
| `LIVEKIT_API_SECRET` | (from `.env`) |
| `LIVEKIT_SIP_TRUNK_ID` | (from `.env`, e.g. `ST_xxxxx`) |
| `OPENAI_API_KEY` | (from `.env`) |
| `OPENAI_REALTIME_MODEL` | `gpt-realtime-mini` |

(`PORT` is auto-set by Render — don't override.)

### 4. Deploy

Render auto-deploys on push to the connected branch. First deploy ~3-4 min.

### 5. Test

Once deployed, your URL is something like `https://livekit-voice-assistant.onrender.com`.

- Open it in browser → orb UI loads → test browser session
- Or trigger a phone call:
  ```bash
  curl -X POST https://livekit-voice-assistant.onrender.com/call \
    -H "Content-Type: application/json" \
    -d '{"phone":"+919163304219","name":"Rohan"}'
  ```

Phone rings, AI speaks. WebRTC works fine from Render's datacenter — none of the local UDP/CGNAT issues.

## Logs

Render dashboard → your service → **Logs** tab. Both the agent worker and gunicorn write to the same stream.

## When it breaks

- **`LIVEKIT_SIP_TRUNK_ID not configured`** → env var missing in Render dashboard
- **Agent log shows `worker registered` but calls don't dispatch** → `agent_name` mismatch; this project uses `voice-assistant`
- **Calls connect but no audio** → most likely OpenAI key issue; check the "OpenAI Realtime" lines in logs
