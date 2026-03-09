"""
J.A.R.V.I.S. — LiveKit Voice Agent
===================================
Capa de voz para el asistente personal JARVIS.

Arquitectura:
  Audio (WebRTC) → Deepgram STT → DeepSeek V3 → XTTS (RunPod) → Audio

Setup:
  pip install -r requirements.txt
  cp .env.example .env  # y rellena las variables
  python jarvis_agent.py dev   # desarrollo
  python jarvis_agent.py start # producción
"""

import asyncio
import io
import logging
import os
from typing import Annotated

import httpx
import soundfile as sf
from dotenv import load_dotenv

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.agents import tts as agents_tts
from livekit.plugins import deepgram, silero

load_dotenv()

logger = logging.getLogger("jarvis-agent")
logger.setLevel(logging.INFO)

JARVIS_WEBHOOK_URL = os.environ.get(
    "JARVIS_WEBHOOK_URL",
    "https://n8n-production-1704.up.railway.app/webhook/n8n",
)
JARVIS_API_KEY = os.environ.get("JARVIS_API_KEY", "")
XTTS_API_URL = os.environ.get("XTTS_API_URL", "")
XTTS_API_KEY = os.environ.get("XTTS_API_KEY", "")
XTTS_LANGUAGE = os.environ.get("XTTS_LANGUAGE", "es")


class JARVISTools(llm.FunctionContext):
    """Define las herramientas que el LLM puede usar."""

    @llm.ai_callable(
        description=(
            "Ejecuta cualquier acción de productividad: "
            "enviar emails, crear/ver eventos en calendario, "
            "gestionar tareas, registrar gastos o ingresos, "
            "buscar contactos, crear contenido, buscar en internet. "
            "Pasa la instrucción completa del usuario como 'query'."
        )
    )
    async def call_jarvis(
        self,
        query: Annotated[
            str,
            llm.TypeInfo(
                description="La instrucción completa del usuario en lenguaje natural. "
                            "Sé específico: incluye nombres, fechas, montos y cualquier detalle relevante."
            ),
        ],
    ) -> str:
        """Llama al orquestador JARVIS en n8n y devuelve la respuesta."""
        logger.info(f"[JARVIS Tool] Sending query: {query[:80]}...")

        headers = {
            "Content-Type": "application/json",
            "x-api-key": JARVIS_API_KEY,
        }
        payload = {"query": query}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    JARVIS_WEBHOOK_URL,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

                result = (
                    data.get("output")
                    or data.get("response")
                    or data.get("message")
                    or str(data)
                )
                logger.info(f"[JARVIS Tool] Response received: {str(result)[:80]}...")
                return result

        except httpx.TimeoutException:
            logger.error("[JARVIS Tool] Request timed out")
            return "La operación tardó demasiado. Intenta de nuevo."
        except httpx.HTTPStatusError as e:
            logger.error(f"[JARVIS Tool] HTTP error: {e.response.status_code}")
            if e.response.status_code == 401:
                return "Error de autenticación con JARVIS. Verifica la API key."
            return f"Error al ejecutar la acción (código {e.response.status_code}). Intenta de nuevo."
        except Exception as e:
            logger.error(f"[JARVIS Tool] Unexpected error: {e}")
            return "Ocurrió un error inesperado. Intenta de nuevo."


class XTTSChunkedStream(agents_tts.ChunkedStream):
    def __init__(self, tts, input_text: str, url: str, api_key: str, language: str):
        super().__init__(tts=tts, input_text=input_text)
        self._url = url
        self._api_key = api_key
        self._language = language

    async def _run(self):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._url}/tts",
                    headers={"X-API-Key": self._api_key},
                    json={"text": self._input_text, "language": self._language},
                )
                response.raise_for_status()

                audio_data, sample_rate = sf.read(
                    io.BytesIO(response.content), dtype="float32"
                )

                if audio_data.ndim == 1:
                    num_channels = 1
                else:
                    num_channels = audio_data.shape[1]

                audio_bytes = audio_data.tobytes()

                await self._event_ch.send(
                    agents_tts.SynthesizedAudio(
                        request_id="xtts",
                        segment_id="0",
                        audio=agents_tts.AudioFrame(
                            data=audio_bytes,
                            sample_rate=sample_rate,
                            num_channels=num_channels,
                            samples_per_channel=len(audio_bytes)
                            // (num_channels * 4),
                        ),
                    )
                )
        except Exception as e:
            logger.error(f"[XTTS] Error synthesizing audio: {e}")
            raise


class XTTSPlugin(agents_tts.TTS):
    def __init__(self):
        super().__init__(capabilities=agents_tts.TTSCapabilities(streaming=False))
        self._url = os.environ["XTTS_API_URL"]
        self._api_key = os.environ["XTTS_API_KEY"]
        self._language = os.environ.get("XTTS_LANGUAGE", "es")

    def synthesize(self, text: str) -> agents_tts.ChunkedStream:
        return XTTSChunkedStream(
            tts=self,
            input_text=text,
            url=self._url,
            api_key=self._api_key,
            language=self._language,
        )


def get_tts():
    """Retorna el plugin TTS a usar."""
    if XTTS_API_URL and XTTS_API_KEY:
        logger.info(f"Using XTTS at {XTTS_API_URL} (language: {XTTS_LANGUAGE})")
        return XTTSPlugin()
    else:
        logger.warning(
            "XTTS not configured. TTS will not work. "
            "Set XTTS_API_URL and XTTS_API_KEY environment variables."
        )
        return None


def get_llm():
    """Retorna el plugin LLM a usar (DeepSeek V3)."""
    from livekit.plugins import openai

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not deepseek_key:
        raise ValueError("DEEPSEEK_API_KEY environment variable is required")

    logger.info("Using DeepSeek V3 (model: deepseek-chat)")
    return openai.LLM(
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key=deepseek_key,
        temperature=0.3,
    )


async def entrypoint(ctx: JobContext):
    """Punto de entrada cuando un participante se une a la sala LiveKit."""

    logger.info(f"[JARVIS] New job: room={ctx.room.name}")

    initial_ctx = llm.ChatContext().append(
        role="system",
        text=(
            "Eres J.A.R.V.I.S. (Just A Rather Very Intelligent System), "
            "el asistente personal de voz de tu usuario. "
            "Tu personalidad es eficiente, sofisticado y ligeramente formal, "
            "similar al JARVIS de Iron Man — inteligente, proactivo y preciso.\n\n"
            "## Cómo operar\n"
            "- Cuando el usuario te pida ejecutar una acción (enviar email, agendar, "
            "crear tarea, registrar gasto, etc.), SIEMPRE usa la herramienta `call_jarvis`.\n"
            "- Para preguntas conversacionales simples, responde directamente sin usar tools.\n"
            "- Mantén las respuestas de voz CORTAS y NATURALES — esto es audio, no texto.\n"
            "- Confirma las acciones de forma concisa: '✓ Email enviado a María.' "
            "en lugar de descripciones largas.\n"
            "- Habla en el idioma en que el usuario te hable (español por defecto).\n"
            "- Si necesitas aclaración antes de ejecutar una acción sensible "
            "(como enviar un email), pídela brevemente.\n\n"
            "## Frases de bienvenida\n"
            "Al conectarse el usuario, salúdalo con algo como: "
            "'Buenas, ¿en qué puedo ayudarte?' o "
            "'JARVIS en línea. ¿Qué necesitas?'"
        ),
    )

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"[JARVIS] Participant joined: {participant.identity}")

    agent = VoicePipelineAgent(
        vad=silero.VAD.load(),
        stt=deepgram.STT(
            model="nova-2",
            language="es",
            punctuate=True,
        ),
        llm=get_llm(),
        tts=get_tts(),
        fnc_ctx=JARVISTools(),
        chat_ctx=initial_ctx,
        allow_interruptions=True,
        interrupt_speech_duration=0.5,
        interrupt_min_words=0,
        will_greet=True,
    )

    agent.start(ctx.room, participant)

    await agent.say(
        "JARVIS en línea. ¿En qué puedo ayudarte?",
        allow_interruptions=True,
    )

    logger.info("[JARVIS] Agent started successfully")


if __name__ == "__main__":
    logger.info("Starting JARVIS Agent...")
    logger.info(f"LLM: DeepSeek V3 (deepseek-chat)")
    logger.info(f"TTS: {'XTTS (' + XTTS_API_URL + ')' if XTTS_API_URL else 'Not configured'}")
    logger.info(f"STT: Deepgram (nova-2, Spanish)")
    
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            num_idle_processes=1,
        )
    )
