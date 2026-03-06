# JARVIS

Personal assistant stack:

- `jarvis-web`: React + LiveKit frontend
- `jarvis-api`: token + dispatch + relay API
- `jarvis-agent`: LiveKit Python agent (n8n orchestrator)
- `jarvis-voice`: XTTS custom voice service (required for golden voice rule)

## Architecture

```text
Browser (React + LiveKit)
  -> LiveKit room (WebRTC)
    -> LiveKit Agent (Python)
      -> n8n webhook
      -> jarvis-voice (/synthesize) for custom JARVIS voice
```

## n8n contract

Agent sends:

```json
{
  "user": { "name": "Isaac", "timezone": "America/Lima" },
  "query": "<original user message>",
  "routing": {
    "category": "planning|email|tasks|data|research|debug|other",
    "requires_approval": false,
    "risk_reason": ""
  },
  "context": { "timestamp_iso": "ISO-8601", "source": "chat" },
  "meta": { "room": "jarvis-room", "participant": "user_xxx", "origin": "livekit-agent" }
}
```

n8n should return JSON containing `output`.

## Required env (no secrets in git)

Use `.env.example` as template.

Required for all core services:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `N8N_WEBHOOK_URL`
- `N8N_RESPONSE_FIELD=output`
- `VOICE_SERVICE_URL` (jarvis-voice URL)

Required for custom voice:

- `CUSTOM_VOICE_LANG=es`
- `CUSTOM_VOICE_WAV_URL=<direct public wav url>`

## Local run

```bash
npm install
npm run server
npm run dev
```

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r agent/requirements.txt
python agent/agent.py dev
```

```bash
pip install -r voice_service/requirements.txt
uvicorn voice_service.app:app --host 0.0.0.0 --port 8000
```

## Railway

Create 4 services from this repo:

1. `jarvis-web` -> `Dockerfile.web`
2. `jarvis-api` -> `server/Dockerfile`
3. `jarvis-agent` -> `agent/Dockerfile`
4. `jarvis-voice` -> `voice_service/Dockerfile`

Then set service variables in Railway UI:

- `jarvis-api`: LiveKit keys + URL, `N8N_WEBHOOK_URL`
- `jarvis-agent`: LiveKit keys + URL, `N8N_WEBHOOK_URL`, `VOICE_SERVICE_URL`, `CUSTOM_VOICE_LANG=es`, `CUSTOM_VOICE_WAV_URL`, `AGENT_SYSTEM_PROMPT_FILE=agent/system_prompt_es.md`
- `jarvis-voice`: `CUSTOM_VOICE_LANG=es`, `CUSTOM_VOICE_WAV_URL`, `COQUI_TOS_AGREED=1`
- `jarvis-web`: `VITE_API_BASE=<jarvis-api public URL>`

## Security

- `.env` must stay git-ignored.
- Never commit API keys, webhook URLs with credentials, or private tokens.
- Rotate secrets that were ever shared publicly.
