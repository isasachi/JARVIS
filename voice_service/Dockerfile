FROM python:3.11-slim
WORKDIR /app

COPY voice_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

ARG XTTS_MODEL=tts_models/multilingual/multi-dataset/xtts_v2
ENV XTTS_MODEL=${XTTS_MODEL}
ENV COQUI_TOS_AGREED=1

# Pre-download XTTS model at build time to avoid first-request timeout in production.
RUN python -c "import os; from TTS.api import TTS; TTS(os.environ.get('XTTS_MODEL', 'tts_models/multilingual/multi-dataset/xtts_v2'))"

COPY voice_service ./voice_service
EXPOSE 8000
CMD ["uvicorn", "voice_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
