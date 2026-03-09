FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir \
    livekit-agents==0.11.2 \
    livekit-plugins-deepgram==0.6.4 \
    livekit-plugins-openai==0.6.0 \
    livekit-plugins-silero==0.6.0

RUN pip install --no-cache-dir -r requirements.txt

RUN pip show livekit-agents

RUN python -c "import silero; silero.VAD.load()" || true

COPY . .

CMD ["python", "jarvis_agent.py", "start"]
