FROM python:3.11-slim
WORKDIR /app

COPY voice_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY voice_service ./voice_service
EXPOSE 8000
CMD ["uvicorn", "voice_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
