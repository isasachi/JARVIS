import json
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, RunContext, WorkerOptions, cli, function_tool

load_dotenv()

N8N_WEBHOOK_URL = os.getenv('N8N_WEBHOOK_URL', '').strip()
N8N_RESPONSE_FIELD = os.getenv('N8N_RESPONSE_FIELD', 'output').strip() or 'output'


def _load_system_prompt() -> str:
    env_prompt = os.getenv('AGENT_SYSTEM_PROMPT', '').strip()
    if env_prompt:
        return env_prompt

    prompt_file = os.getenv('AGENT_SYSTEM_PROMPT_FILE', '').strip()
    if prompt_file:
        path = Path(prompt_file)
    else:
        path = Path(__file__).with_name('system_prompt_es.md')

    try:
        return path.read_text(encoding='utf-8').strip()
    except Exception:
        return (
            'You are JARVIS. Be concise, accurate, and action-oriented. '
            'You must use the route_to_n8n tool for every user task before responding.'
        )


SYSTEM_PROMPT = _load_system_prompt()

VOICE_MODE = os.getenv('VOICE_MODE', 'free').strip().lower()

FREE_STT_MODEL = os.getenv('FREE_STT_MODEL', 'local/whisper')
FREE_LLM_MODEL = os.getenv('FREE_LLM_MODEL', 'openai/gpt-4.1-mini')
FREE_TTS_MODEL = os.getenv('FREE_TTS_MODEL', 'local/piper')

MINIMAX_STT_MODEL = os.getenv('MINIMAX_STT_MODEL', 'deepgram/nova-3:en')
MINIMAX_LLM_MODEL = os.getenv('MINIMAX_LLM_MODEL', 'openai/gpt-4.1-mini')
MINIMAX_TTS_MODEL = os.getenv('MINIMAX_TTS_MODEL', 'speech-2.8-hd')


def _read_identity(context: RunContext) -> str | None:
    try:
        session = getattr(context, 'session', None)
        room_io = getattr(session, 'room_io', None)
        participant = getattr(room_io, 'linked_participant', None)
        return getattr(participant, 'identity', None)
    except Exception:
        return None


def _read_room_name(context: RunContext) -> str | None:
    try:
        session = getattr(context, 'session', None)
        room = getattr(session, 'room', None)
        return getattr(room, 'name', None)
    except Exception:
        return None


def _extract_n8n_text(body: object) -> str:
    if body is None:
        return 'Done.'

    if isinstance(body, str):
        return body.strip() or 'Done.'

    if isinstance(body, dict):
        preferred = body.get(N8N_RESPONSE_FIELD)
        if isinstance(preferred, str) and preferred.strip():
            return preferred.strip()

        for key in ('output', 'reply', 'message', 'text'):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        raw = body.get('raw')
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

        if raw == '':
            return 'Done.'

    if isinstance(body, list) and len(body) == 0:
        return 'Done.'

    return json.dumps(body, ensure_ascii=True)


async def post_to_n8n(payload: dict) -> dict:
    if not N8N_WEBHOOK_URL:
        return {'ok': False, 'error': 'N8N_WEBHOOK_URL is not configured'}

    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(N8N_WEBHOOK_URL, json=payload) as response:
            text = await response.text()
            content_type = response.headers.get('content-type', '')

            if 'application/json' in content_type:
                try:
                    body = json.loads(text)
                except json.JSONDecodeError:
                    body = {'raw': text}
            else:
                body = text

            return {
                'ok': 200 <= response.status < 300,
                'status': response.status,
                'body': body,
            }


def resolve_models() -> tuple[str, str, str]:
    if VOICE_MODE == 'minimax':
        return (MINIMAX_STT_MODEL, MINIMAX_LLM_MODEL, MINIMAX_TTS_MODEL)

    return (FREE_STT_MODEL, FREE_LLM_MODEL, FREE_TTS_MODEL)


class JarvisAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

    @function_tool()
    async def route_to_n8n(self, context: RunContext, user_request: str) -> str:
        """Send user intent to n8n for orchestration and return a normalized response text.

        Args:
            user_request: The user task in plain language.
        """

        payload = {
            'source': 'livekit-agent',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'room': _read_room_name(context),
            'participant': _read_identity(context),
            'query': user_request,
        }

        result = await post_to_n8n(payload)
        if not result['ok']:
            return f"n8n orchestration failed: {result.get('status', 'unknown')}, {result.get('body')}"

        return _extract_n8n_text(result.get('body'))


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    stt_model, llm_model, tts_model = resolve_models()

    session = AgentSession(
        stt=stt_model,
        llm=llm_model,
        tts=tts_model,
    )

    agent = JarvisAgent()

    await session.start(room=ctx.room, agent=agent)
    await session.generate_reply(
        instructions='Greet the user in one short sentence and say you are ready to execute tasks through n8n.'
    )


if __name__ == '__main__':
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=os.getenv('AGENT_NAME', 'jarvis-agent'),
        )
    )
