FROM python:3.11-slim
WORKDIR /app
COPY agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY agent ./agent
RUN python agent/agent.py download-files
CMD ["python", "agent/agent.py", "start"]
