/*
 * Browser side of the LiveKit voice transport (VOICE_TRANSPORT=livekit).
 *
 * The original /ws/voice path sends microphone audio over a WebSocket and the
 * server plays the reply on its own speakers. This file is the alternative:
 * the page joins a LiveKit room, publishes the microphone (with the browser's
 * echo cancellation and noise suppression), and plays the assistant's audio
 * track in the page itself. The agent publishes its status, transcript and
 * reply events on a data channel in exactly the JSON shapes /ws/voice sends,
 * so app.js hands them to the same handleServerMessage() it already has.
 *
 * Nothing here runs unless GET /api/livekit/config says the server is set to
 * livekit and configured; app.js falls back to /ws/voice on any failure.
 */
(function () {
  "use strict";

  const EVENT_TOPIC = "va-events";
  const CONTROL_TOPIC = "va-control";
  const AGENT_TRACK_NAME = "agent-voice";

  let cachedConfig = null;

  function client() {
    return window.LivekitClient || null;
  }

  // The server's transport choice, asked once per page load. Any failure
  // (old backend without the route, network error) means "use the WebSocket".
  async function fetchConfig(baseUrl) {
    if (cachedConfig) return cachedConfig;
    try {
      const response = await fetch(`${baseUrl}/api/livekit/config`, { credentials: "include" });
      if (response.ok) cachedConfig = await response.json();
    } catch (err) {
      /* backend unreachable or route missing */
    }
    return cachedConfig || { transport: "websocket", livekit_configured: false, avatar_renderer: "legacy" };
  }

  // LiveKit is used first. It is skipped (the page then uses /ws/voice and the
  // local pipeline) when the server is not set to it, has no keys, or says its
  // voice agent is not running right now. Asked fresh each time, so an agent
  // that came up after the page loaded is picked up on the next conversation.
  async function isEnabled(baseUrl) {
    cachedConfig = null;
    const config = await fetchConfig(baseUrl);
    return Boolean(
      client() && config.transport === "livekit" && config.livekit_configured && config.agent_ready !== false
    );
  }

  // Joins the room and starts publishing the microphone. Options:
  //   baseUrl, language, voiceStyle, userName, sessionToken (as POST /auth/verify returned)
  //   onEvent(payload)        a /ws/voice shaped event from the agent
  //   onAgentAudio(track, el) the agent's audio track and the element playing it
  //   onDisconnected()        the room closed (the agent ended it, or the network dropped)
  //   onNeedsGesture()        the browser blocked audio until the user taps the page
  //   agentTimeoutMs          how long to wait for the agent to answer (default 20000)
  // Resolves to a handle {send, setMuted, disconnect, room}; rejects when the
  // token or the connection fails, so the caller can fall back.
  async function connect(options) {
    const lk = client();
    if (!lk) throw new Error("livekit-client is not loaded");

    const response = await fetch(`${options.baseUrl}/api/livekit/token`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        language: options.language || null,
        voice_style: options.voiceStyle || "female_natural",
        user_name: options.userName || null,
        session_token: options.sessionToken || null,
        handover: Boolean(options.handover),
      }),
    });
    if (!response.ok) throw new Error(`token request failed (${response.status})`);
    const { url, token } = await response.json();

    const room = new lk.Room({
      audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    const playbackElements = [];

    room.on(lk.RoomEvent.TrackSubscribed, (track) => {
      if (track.kind !== "audio") return;
      const element = track.attach();
      element.dataset.trackName = AGENT_TRACK_NAME;
      element.style.display = "none";
      document.body.appendChild(element);
      playbackElements.push(element);
      if (options.onAgentAudio) options.onAgentAudio(track, element);
    });

    room.on(lk.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
      if (topic !== EVENT_TOPIC) return;
      try {
        options.onEvent(JSON.parse(decoder.decode(payload)));
      } catch (err) {
        /* not one of ours */
      }
    });

    room.on(lk.RoomEvent.AudioPlaybackStatusChanged, () => {
      if (room.canPlaybackAudio) return;
      if (options.onNeedsGesture) options.onNeedsGesture();
      const resume = () => {
        room.startAudio().catch(() => {});
        document.removeEventListener("click", resume);
      };
      document.addEventListener("click", resume);
    });

    room.on(lk.RoomEvent.Disconnected, () => {
      playbackElements.forEach((element) => element.remove());
      if (options.onDisconnected) options.onDisconnected();
    });

    // Resolves when the agent first speaks up (its first event or its audio
    // track), so a room that connects but never gets an agent is detected.
    let agentSeen;
    const agentArrived = new Promise((resolve) => {
      agentSeen = resolve;
    });
    room.on(lk.RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === "audio") agentSeen();
    });
    room.on(lk.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
      if (topic === EVENT_TOPIC) agentSeen();
    });

    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);

    const waitMs = options.agentTimeoutMs || 20000;
    let timer;
    const arrived = await Promise.race([
      agentArrived.then(() => true),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(false), waitMs);
      }),
    ]);
    clearTimeout(timer);
    if (!arrived) {
      room.disconnect();
      throw new Error("the LiveKit voice agent did not join in time");
    }

    function send(message) {
      return room.localParticipant.publishData(encoder.encode(JSON.stringify(message)), {
        reliable: true,
        topic: CONTROL_TOPIC,
      });
    }

    return {
      room,
      send,
      // The audio track stays published while muted; the agent ignores it.
      setMuted: (muted) => send({ type: "mute", muted: Boolean(muted) }),
      setVoice: (voiceStyle) => send({ type: "set_voice", voice_style: voiceStyle }),
      disconnect: () => room.disconnect(),
    };
  }

  window.LiveKitVoice = { isEnabled, fetchConfig, connect };
})();
