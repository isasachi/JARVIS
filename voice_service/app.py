import os
import tempfile
import uuid
from threading import Lock, Thread

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from TTS.api import TTS

app = FastAPI(title='jarvis-voice-service')

MODEL_NAME = os.getenv('XTTS_MODEL', 'tts_models/multilingual/multi-dataset/xtts_v2')
DEFAULT_LANG = os.getenv('CUSTOM_VOICE_LANG', 'es')
DEFAULT_WAV_URL = os.getenv('CUSTOM_VOICE_WAV_URL', '').strip()
DEFAULT_WAV_PATH = os.getenv('CUSTOM_VOICE_WAV_PATH', '/tmp/custom_voice.wav')

_tts = None
_tts_lock = Lock()
_wav_lock = Lock()
_cached_wav_url = None

_warmup_lock = Lock()
_warmup_started = False
_warmup_ready = False
_warmup_error = ''


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
    global _cached_wav_url

    url = (custom_url or DEFAULT_WAV_URL).strip()
    if not url:
        if os.path.exists(DEFAULT_WAV_PATH):
            return DEFAULT_WAV_PATH
        raise HTTPException(status_code=400, detail='No speaker reference wav configured')

    with _wav_lock:
        if os.path.exists(DEFAULT_WAV_PATH) and _cached_wav_url == url and os.path.getsize(DEFAULT_WAV_PATH) > 0:
            return DEFAULT_WAV_PATH

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f'Failed to fetch speaker wav: {exc}') from exc

        with open(DEFAULT_WAV_PATH, 'wb') as f:
            f.write(response.content)

        _cached_wav_url = url
        return DEFAULT_WAV_PATH


def _run_warmup() -> None:
    global _warmup_started, _warmup_ready, _warmup_error
    try:
        get_tts()
        if DEFAULT_WAV_URL:
            ensure_reference_wav(None)
        _warmup_ready = True
        _warmup_error = ''
    except Exception as exc:
        _warmup_error = str(exc)
    finally:
        _warmup_started = False


def trigger_warmup() -> None:
    global _warmup_started
    with _warmup_lock:
        if _warmup_started or _warmup_ready:
            return
        _warmup_started = True
        Thread(target=_run_warmup, daemon=True).start()


@app.on_event('startup')
def on_startup() -> None:
    trigger_warmup()


@app.get('/health')
def health() -> dict:
    return {
        'ok': True,
        'warmup_ready': _warmup_ready,
        'warmup_started': _warmup_started,
        'warmup_error': _warmup_error,
    }


@app.post('/synthesize')
def synthesize(req: SynthesizeRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail='Text is required')

    if not _warmup_ready:
        trigger_warmup()
        raise HTTPException(status_code=503, detail='Voice model warming up, retry shortly')

    ref_wav = ensure_reference_wav(req.speaker_wav_url)
    out_path = os.path.join(tempfile.gettempdir(), f'jarvis_{uuid.uuid4().hex}.wav')

    try:
        tts = get_tts()
        tts.tts_to_file(
            text=text,
            speaker_wav=ref_wav,
            language=(req.language or DEFAULT_LANG),
            file_path=out_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'TTS synthesis failed: {exc}') from exc

    return FileResponse(out_path, media_type='audio/wav', filename='jarvis.wav')
