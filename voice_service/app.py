import os
import tempfile
import uuid
from threading import Lock

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from TTS.api import TTS

app = FastAPI(title='jarvis-voice-service')

MODEL_NAME = os.getenv('XTTS_MODEL', 'tts_models/multilingual/multi-dataset/xtts_v2')
DEFAULT_LANG = os.getenv('CUSTOM_VOICE_LANG', 'en')
DEFAULT_WAV_URL = os.getenv('CUSTOM_VOICE_WAV_URL', '').strip()
DEFAULT_WAV_PATH = os.getenv('CUSTOM_VOICE_WAV_PATH', '/tmp/custom_voice.wav')

_tts = None
_tts_lock = Lock()


class SynthesizeRequest(BaseModel):
    text: str
    language: str | None = None
    speaker_wav_url: str | None = None


def get_tts() -> TTS:
    global _tts
    if _tts is None:
        with _tts_lock:
            if _tts is None:
                _tts = TTS(MODEL_NAME)
    return _tts


def ensure_reference_wav(custom_url: str | None) -> str:
    url = (custom_url or DEFAULT_WAV_URL).strip()
    if not url:
        if os.path.exists(DEFAULT_WAV_PATH):
            return DEFAULT_WAV_PATH
        raise HTTPException(status_code=400, detail='No speaker reference wav configured')

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'Failed to fetch speaker wav: {exc}') from exc

    with open(DEFAULT_WAV_PATH, 'wb') as f:
        f.write(response.content)

    return DEFAULT_WAV_PATH


@app.get('/health')
def health() -> dict:
    return {'ok': True}


@app.post('/synthesize')
def synthesize(req: SynthesizeRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail='Text is required')

    ref_wav = ensure_reference_wav(req.speaker_wav_url)
    out_path = os.path.join(tempfile.gettempdir(), f'jarvis_{uuid.uuid4().hex}.wav')

    try:
        tts = get_tts()
        tts.tts_to_file(
            text=req.text,
            speaker_wav=ref_wav,
            language=(req.language or DEFAULT_LANG),
            file_path=out_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'TTS synthesis failed: {exc}') from exc

    return FileResponse(out_path, media_type='audio/wav', filename='jarvis.wav')
