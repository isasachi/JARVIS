import { useEffect, useMemo, useRef, useState } from 'react';
import { Room, RoomEvent, Track } from 'livekit-client';

const DEFAULT_API_BASE =
  typeof window !== 'undefined' && window.location.hostname === 'localhost'
    ? 'http://localhost:8787'
    : 'https://jarvis-api-production-abff.up.railway.app';

const API_BASE = import.meta.env.VITE_API_BASE ?? DEFAULT_API_BASE;
const ROOM_NAME = import.meta.env.VITE_LIVEKIT_ROOM ?? 'jarvis-room';

const IDLE_STATUS = '? CLICK ORB TO START SESSION';

function createRoom() {
  return new Room({
    adaptiveStream: true,
    dynacast: true,
    audioCaptureDefaults: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
}

export default function App() {
  const [clock, setClock] = useState('--:--:--');
  const [toast, setToast] = useState('');
  const [isToastVisible, setIsToastVisible] = useState(false);
  const [sessionState, setSessionState] = useState('idle'); // idle | connecting | live
  const [statusText, setStatusText] = useState(IDLE_STATUS);
  const [liveMode, setLiveMode] = useState('listening'); // listening | speaking
  const [participantName] = useState(() => `user_${Math.random().toString(36).slice(2, 9)}`);

  const roomRef = useRef(null);
  const toastTimerRef = useRef(null);
  const audioContainerRef = useRef(null);

  useEffect(() => {
    const updateClock = () => {
      setClock(
        new Date().toLocaleTimeString('en-US', {
          hour12: false,
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }),
      );
    };
    updateClock();
    const interval = setInterval(updateClock, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) {
        clearTimeout(toastTimerRef.current);
      }
      if (roomRef.current) {
        roomRef.current.disconnect(true);
      }
    };
  }, []);

  const showToast = (message) => {
    setToast(message);
    setIsToastVisible(true);
    if (toastTimerRef.current) {
      clearTimeout(toastTimerRef.current);
    }
    toastTimerRef.current = setTimeout(() => setIsToastVisible(false), 4000);
  };

  const isLive = sessionState === 'live';
  const isLoading = sessionState === 'connecting';

  const voiceEngineLabel = useMemo(() => {
    return import.meta.env.VITE_VOICE_LABEL ?? 'MINIMAX CUSTOM ?';
  }, []);

  async function fetchLiveKitToken() {
    const response = await fetch(`${API_BASE}/api/livekit/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ roomName: ROOM_NAME, participantName }),
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(body || `Token request failed (${response.status})`);
    }

    const data = await response.json();
    if (!data?.token || !data?.url) {
      throw new Error('Invalid token response from API');
    }
    return data;
  }

  function bindRoomEvents(room) {
    room.on(RoomEvent.Disconnected, () => {
      roomRef.current = null;
      setSessionState('idle');
      setLiveMode('listening');
      setStatusText(IDLE_STATUS);
      if (audioContainerRef.current) {
        audioContainerRef.current.innerHTML = '';
      }
    });

    room.on(RoomEvent.ConnectionStateChanged, (state) => {
      if (state === 'connected') {
        setSessionState('live');
        setStatusText('? LISTENING - SPEAK NOW');
      }
      if (state === 'disconnected') {
        setSessionState('idle');
        setStatusText(IDLE_STATUS);
      }
    });

    room.on(RoomEvent.TrackSubscribed, (track, publication) => {
      if (track.kind !== Track.Kind.Audio || !audioContainerRef.current) {
        return;
      }

      const el = track.attach();
      el.autoplay = true;
      el.dataset.trackSid = publication.trackSid;
      audioContainerRef.current.appendChild(el);
    });

    room.on(RoomEvent.TrackUnsubscribed, (track) => {
      if (track.kind !== Track.Kind.Audio) {
        return;
      }

      track.detach().forEach((el) => el.remove());
    });

    room.on(RoomEvent.DataReceived, (payload) => {
      try {
        const decoded = JSON.parse(new TextDecoder().decode(payload));
        if (decoded?.type === 'agent.mode' && decoded?.mode) {
          setLiveMode(decoded.mode);
          setStatusText(decoded.mode === 'speaking' ? '? JARVIS SPEAKING...' : '? LISTENING - SPEAK NOW');
        }
      } catch {
        // Ignore non-json payloads.
      }
    });
  }

  async function startSession() {
    setSessionState('connecting');
    setStatusText('? CONNECTING...');

    try {
      const { token, url } = await fetchLiveKitToken();
      const room = createRoom();
      bindRoomEvents(room);

      await room.connect(url, token, { autoSubscribe: true });
      await room.localParticipant.setMicrophoneEnabled(true);

      roomRef.current = room;
    } catch (error) {
      setSessionState('idle');
      setStatusText(IDLE_STATUS);
      showToast(`Could not start: ${error?.message ?? String(error)}`);
    }
  }

  async function endSession() {
    if (!roomRef.current) {
      setSessionState('idle');
      setStatusText(IDLE_STATUS);
      return;
    }

    try {
      roomRef.current.disconnect(true);
    } catch {
      // no-op
    } finally {
      roomRef.current = null;
      setSessionState('idle');
      setLiveMode('listening');
      setStatusText(IDLE_STATUS);
      if (audioContainerRef.current) {
        audioContainerRef.current.innerHTML = '';
      }
    }
  }

  async function toggleSession() {
    if (isLoading) {
      return;
    }

    if (roomRef.current) {
      await endSession();
      return;
    }

    await startSession();
  }

  return (
    <>
      <div id="scan" />
      <div id="glow" className={isLive ? 'live' : ''} />
      <div id="toast" className={isToastVisible ? 'show' : ''}>{toast}</div>
      <div ref={audioContainerRef} aria-hidden="true" style={{ display: 'none' }} />

      <div id="app">
        <header>
          <div className="logo">J.A.R.V.I.S<em>.ai</em></div>
          <div className="hd-mid">
            <div className={`pulse ${isLive ? 'live' : ''}`} />
            <span>{isLoading ? 'CONNECTING' : isLive ? 'SESSION ACTIVE' : 'STANDBY'}</span>
            <span style={{ opacity: 0.26 }}>[ VOICE INTERFACE ]</span>
          </div>
          <div className="hd-clock">{clock}</div>
        </header>

        <main>
          <div className={`orb-wrap ${isLive ? 'live' : ''}`}>
            <div className="ring rA" />
            <div className="ring rB" />
            <div className="ring rC" />
            <div className="ring rD" />
            <div className="orb-core" />

            <button
              id="orb-btn"
              className={`${isLive ? 'live' : ''} ${isLoading ? 'loading' : ''}`.trim()}
              title="Start / end voice session"
              aria-label="Toggle voice call"
              onClick={toggleSession}
            >
              <div className="spin-ring" />

              {!isLive && (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 013.07 9.81 19.79 19.79 0 01.11 1.2 2 2 0 012.1 0h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L6.09 7.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 14.92z" />
                </svg>
              )}

              {isLive && (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'rotate(135deg)' }}>
                  <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 013.07 9.81 19.79 19.79 0 01.11 1.2 2 2 0 012.1 0h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L6.09 7.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 14.92z" />
                </svg>
              )}
            </button>
          </div>

          <div id="status" className={`${isLive ? 'live' : ''} ${isLoading ? 'loading' : ''}`.trim()}>
            {statusText}
          </div>

          <div id="wave" className={isLive ? 'live' : ''}>
            <div className="wb" /><div className="wb" /><div className="wb" />
            <div className="wb" /><div className="wb" /><div className="wb" />
            <div className="wb" /><div className="wb" /><div className="wb" />
          </div>

          <div className="card">
            <div className="row"><span className="rk">Agent ID</span><span className="rv">livekit_agent_jarvis...</span></div>
            <div className="row"><span className="rk">Voice Engine</span><span className="rv g">{voiceEngineLabel}</span></div>
            <div className="row"><span className="rk">Protocol</span><span className="rv">WEBRTC / LIVEKIT</span></div>
            <div className="row">
              <span className="rk">Session</span>
              <span className={`rv ${isLive ? 'r' : ''}`}>{isLive ? '? LIVE' : isLoading ? '? CONNECTING' : '? IDLE'}</span>
            </div>
            <div className="row"><span className="rk">Mode</span><span className="rv">{isLive ? liveMode.toUpperCase() : 'N/A'}</span></div>
          </div>
        </main>

        <footer>
          <span>JARVIS // VOICE INTERFACE v4.0</span>
          <div className="ft-r">
            <span>@LIVEKIT + N8N ORCHESTRATION</span>
            <span id="ft-live" className={isLive ? 'live' : ''}>{isLive ? '? LIVE SESSION' : '? AWAITING INPUT'}</span>
          </div>
        </footer>
      </div>
    </>
  );
}
