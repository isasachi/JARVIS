import os
import asyncio
import json
import uuid
import logging
from pathlib import Path

import httpx
from TTS.api import TTS
import torch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

REFERENCE_AUDIO_URL = os.getenv("REFERENCE_AUDIO_URL")
LANGUAGE = os.getenv("LANGUAGE", "es")
API_KEY = os.getenv("API_KEY")

reference_audio_path = "/tmp/reference_voice.wav"
tts_model = None
is_gpu_available = False
rate_limiter = asyncio.Semaphore(10)


def download_reference_audio():
    if not REFERENCE_AUDIO_URL:
        raise ValueError("REFERENCE_AUDIO_URL environment variable is required")

    logger.info(f"Downloading reference audio from {REFERENCE_AUDIO_URL}")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(REFERENCE_AUDIO_URL)
            response.raise_for_status()
            Path(reference_audio_path).write_bytes(response.content)
        logger.info(f"Reference audio saved to {reference_audio_path}")
    except Exception as e:
        raise RuntimeError(f"Failed to download reference audio: {e}")


def validate_reference_audio():
    try:
        import wave
        with wave.open(reference_audio_path, "rb") as wf:
            channels = wf.getnchannels()
            framerate = wf.getframerate()
            frames = wf.getnframes()
            duration = frames / framerate
            logger.info(f"Reference audio: {channels} channel(s), {framerate} Hz, {duration:.2f}s")
            if channels != 1:
                logger.warning("Reference audio is not mono. XTTS works best with mono audio.")
            if framerate != 22050:
                logger.warning(f"Reference audio sample rate is {framerate} Hz. XTTS works best with 22050 Hz.")
            if duration < 6:
                logger.warning(f"Reference audio is only {duration:.2f}s long. XTTS works best with at least 6 seconds.")
    except Exception as e:
        logger.warning(f"Could not validate reference audio: {e}")


def initialize_model():
    global tts_model, is_gpu_available
    logger.info("Initializing XTTS-v2 model...")

    try:
        tts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
    except Exception as e:
        logger.error(f"Failed to load XTTS model: {e}")
        raise RuntimeError(f"Failed to load XTTS model: {e}")

    is_gpu_available = torch.cuda.is_available()
    if is_gpu_available:
        tts_model.to("cuda")
        logger.info("Model moved to CUDA GPU")
    else:
        logger.warning("No GPU available, using CPU")

    if is_gpu_available and torch.cuda.is_available():
        with torch.cuda.amp.autocast():
            tts_model.tts_to_file(
                text="Hola",
                speaker_wav=reference_audio_path,
                language=LANGUAGE,
                file_path="/tmp/warmup.wav"
            )
    else:
        tts_model.tts_to_file(
            text="Hola",
            speaker_wav=reference_audio_path,
            language=LANGUAGE,
            file_path="/tmp/warmup.wav"
        )
    logger.info("XTTS service ready for RunPod Serverless")


def synthesize_speech(text: str, language: str, output_path: str):
    if is_gpu_available and torch.cuda.is_available():
        with torch.cuda.amp.autocast():
            tts_model.tts_to_file(
                text=text,
                speaker_wav=reference_audio_path,
                language=language,
                file_path=output_path
            )
    else:
        tts_model.tts_to_file(
            text=text,
            speaker_wav=reference_audio_path,
            language=language,
            file_path=output_path
        )


async def handler(event, context):
    global tts_model, is_gpu_available

    if tts_model is None:
        download_reference_audio()
        validate_reference_audio()
        initialize_model()

    route = event.get("route", "")
    method = event.get("method", "GET")
    headers = event.get("headers", {})
    body = event.get("body", "")

    if route == "/health" and method == "GET":
        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "ok",
                "model": "xtts_v2",
                "language": LANGUAGE,
                "gpu": is_gpu_available
            }),
            "headers": {"Content-Type": "application/json"}
        }

    if route == "/tts" and method == "POST":
        api_key = headers.get("x-api-key", "")
        if api_key != API_KEY:
            return {
                "statusCode": 401,
                "body": json.dumps({"detail": "Invalid API key"}),
                "headers": {"Content-Type": "application/json"}
            }

        try:
            request_data = json.loads(body)
        except json.JSONDecodeError:
            return {
                "statusCode": 400,
                "body": json.dumps({"detail": "Invalid JSON body"}),
                "headers": {"Content-Type": "application/json"}
            }

        text = request_data.get("text", "")
        language = request_data.get("language", LANGUAGE)

        if not text or not text.strip():
            return {
                "statusCode": 400,
                "body": json.dumps({"detail": "Text cannot be empty"}),
                "headers": {"Content-Type": "application/json"}
            }

        if len(text) > 500:
            return {
                "statusCode": 400,
                "body": json.dumps({"detail": "Text cannot exceed 500 characters"}),
                "headers": {"Content-Type": "application/json"}
            }

        async with rate_limiter:
            output_file = f"/tmp/output_{uuid.uuid4()}.wav"

            try:
                loop = asyncio.get_event_loop()
                await asyncio.wait_for(
                    loop.run_in_executor(None, synthesize_speech, text, language, output_file),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                return {
                    "statusCode": 504,
                    "body": json.dumps({"detail": "Synthesis timeout exceeded"}),
                    "headers": {"Content-Type": "application/json"}
                }

            with open(output_file, "rb") as f:
                audio_data = f.read()

            try:
                os.remove(output_file)
            except Exception:
                pass

            import base64
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")

            return {
                "statusCode": 200,
                "body": json.dumps({"audio": audio_b64}),
                "headers": {"Content-Type": "application/json"}
            }

    return {
        "statusCode": 404,
        "body": json.dumps({"detail": "Not found"}),
        "headers": {"Content-Type": "application/json"}
    }
