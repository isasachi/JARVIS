import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterable

import aiohttp
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, ModelSettings, RunContext, WorkerOptions, cli, function_tool
from livekit.agents.utils.audio import audio_frames_from_file

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger('jarvis_agent')

load_dotenv()

N8N_WEBHOOK_URL = os.getenv('N8N_WEBHOOK_URL', '').strip()
N8N_RESPONSE_FIELD = os.getenv('N8N_RESPONSE_FIELD', 'output').strip() or 'output'

STT_MODEL = os.getenv('FREE_STT_MODEL', 'deepgram/nova-3:multi')
LLM_MODEL = os.getenv('FREE_LLM_MODEL', 'openai/gpt-4o-mini')
SESSION_TTS_MODEL = os.getenv('FREE_TTS_MODEL', 'cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc')

VOICE_SERVICE_URL = os.getenv('VOICE_SERVICE_URL', '').strip().rstrip('/')
CUSTOM_VOICE_LANG = os.getenv('CUSTOM_VOICE_LANG', 'es').strip() or 'es'
VOICE_SERVICE_TIMEOUT = int(os.getenv('VOICE_SERVICE_TIMEOUT', '20'))
VOICE_READY_TIMEOUT = int(os.getenv('VOICE_READY_TIMEOUT', '120'))
VOICE_READY_POLL_SECONDS = float(os.getenv('VOICE_READY_POLL_SECONDS', '2'))

USER_NAME = os.getenv('JARVIS_USER_NAME', 'Isaac')
USER_TIMEZONE = os.getenv('JARVIS_USER_TIMEZONE', 'America/Lima')


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

    if any(k in q for k in ('agenda', 'calendario', 'plan', 'programa', 'horario', 'reunion')):
        return 'planning'
    if any(k in q for k in ('email', 'correo', 'mail', 'gmail', 'enviarle', 'escribile', 'escribele')):
        return 'email'
    if any(k in q for k in ('tarea', 'recordatorio', 'pendiente', 'to-do', 'todo')):
        return 'tasks'
    if any(k in q for k in ('tabla', 'database', 'base de datos', 'registro', 'crud', 'sql')):
        return 'data'
    if any(k in q for k in ('investiga', 'buscar', 'busca', 'compara', 'resumen', 'research')):
        return 'research'
    if any(k in q for k in ('error', 'falla', 'debug', 'bug', 'trace', 'log', 'diagnostico')):
        return 'debug'
    return 'other'


def _risk_for_query(query: str) -> tuple[bool, str]:
    q = query.lower()

    if any(k in q for k in ('correo', 'email', 'mail', 'enviar email', 'enviar correo')):
        return True, 'send_email'
    if any(k in q for k in ('elimina', 'borrar', 'borra', 'delete', 'sobrescribe', 'reemplaza')):
        return True, 'data_deletion'
    if any(k in q for k in ('pagar', 'compra', 'comprar', 'transferir', 'suscribir', 'suscripcion')):
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

            logger.info('n8n response status=%s', response.status)
            return {
                'ok': 200 <= response.status < 300,
                'status': response.status,
                'body': body,
            }


async def wait_for_voice_ready() -> None:
    if not VOICE_SERVICE_URL:
        raise RuntimeError('VOICE_SERVICE_URL is not configured for custom voice synthesis')

    deadline = time.monotonic() + max(1, VOICE_READY_TIMEOUT)
    last_error = 'voice service did not become ready'

    while time.monotonic() < deadline:
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f'{VOICE_SERVICE_URL}/health') as response:
                    if 200 <= response.status < 300:
                        payload = await response.json()
                        if payload.get('ok'):
                            logger.info('voice ready model=%s language=%s', payload.get('model'), payload.get('language'))
                            return
                        last_error = payload.get('error') or 'voice service reported not ready'
                    else:
                        last_error = f'health status {response.status}'
        except Exception as exc:
            last_error = str(exc)

        await asyncio.sleep(max(0.5, VOICE_READY_POLL_SECONDS))

    raise RuntimeError(f'custom voice service not ready: {last_error}')


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
        remaining = max(1, int(deadline - time.monotonic()))
        request_timeout = aiohttp.ClientTimeout(total=min(12, remaining))

        async with aiohttp.ClientSession(timeout=request_timeout) as session:
            async with session.post(f'{VOICE_SERVICE_URL}/synthesize', json=payload) as response:
                if 200 <= response.status < 300:
                    audio_bytes = await response.read()
                    logger.info('voice synth ok chars=%s bytes=%s attempt=%s', len(text), len(audio_bytes), attempt)
                    tmp_file = tempfile.NamedTemporaryFile(prefix='jarvis_tts_', suffix='.wav', delete=False)
                    tmp_file.write(audio_bytes)
                    tmp_file.flush()
                    tmp_path = tmp_file.name
                    tmp_file.close()
                    return tmp_path

                detail = await response.text()
                transient = response.status in (502, 503, 504)
                can_retry = transient and time.monotonic() < deadline and attempt < 2
                logger.warning('voice synth failed status=%s attempt=%s detail=%s', response.status, attempt, detail[:200])
                if can_retry:
                    await asyncio.sleep(2)
                    continue

                raise RuntimeError(f'custom voice synthesis failed: {response.status} {detail}')


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
            logger.info('tts_node skipped empty content')
            return

        logger.info('tts_node content chars=%s', len(content))
        tmp_path: str | None = None
        try:
            tmp_path = await synthesize_custom_voice(content)
            async for frame in audio_frames_from_file(tmp_path):
                yield frame
        except Exception as exc:
            logger.exception('tts_node failed: %s', exc)
            raise RuntimeError(f'custom voice only mode failed: {exc}') from exc
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @function_tool()
    async def route_to_n8n(self, context: RunContext, user_request: str) -> str:
        query = user_request if isinstance(user_request, str) else ''
        normalized = _normalize_text(query)
        category = _classify_query(normalized)
        requires_approval, risk_reason = _risk_for_query(normalized)

        logger.info('route_to_n8n called chars=%s category=%s approval=%s', len(query), category, requires_approval)

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
    logger.info('entrypoint start room=%s', ctx.room.name)
    await wait_for_voice_ready()
    await ctx.connect()

    session = AgentSession(
        stt=STT_MODEL,
        llm=LLM_MODEL,
        tts=SESSION_TTS_MODEL,
    )

    agent = JarvisAgent()

    await session.start(room=ctx.room, agent=agent)
    logger.info('session started, sending welcome')
    await session.say('Bienvenido señor, ¿qué haremos hoy?')


if __name__ == '__main__':
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=os.getenv('AGENT_NAME', 'jarvis-agent'),
        )
    )
