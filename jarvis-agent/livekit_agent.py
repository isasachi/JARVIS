"""
J.A.R.V.I.S. — LiveKit Voice Agent
====================================
Capa de voz para el asistente personal JARVIS.

Arquitectura:
  Audio (WebRTC) → Deepgram STT → GPT-4o (con tool: call_jarvis) → TTS → Audio

Setup:
  pip install -r requirements.txt
  cp .env.example .env  # y rellena las variables
  python livekit_agent.py dev   # desarrollo
  python livekit_agent.py start # producción
"""

import asyncio
import logging
import os
from typing import Annotated

import httpx
from dotenv import load_dotenv

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import deepgram, openai, silero

load_dotenv()

logger = logging.getLogger("jarvis-agent")
logger.setLevel(logging.INFO)

# ─────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────

JARVIS_WEBHOOK_URL = os.environ.get(
    "JARVIS_WEBHOOK_URL",
    "https://n8n-production-1704.up.railway.app/webhook/n8n",
)
JARVIS_API_KEY = os.environ.get("JARVIS_API_KEY", "")
XTTS_API_URL = os.environ.get("XTTS_API_URL", "")  # vacío = usar OpenAI TTS
XTTS_LANGUAGE = os.environ.get("XTTS_LANGUAGE", "es")


# ─────────────────────────────────────────────
# Tool: Llamar a JARVIS en n8n
# ─────────────────────────────────────────────

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

                # n8n puede devolver el output en diferentes estructuras
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


# ─────────────────────────────────────────────
# TTS personalizado con XTTS (opcional)
# ─────────────────────────────────────────────

def get_tts():
    """
    Retorna el plugin TTS a usar.
    Si XTTS_API_URL está configurado, usa XTTS vía HTTP.
    Si no, usa OpenAI TTS (tts-1-hd) como fallback.
    """
    if XTTS_API_URL:
        logger.info(f"Using XTTS at {XTTS_API_URL}")
        # Nota: livekit-agents no tiene plugin nativo de XTTS todavía.
        # Usa el plugin HTTP de OpenAI apuntando a XTTS si tu servidor
        # implementa la misma interfaz de API, o usa el adaptador custom
        # en la sección de comentarios al final de este archivo.
        # Por ahora retorna OpenAI TTS hasta que configures XTTS.
        return openai.TTS(
            model="tts-1-hd",
            voice="onyx",  # Voz más grave y autoritaria para JARVIS
        )
    else:
        logger.info("Using OpenAI TTS (tts-1-hd, voice: onyx)")
        return openai.TTS(
            model="tts-1-hd",
            voice="onyx",
        )


# ─────────────────────────────────────────────
# Entrypoint del agente
# ─────────────────────────────────────────────

async def entrypoint(ctx: JobContext):
    """Punto de entrada cuando un participante se une a la sala LiveKit."""

    logger.info(f"[JARVIS] New job: room={ctx.room.name}")

    # Contexto inicial del sistema para el LLM
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

    # Conectar a la sala y suscribirse solo a audio
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Esperar al primer participante humano
    participant = await ctx.wait_for_participant()
    logger.info(f"[JARVIS] Participant joined: {participant.identity}")

    # Crear el pipeline de voz
    agent = VoicePipelineAgent(
        vad=silero.VAD.load(),                          # Voice Activity Detection
        stt=deepgram.STT(                               # Speech-to-Text
            model="nova-2",
            language="es",                              # Español
            punctuate=True,
        ),
        llm=openai.LLM(                                 # Language Model
            model="gpt-4o",
            temperature=0.3,                            # Más determinista para acciones
        ),
        tts=get_tts(),                                  # Text-to-Speech
        fnc_ctx=JARVISTools(),                          # Herramientas disponibles
        chat_ctx=initial_ctx,
        # Configuración de interrupciones — permite que el usuario interrumpa
        allow_interruptions=True,
        interrupt_speech_duration=0.5,
        interrupt_min_words=0,
        # Mensaje inicial al conectarse
        will_greet=True,
    )

    agent.start(ctx.room, participant)

    # Saludo inicial
    await agent.say(
        "JARVIS en línea. ¿En qué puedo ayudarte?",
        allow_interruptions=True,
    )

    logger.info("[JARVIS] Agent started successfully")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            # Número de trabajos concurrentes — ajusta según recursos
            num_idle_processes=1,
        )
    )


# ─────────────────────────────────────────────
# NOTA: Integración XTTS personalizada
# ─────────────────────────────────────────────
#
# Para integrar XTTS-v2 con una voz clonada, reemplaza get_tts() con:
#
# from livekit.agents import tts as agents_tts
# import io
#
# class XTTSPlugin(agents_tts.TTS):
#     """Plugin TTS personalizado que usa el servidor XTTS-v2."""
#
#     def __init__(self):
#         super().__init__(
#             capabilities=agents_tts.TTSCapabilities(streaming=False)
#         )
#
#     def synthesize(self, text: str) -> agents_tts.ChunkedStream:
#         return XTTSChunkedStream(text)
#
#
# class XTTSChunkedStream(agents_tts.ChunkedStream):
#     def __init__(self, text: str):
#         super().__init__()
#         self._text = text
#
#     async def _run(self):
#         async with httpx.AsyncClient() as client:
#             response = await client.post(
#                 f"{XTTS_API_URL}/tts_to_audio",
#                 json={
#                     "text": self._text,
#                     "speaker_wav": "reference_voice",
#                     "language": XTTS_LANGUAGE,
#                 },
#                 timeout=30.0,
#             )
#             # XTTS devuelve WAV bytes
#             audio_bytes = response.content
#             self._event_ch.send_nowait(
#                 agents_tts.SynthesizedAudio(
#                     request_id="xtts",
#                     segment_id="0",
#                     audio=agents_tts.AudioFrame(
#                         data=audio_bytes,
#                         sample_rate=22050,
#                         num_channels=1,
#                         samples_per_channel=len(audio_bytes) // 2,
#                     ),
#                 )
#             )
