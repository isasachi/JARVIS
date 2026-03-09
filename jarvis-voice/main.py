import os
import asyncio
import logging
import uuid
import time
from pathlib import Path
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from TTS.api import TTS
import torch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

REFERENCE_AUDIO_URL = os.getenv("REFERENCE_AUDIO_URL")
LANGUAGE = os.getenv("LANGUAGE", "es")
PORT = int(os.getenv("PORT", "8000"))
API_KEY = os.getenv("API_KEY")

if not REFERENCE_AUDIO_URL:
    raise ValueError("REFERENCE_AUDIO_URL environment variable is required")

app = FastAPI(title="XTTS Voice API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rate_limiter = asyncio.Semaphore(10)
tts_model = None
is_gpu_available = False
reference_audio_path = "/tmp/reference_voice.wav"


class TTSRequest(BaseModel):
    text: str
    language: str | None = None


def download_reference_audio():
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
    logger.info("XTTS service ready")


@app.on_event("startup")
async def startup_event():
    download_reference_audio()
    validate_reference_audio()
    initialize_model()


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "model": "xtts_v2",
        "language": LANGUAGE,
        "gpu": is_gpu_available
    }


async def synthesize_speech(text: str, language: str, output_path: str):
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


@app.post("/tts")
async def text_to_speech(
    request: TTSRequest,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    if len(request.text) > 500:
        raise HTTPException(status_code=400, detail="Text cannot exceed 500 characters")

    language = request.language or LANGUAGE

    async with rate_limiter:
        start_time = time.time()
        logger.info(f"Request: text_length={len(request.text)}, language={language}")

        output_file = f"/tmp/output_{uuid.uuid4()}.wav"

        try:
            await asyncio.wait_for(
                synthesize_speech(request.text, language, output_file),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Synthesis timeout exceeded")

        synthesis_time = (time.time() - start_time) * 1000
        logger.info(f"Synthesis completed in {synthesis_time:.2f}ms")

        def iter_file():
            try:
                with open(output_file, "rb") as f:
                    while chunk := f.read(8192):
                        yield chunk
            finally:
                try:
                    os.remove(output_file)
                except Exception:
                    pass

        return StreamingResponse(
            iter_file(),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=speech.wav"}
        )


@app.post("/tts/stream")
async def text_to_speech_stream(
    request: TTSRequest,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    if len(request.text) > 500:
        raise HTTPException(status_code=400, detail="Text cannot exceed 500 characters")

    language = request.language or LANGUAGE

    async with rate_limiter:
        start_time = time.time()
        logger.info(f"Stream Request: text_length={len(request.text)}, language={language}")

        output_file = f"/tmp/output_stream_{uuid.uuid4()}.wav"

        try:
            await asyncio.wait_for(
                synthesize_speech(request.text, language, output_file),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Synthesis timeout exceeded")

        synthesis_time = (time.time() - start_time) * 1000
        logger.info(f"Stream Synthesis completed in {synthesis_time:.2f}ms")

        def iter_file():
            try:
                with open(output_file, "rb") as f:
                    while chunk := f.read(8192):
                        yield chunk
            finally:
                try:
                    os.remove(output_file)
                except Exception:
                    pass

        return StreamingResponse(
            iter_file(),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=speech.wav"}
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, workers=1)
