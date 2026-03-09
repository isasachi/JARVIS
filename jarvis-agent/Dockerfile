FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
ARG CACHE_BUST=1
RUN pip install --no-cache-dir -r requirements.txt

RUN pip show livekit-agents

RUN python -c "import silero; silero.VAD.load()" || true

COPY . .

CMD ["python", "jarvis_agent.py", "start"]
