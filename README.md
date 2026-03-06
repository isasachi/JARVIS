# JARVIS

Personal assistant stack with:

- React + LiveKit web client
- LiveKit agent worker (Python)
- n8n orchestration backend
- Optional free custom voice service (XTTS)

## Architecture

```text
Browser (React + LiveKit frontend)
  -> LiveKit room (WebRTC)
    -> LiveKit Agent (Python)
      -> n8n webhook (query in, output out)
  -> API service (token mint + relay/proxy)
  -> Voice service (optional XTTS clone)
```

## n8n contract

Agent sends:

```json
{
  "source": "livekit-agent",
  "timestamp": "ISO-8601",
  "room": "jarvis-room",
  "participant": "user_xxx",
  "query": "user request"
}
```

n8n must return:

```json
{ "output": "Text JARVIS should say" }
```

`N8N_RESPONSE_FIELD` defaults to `output`.

## Services

- `jarvis-web`: frontend (`Dockerfile.web`)
- `jarvis-api`: token API + relay/proxy (`server/Dockerfile`)
- `jarvis-agent`: LiveKit worker (`agent/Dockerfile`)
- `jarvis-voice` (optional): XTTS custom voice (`voice_service/Dockerfile`)

## Environment

Never commit real secrets. Use `.env.example` as template.

1. Copy:

```bash
cp .env.example .env
```

2. Fill required values:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `N8N_WEBHOOK_URL`
- `N8N_RESPONSE_FIELD=output`

3. For free custom voice mode:

- `VOICE_MODE=free`
- `CUSTOM_VOICE_LANG=es`
- `CUSTOM_VOICE_WAV_URL=<public direct wav url>`
- `VOICE_SERVICE_URL=<voice service url>`

## Local run

Frontend/API:

```bash
npm install
npm run server
npm run dev
```

Agent:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r agent/requirements.txt
python agent/agent.py dev
```

Optional voice service:

```bash
pip install -r voice_service/requirements.txt
uvicorn voice_service.app:app --host 0.0.0.0 --port 8000
```

## Docker

```bash
docker compose up --build -d
```

## Railway deployment

Create Railway services from this repo:

1. `jarvis-web` -> `Dockerfile.web`
2. `jarvis-api` -> `server/Dockerfile`
3. `jarvis-agent` -> `agent/Dockerfile`
4. `jarvis-voice` -> `voice_service/Dockerfile` (optional)

Set env vars per service in Railway UI. Do not hardcode secrets in code or docs.

## Security checklist

- `.env` is ignored by git.
- Rotate any credentials that were ever shared in chat or committed before.
- Keep webhook URLs and API keys only in platform secrets.
