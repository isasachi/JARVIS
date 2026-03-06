import cors from 'cors';
import dotenv from 'dotenv';
import express from 'express';
import { AccessToken, AgentDispatchClient, RoomServiceClient } from 'livekit-server-sdk';

dotenv.config();

const app = express();
const port = Number(process.env.PORT || 8787);

const livekitUrl = process.env.LIVEKIT_URL;
const apiKey = process.env.LIVEKIT_API_KEY;
const apiSecret = process.env.LIVEKIT_API_SECRET;
const n8nWebhookUrl = process.env.N8N_WEBHOOK_URL;
const voiceServiceUrl = process.env.VOICE_SERVICE_URL;
const agentName = process.env.AGENT_NAME || 'jarvis-agent';

if (!livekitUrl || !apiKey || !apiSecret) {
  console.error('Missing LIVEKIT_URL, LIVEKIT_API_KEY, or LIVEKIT_API_SECRET in environment.');
}

const dispatchClient = new AgentDispatchClient(livekitUrl, apiKey, apiSecret);
const roomServiceClient = new RoomServiceClient(livekitUrl, apiKey, apiSecret);

app.use(cors());
app.use(express.json({ limit: '2mb' }));

app.get('/health', (_req, res) => {
  res.json({ ok: true });
});

app.post('/api/livekit/token', async (req, res) => {
  try {
    const roomName = req.body?.roomName ?? 'jarvis-room';
    const participantName = req.body?.participantName ?? `user_${Date.now()}`;

    const at = new AccessToken(apiKey, apiSecret, {
      identity: participantName,
      ttl: '10m',
      name: participantName,
      metadata: JSON.stringify({ role: 'user', source: 'web' }),
    });

    at.addGrant({
      roomJoin: true,
      room: roomName,
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
    });

    try {
      await roomServiceClient.createRoom({ name: roomName });
    } catch (roomCreateError) {
      const msg = String(roomCreateError?.message ?? roomCreateError);
      if (!msg.toLowerCase().includes('already exists')) {
        return res.status(500).json({
          error: `Room create failed: ${msg}`,
        });
      }
    }

    try {
      const existing = await dispatchClient.listDispatch(roomName);
      const alreadyDispatched = Array.isArray(existing)
        && existing.some((dispatch) => dispatch?.agentName === agentName);

      if (!alreadyDispatched) {
        await dispatchClient.createDispatch(roomName, agentName, {
          metadata: JSON.stringify({ source: 'web-token-endpoint', participantName }),
        });
      }
    } catch (dispatchError) {
      return res.status(500).json({
        error: `Agent dispatch failed: ${dispatchError?.message ?? String(dispatchError)}`,
      });
    }

    res.json({
      token: await at.toJwt(),
      url: livekitUrl,
      roomName,
      participantName,
    });
  } catch (error) {
    res.status(500).json({ error: error?.message ?? 'Token generation failed' });
  }
});

app.post('/api/n8n/relay', async (req, res) => {
  if (!n8nWebhookUrl) {
    return res.status(500).json({ error: 'N8N_WEBHOOK_URL is not configured' });
  }

  try {
    const response = await fetch(n8nWebhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body ?? {}),
    });

    const text = await response.text();

    res.status(response.status).type(response.headers.get('content-type') ?? 'application/json').send(text);
  } catch (error) {
    res.status(500).json({ error: error?.message ?? 'n8n relay failed' });
  }
});

app.post('/api/voice/synthesize', async (req, res) => {
  if (!voiceServiceUrl) {
    return res.status(500).json({ error: 'VOICE_SERVICE_URL is not configured' });
  }

  try {
    const response = await fetch(`${voiceServiceUrl.replace(/\/$/, '')}/synthesize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body ?? {}),
    });

    if (!response.ok) {
      const errorBody = await response.text();
      return res.status(response.status).send(errorBody || 'Voice synthesis failed');
    }

    const buffer = Buffer.from(await response.arrayBuffer());
    res.setHeader('Content-Type', response.headers.get('content-type') ?? 'audio/wav');
    return res.send(buffer);
  } catch (error) {
    return res.status(500).json({ error: error?.message ?? 'voice proxy failed' });
  }
});

app.listen(port, () => {
  console.log(`Token server listening on http://localhost:${port}`);
});
