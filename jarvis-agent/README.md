# JARVIS LiveKit Voice Agent

Asistente de voz para el ecosistema JARVIS, basado en LiveKit Agents.

## Arquitectura

```
Audio (WebRTC) → Deepgram STT (nova-2) → DeepSeek V3 → XTTS (RunPod) → Audio
```

- **STT**: Deepgram Nova-2 en español
- **LLM**: DeepSeek V3 (deepseek-chat)
- **TTS**: XTTS personalizado desplegado en RunPod
- **VAD**: Silero VAD

## Requisitos Previos

1. **LiveKit Cloud**: Crear proyecto en https://cloud.livekit.io
2. **Deepgram**: Obtener API key en https://console.deepgram.com
3. **DeepSeek**: Obtener API key en https://platform.deepseek.com
4. **RunPod**: Desplegar servicio XTTS y obtener endpoint + API key

## Desarrollo Local

1. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

2. Configurar variables de entorno:
   ```bash
   cp .env.example .env
   # Editar .env con tus credenciales
   ```

3. Ejecutar en modo desarrollo:
   ```bash
   python jarvis_agent.py dev
   ```

4. O usar Docker Compose:
   ```bash
   docker-compose up --build
   ```

## Despliegue en Railway

1. Conectar este repositorio en Railway
2. Agregar todas las variables de entorno desde `.env.example`:
   - `LIVEKIT_URL`
   - `LIVEKIT_API_KEY`
   - `LIVEKIT_API_SECRET`
   - `DEEPSEEK_API_KEY`
   - `DEEPGRAM_API_KEY`
   - `XTTS_API_URL`
   - `XTTS_API_KEY`
   - `XTTS_LANGUAGE`
   - `JARVIS_WEBHOOK_URL`
   - `JARVIS_API_KEY`

3. Setear start command:
   ```
   python jarvis_agent.py start
   ```

## Pruebas

Usa [LiveKit Playground](https://livekit.io/playground) para probar el agente:
1. Conectar al room de tu proyecto en LiveKit Cloud
2. Hablar con JARVIS y verificar que:
   - El STT reconozca tu voz
   - DeepSeek procese la consulta
   - XTTS genere audio de respuesta

## Variables de Entorno

| Variable | Descripción |
|----------|-------------|
| `LIVEKIT_URL` | URL de WebSocket de LiveKit Cloud |
| `LIVEKIT_API_KEY` | API Key de LiveKit |
| `LIVEKIT_API_SECRET` | Secret de LiveKit |
| `DEEPSEEK_API_KEY` | API Key de DeepSeek |
| `DEEPGRAM_API_KEY` | API Key de Deepgram |
| `XTTS_API_URL` | Endpoint del servicio XTTS en RunPod |
| `XTTS_API_KEY` | API Key del servicio XTTS |
| `XTTS_LANGUAGE` | Idioma para TTS (default: es) |
| `JARVIS_WEBHOOK_URL` | URL del webhook de n8n |
| `JARVIS_API_KEY` | API Key para autenticar con n8n |
