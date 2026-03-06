import json
import os
import tempfile
import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterable

import aiohttp
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, ModelSettings, RunContext, WorkerOptions, cli, function_tool
from livekit.agents.utils.audio import audio_frames_from_file

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
            'Eres J.A.R.V.I.S. Responde en espanol, formal, breve y preciso. '
            'Trata al usuario como Senor y usa route_to_n8n para enrutar cada tarea.'
        )


SYSTEM_PROMPT = _load_system_prompt()

VOICE_MODE = os.getenv('VOICE_MODE', 'free').strip().lower()

FREE_STT_MODEL = os.getenv('FREE_STT_MODEL', 'deepgram/nova-3:multi')
FREE_LLM_MODEL = os.getenv('FREE_LLM_MODEL', 'openai/gpt-4o-mini')
FREE_TTS_MODEL = os.getenv('FREE_TTS_MODEL', 'cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc')

MINIMAX_STT_MODEL = os.getenv('MINIMAX_STT_MODEL', 'deepgram/nova-3:multi')
MINIMAX_LLM_MODEL = os.getenv('MINIMAX_LLM_MODEL', 'openai/gpt-4o-mini')
MINIMAX_TTS_MODEL = os.getenv('MINIMAX_TTS_MODEL', 'speech-2.8-hd')

VOICE_SERVICE_URL = os.getenv('VOICE_SERVICE_URL', '').strip().rstrip('/')
CUSTOM_VOICE_LANG = os.getenv('CUSTOM_VOICE_LANG', 'es').strip() or 'es'
CUSTOM_VOICE_WAV_URL = os.getenv('CUSTOM_VOICE_WAV_URL', '').strip()
VOICE_SERVICE_TIMEOUT = int(os.getenv('VOICE_SERVICE_TIMEOUT', '180'))

USER_NAME = os.getenv('JARVIS_USER_NAME', 'Isaac')
USER_TIMEZONE = os.getenv('JARVIS_USER_TIMEZONE', 'America/Lima')


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


def _normalize_text(text: str) -> str:
    return (text or '').strip()


def _classify_query(query: str) -> str:
    q = query.lower()

    if any(k in q for k in ('agenda', 'calendario', 'plan', 'programa', 'horario', 'reunion', 'reunion')):
        return 'planning'
    if any(k in q for k in ('email', 'correo', 'mail', 'gmail', 'enviarle', 'escribile', 'escribele')):
        return 'email'
    if any(k in q for k in ('tarea', 'recordatorio', 'pendiente', 'to-do', 'todo')):
        return 'tasks'
    if any(k in q for k in ('tabla', 'database', 'base de datos', 'registro', 'crud', 'sql')):
        return 'data'
    if any(k in q for k in ('investiga', 'buscar', 'busca', 'compara', 'resumen', 'research')):
        return 'research'
    if any(k in q for k in ('error', 'falla', 'debug', 'bug', 'trace', 'log', 'diagnostico', 'diagnostico')):
        return 'debug'
    return 'other'


def _risk_for_query(query: str) -> tuple[bool, str]:
    q = query.lower()

    if any(k in q for k in ('correo', 'email', 'mail', 'enviar email', 'enviar correo')):
        return True, 'send_email'
    if any(k in q for k in ('elimina', 'borrar', 'borra', 'delete', 'sobrescribe', 'reemplaza')):
        return True, 'data_deletion'
    if any(k in q for k in ('pagar', 'compra', 'comprar', 'transferir', 'suscribir', 'suscripcion', 'suscripcion')):
        return True, 'financial_action'
    if any(k in q for k in ('masivo', 'en lote', 'bulk', 'todos los', 'todas las')):
        return True, 'mass_operation'

    return False, ''


def _extract_n8n_text(body: object) -> str:
    if body is None:
        return 'Hecho, Senor.'

    if isinstance(body, str):
        return body.strip() or 'Hecho, Senor.'

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
            return 'Hecho, Senor.'

    if isinstance(body, list) and len(body) == 0:
        return 'Hecho, Senor.'

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


async def synthesize_custom_voice(text: str) -> str:
    if not VOICE_SERVICE_URL:
        raise RuntimeError('VOICE_SERVICE_URL is not configured for custom voice synthesis')

    payload = {
        'text': text,
        'language': CUSTOM_VOICE_LANG,
    }

    deadline = time.monotonic() + VOICE_SERVICE_TIMEOUT
    attempt = 0

    while True:
        attempt += 1
        request_timeout = aiohttp.ClientTimeout(total=min(90, max(20, int(deadline - time.monotonic()))))
        async with aiohttp.ClientSession(timeout=request_timeout) as session:
            async with session.post(f'{VOICE_SERVICE_URL}/synthesize', json=payload) as response:
                if 200 <= response.status < 300:
                    audio_bytes = await response.read()
                    tmp_file = tempfile.NamedTemporaryFile(prefix='jarvis_tts_', suffix='.wav', delete=False)
                    tmp_file.write(audio_bytes)
                    tmp_file.flush()
                    tmp_path = tmp_file.name
                    tmp_file.close()
                    return tmp_path

                detail = await response.text()
                transient = response.status in (502, 503, 504)
                if transient and time.monotonic() < deadline:
                    await asyncio.sleep(min(12, 2 + attempt * 2))
                    continue

                raise RuntimeError(f'custom voice synthesis failed: {response.status} {detail}')


async def _single_text_stream(content: str):
    yield content


def resolve_models() -> tuple[str, str, str]:
    if VOICE_MODE == 'minimax':
        return (MINIMAX_STT_MODEL, MINIMAX_LLM_MODEL, MINIMAX_TTS_MODEL)

    return (FREE_STT_MODEL, FREE_LLM_MODEL, FREE_TTS_MODEL)


class JarvisAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        chunks: list[str] = []
        async for chunk in text:
            if chunk:
                chunks.append(chunk)

        content = _normalize_text(''.join(chunks))
        if not content:
            return

        if not VOICE_SERVICE_URL:
            async for frame in Agent.default.tts_node(self, _single_text_stream(content), model_settings):
                yield frame
            return

        tmp_path: str | None = None
        try:
            tmp_path = await synthesize_custom_voice(content)
            async for frame in audio_frames_from_file(tmp_path):
                yield frame
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @function_tool()
    async def route_to_n8n(self, context: RunContext, user_request: str) -> str:
        query = _normalize_text(user_request)
        category = _classify_query(query)
        requires_approval, risk_reason = _risk_for_query(query)

        payload = {
            'user': {
                'name': USER_NAME,
                'timezone': USER_TIMEZONE,
            },
            'query': query,
            'routing': {
                'category': category,
                'requires_approval': requires_approval,
                'risk_reason': risk_reason if requires_approval else '',
            },
            'context': {
                'timestamp_iso': datetime.now(timezone.utc).isoformat(),
                'source': 'chat',
            },
            'meta': {
                'room': _read_room_name(context),
                'participant': _read_identity(context),
                'origin': 'livekit-agent',
            },
        }

        result = await post_to_n8n(payload)
        if not result['ok']:
            return f"Entendido, Senor. Fallo al enrutar a n8n: {result.get('status', 'unknown')}."

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
    await session.say('Bienvenido señor, ¿qué haremos hoy?')


if __name__ == '__main__':
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=os.getenv('AGENT_NAME', 'jarvis-agent'),
        )
    )


