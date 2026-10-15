/*
 * Browser side of the Gemini Live voice tier (tier 1 of the voice router).
 *
 * The page talks to this server, never to Gemini: the key and every tool call
 * stay on the backend (docs/GEMINI_LIVE_PLAN.md). Microphone audio goes out as
 * binary 16 kHz PCM16 frames on /ws/live, the model's speech comes back as
 * binary 24 kHz PCM16 frames, and the same JSON events as /ws/voice carry the
 * transcript, status and confirmation. The speech is played here and the
 * analyser it passes through drives the avatar's lip sync from the real audio.
 *
 * Nothing runs unless app.js asks for it after GET /api/voice/tier says
 * "gemini_live"; any failure is reported through onFailed and app.js starts the
 * next tier.
 */
(function () {
  "use strict";

  const IN_RATE = 16000;
  const OUT_RATE = 24000;
  const SETUP_TIMEOUT_MS = 12000;

  async function tier(baseUrl) {
    try {
      const response = await fetch(`${baseUrl}/api/voice/tier`, { credentials: "include" });
      if (response.ok) return await response.json();
    } catch (err) {
      /* backend unreachable or route missing: the old path is used */
    }
    return { enabled: false, tier: "local" };
  }

  function toInt16(float32, sourceRate) {
    // Linear resampling to 16 kHz, then clamp to PCM16.
    const ratio = sourceRate / IN_RATE;
    const length = Math.floor(float32.length / ratio);
    const out = new Int16Array(length);
    for (let i = 0; i < length; i++) {
      const position = i * ratio;
      const index = Math.floor(position);
      const next = Math.min(index + 1, float32.length - 1);
      const sample = float32[index] + (float32[next] - float32[index]) * (position - index);
      out[i] = Math.max(-1, Math.min(1, sample)) * 32767;
    }
    return out;
  }

  // Options:
  //   wsUrl, language, voiceStyle, userName, sessionToken, greet, context
  //   onEvent(payload)   a /ws/voice shaped JSON event
  //   onAudio(analyser)  once, when playback exists: hand it to the avatar for lip sync
  //   onFailed(reason, wasReady)  genuine failure (the server already told the router)
  //   onClosed()         the user ended it or the socket closed normally
  // Resolves to {setMuted, stopPlayback, disconnect} after "live_ready", or rejects
  // with an Error whose .reason is the failure class, so app.js can start the next tier.
  async function connect(options) {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const context = new AudioCtx();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    analyser.connect(context.destination);

    let muted = false;
    let closed = false;
    let ready = false;
    let playHead = 0;
    const playing = new Set();

    const socket = new WebSocket(options.wsUrl);
    socket.binaryType = "arraybuffer";

    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (event) => {
      if (!ready || muted || closed || socket.readyState !== WebSocket.OPEN) return;
      const pcm = toInt16(event.inputBuffer.getChannelData(0), context.sampleRate);
      socket.send(pcm.buffer);
    };
    source.connect(processor);
    processor.connect(context.destination);

    function stopPlayback() {
      playing.forEach((node) => {
        try {
          node.onended = null;
          node.stop();
        } catch (err) {
          /* already finished */
        }
      });
      playing.clear();
      playHead = 0;
    }

    function play(arrayBuffer) {
      const samples = new Int16Array(arrayBuffer);
      if (!samples.length) return;
      const buffer = context.createBuffer(1, samples.length, OUT_RATE);
      const channel = buffer.getChannelData(0);
      for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
      const node = context.createBufferSource();
      node.buffer = buffer;
      node.connect(analyser);
      const startAt = Math.max(context.currentTime + 0.02, playHead);
      node.start(startAt);
      playHead = startAt + buffer.duration;
      playing.add(node);
      node.onended = () => playing.delete(node);
    }

    function cleanup() {
      if (closed) return;
      closed = true;
      stopPlayback();
      try { processor.disconnect(); } catch (err) { /* ignore */ }
      try { source.disconnect(); } catch (err) { /* ignore */ }
      stream.getTracks().forEach((track) => track.stop());
      try { context.close(); } catch (err) { /* ignore */ }
    }

    return await new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => fail("timeout"), SETUP_TIMEOUT_MS);

      function fail(reason) {
        clearTimeout(timer);
        const wasReady = ready;
        cleanup();
        try { socket.close(); } catch (err) { /* ignore */ }
        if (!settled) {
          settled = true;
          const error = new Error("gemini live unavailable: " + reason);
          error.reason = reason;
          reject(error);
        } else if (options.onFailed) {
          options.onFailed(reason, wasReady);
        }
      }

      socket.onopen = () => {
        socket.send(
          JSON.stringify({
            type: "start_session",
            language_hint: options.language,
            voice_style: options.voiceStyle,
            user_name: options.userName || null,
            greet: options.greet !== false,
            session_token: options.sessionToken || null,
            context: options.context || "",
          })
        );
      };

      socket.onmessage = (message) => {
        if (typeof message.data !== "string") {
          play(message.data);
          return;
        }
        let payload;
        try {
          payload = JSON.parse(message.data);
        } catch (err) {
          return;
        }
        if (payload.type === "live_ready") {
          ready = true;
          clearTimeout(timer);
          if (context.state === "suspended") context.resume().catch(() => {});
          if (options.onAudio) options.onAudio(analyser, context);
          settled = true;
          resolve({
            setMuted(value) { muted = Boolean(value); },
            stopPlayback,
            disconnect() {
              try { socket.send(JSON.stringify({ type: "end_session" })); } catch (err) { /* closing */ }
              try { socket.close(); } catch (err) { /* ignore */ }
              cleanup();
            },
          });
          return;
        }
        if (payload.type === "live_failed") {
          fail(payload.reason || "outage");
          return;
        }
        if (payload.type === "interrupt") stopPlayback();
        if (options.onEvent) options.onEvent(payload);
      };

      socket.onerror = () => fail("outage");
      socket.onclose = () => {
        if (closed) return;
        if (!ready) {
          fail("outage");
          return;
        }
        cleanup();
        if (options.onClosed) options.onClosed();
      };
    });
  }

  window.LiveVoice = { tier, connect };
})();
