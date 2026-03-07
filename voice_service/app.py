import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import requests
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from TTS.api import TTS

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger('jarvis_voice')


@dataclass
class VoiceConfig:
    model_name: str
    language: str
    bundled_wav_path: str
    custom_wav_url: str
    custom_wav_path: str
    warmup_text: str


class CustomVoiceEngine:
    def __init__(self, config: VoiceConfig) -> None:
        self.config = config
        self._tts: TTS | None = None
        self._speaker_wav_path: str | None = None
        self._speaker_source = 'unknown'
        self._ready = False
        self._error = ''
        self._startup_ms = 0
        self._synth_lock = Lock()
        self._warmup_audio: bytes | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> str:
        return self._error

    @property
    def startup_ms(self) -> int:
        return self._startup_ms

    def startup(self) -> None:
        start = time.monotonic()
        self._apply_runtime_tuning()

        try:
            self._speaker_wav_path = self._resolve_speaker_wav()
            self._tts = TTS(self.config.model_name)
            self._warmup_audio = self._prime_model()
            self._ready = True
            self._error = ''
        except Exception as exc:
            self._ready = False
            self._error = str(exc)
            logger.exception('voice startup failed: %s', exc)
            raise
        finally:
            self._startup_ms = int((time.monotonic() - start) * 1000)

        logger.info(
            'voice startup complete model=%s language=%s speaker_source=%s startup_ms=%s',
            self.config.model_name,
            self.config.language,
            self._speaker_source,
            self._startup_ms,
        )

    def synthesize(self, text: str, language: str | None = None) -> bytes:
        if not self._ready or self._tts is None or self._speaker_wav_path is None:
            raise RuntimeError('voice engine is not ready')

        if text is None or text.strip() == '':
            raise ValueError('text is required')

        if self._warmup_audio and text.strip() == self.config.warmup_text.strip():
            logger.info('voice synth cache hit for warmup text')
            return self._warmup_audio

        logger.info('voice synth request chars=%s', len(text))

        with self._synth_lock:
            out_fd, out_path = tempfile.mkstemp(prefix='jarvis_', suffix='.wav')
            os.close(out_fd)
            try:
                with torch.inference_mode():
                    self._tts.tts_to_file(
                        text=text,
                        speaker_wav=self._speaker_wav_path,
                        language=(language or self.config.language),
                        file_path=out_path,
                    )
                with open(out_path, 'rb') as f:
                    return f.read()
            finally:
                if os.path.exists(out_path):
                    os.remove(out_path)

    def _prime_model(self) -> bytes:
        assert self._tts is not None
        assert self._speaker_wav_path is not None

        out_fd, out_path = tempfile.mkstemp(prefix='jarvis_prime_', suffix='.wav')
        os.close(out_fd)
        try:
            with torch.inference_mode():
                self._tts.tts_to_file(
                    text=self.config.warmup_text,
                    speaker_wav=self._speaker_wav_path,
                    language=self.config.language,
                    file_path=out_path,
                )
            with open(out_path, 'rb') as f:
                return f.read()
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def _resolve_speaker_wav(self) -> str:
        bundled = self.config.bundled_wav_path
        if os.path.exists(bundled) and os.path.getsize(bundled) > 0:
            self._speaker_source = 'bundled_file'
            return bundled

        custom_url = self.config.custom_wav_url.strip()
        if not custom_url:
            raise RuntimeError('No custom voice WAV available. Provide voice_service/custom_voice.wav or CUSTOM_VOICE_WAV_URL')

        try:
            response = requests.get(custom_url, timeout=60)
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f'Failed to download custom voice WAV: {exc}') from exc

        target = self.config.custom_wav_path
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        with open(target, 'wb') as f:
            f.write(response.content)

        if os.path.getsize(target) == 0:
            raise RuntimeError('Downloaded custom voice WAV is empty')

        self._speaker_source = 'downloaded_url'
        return target

    def _apply_runtime_tuning(self) -> None:
        num_threads = int(os.getenv('TORCH_NUM_THREADS', '6'))
        num_interop_threads = int(os.getenv('TORCH_INTEROP_THREADS', '2'))
        torch.set_num_threads(max(1, num_threads))
        torch.set_num_interop_threads(max(1, num_interop_threads))


class SynthesizeRequest(BaseModel):
    text: str
    language: str | None = None


MODEL_NAME = os.getenv('XTTS_MODEL', 'tts_models/multilingual/multi-dataset/xtts_v2')
DEFAULT_LANG = os.getenv('CUSTOM_VOICE_LANG', 'es')
DEFAULT_WAV_URL = os.getenv('CUSTOM_VOICE_WAV_URL', '').strip()
DEFAULT_WAV_PATH = os.getenv('CUSTOM_VOICE_WAV_PATH', '/tmp/custom_voice.wav')
BUNDLED_WAV_PATH = str(Path(__file__).with_name('custom_voice.wav'))
WARMUP_TEXT = os.getenv('WARMUP_TEXT', 'Bienvenido señor, ¿qué haremos hoy?')

engine = CustomVoiceEngine(
    VoiceConfig(
        model_name=MODEL_NAME,
        language=DEFAULT_LANG,
        bundled_wav_path=BUNDLED_WAV_PATH,
        custom_wav_url=DEFAULT_WAV_URL,
        custom_wav_path=DEFAULT_WAV_PATH,
        warmup_text=WARMUP_TEXT,
    )
)

app = FastAPI(title='jarvis-voice-service')


@app.on_event('startup')
def on_startup() -> None:
    engine.startup()


@app.get('/health')
def health() -> dict:
    return {
        'ok': engine.ready,
        'model': MODEL_NAME,
        'language': DEFAULT_LANG,
        'startup_ms': engine.startup_ms,
        'error': engine.error,
    }


@app.post('/synthesize')
def synthesize(req: SynthesizeRequest):
    if not engine.ready:
        raise HTTPException(status_code=503, detail='Voice engine is not ready')

    try:
        wav_bytes = engine.synthesize(req.text, req.language or DEFAULT_LANG)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'TTS synthesis failed: {exc}') from exc

    return Response(content=wav_bytes, media_type='audio/wav')
