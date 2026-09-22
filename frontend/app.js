/*
 * Frontend for the voice interaction backend.
 *
 * Captures microphone audio via the Web Audio API, resamples it to the
 * 16 kHz mono float32 format the backend's WebSocket protocol expects
 * (see backend/server.py's module docstring for the full protocol), and
 * streams it as binary frames as it is captured, rather than recording a
 * full clip and sending it once, since that streaming behavior is what the
 * whole real time requirement depends on.
 *
 * A deliberate simplification for this pass: audio playback of the
 * assistant's reply happens on the server machine's own speakers
 * (tts.speak_text() plays there directly), not through this page. This UI
 * shows status and transcripts live instead. Streaming synthesized audio
 * back to the browser is a reasonable next step, noted in README.md, but
 * out of scope here since the target environment for this demo is one
 * machine.
 */

const SAMPLE_RATE = 16000;
const CHUNK_SECONDS = 0.5;
const CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_SECONDS;
const CALIBRATION_SECONDS = 1.6;

const micButton = document.getElementById("mic-button");
const micLabel = document.getElementById("mic-label");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const botAvatar = document.getElementById("bot-avatar");
const languageBadge = document.getElementById("language-badge");
const modeBadge = document.getElementById("mode-badge");
const transcript = document.getElementById("transcript");
const backendUrlInput = document.getElementById("backend-url");
const languageSelect = document.getElementById("language-select");
const connectionNote = document.getElementById("connection-note");

let audioContext = null;
let mediaStream = null;
let sourceNode = null;
let processorNode = null;
let socket = null;
let resampleRemainder = new Float32Array(0);
let sendBuffer = new Float32Array(0);
let calibrationSamplesSent = 0;
let calibrationDone = false;
let isActive = false;
let isConnecting = false;
let currentPartialBubble = null;

function setStatus(state) {
  statusDot.className = `status-dot status-${state}`;
  const labels = {
    idle: "Idle",
    calibrating: "Calibrating microphone…",
    listening: "Listening",
    thinking: "Thinking",
    speaking: "Speaking",
    error: "Error",
  };
  statusText.textContent = labels[state] || state;
  // Drives the animated bot avatar's mouth/ring/waveform styling (see
  // style.css's .bot.state-* rules); a plain class swap keeps this in one
  // place rather than duplicating per-state animation logic in JS.
  if (botAvatar) {
    botAvatar.className = `bot state-${state}`;
  }
}

function setModeBadge(mode) {
  if (!mode) {
    modeBadge.textContent = "Agent mode: —";
    modeBadge.className = "badge badge-muted";
    return;
  }
  const isCloud = mode.toLowerCase().includes("cloud");
  modeBadge.textContent = `Agent mode: ${mode}`;
  modeBadge.className = `badge ${isCloud ? "badge-cloud" : "badge-local"}`;
}

function setLanguageBadge(language) {
  const names = { en: "English", si: "Sinhala", ta: "Tamil" };
  languageBadge.textContent = `Language: ${names[language] || language || "—"}`;
}

function clearHint() {
  const hint = transcript.querySelector(".hint");
  if (hint) hint.remove();
}

function addBubble(role, text, meta) {
  clearHint();
  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role}`;
  bubble.textContent = text;
  if (meta) {
    const metaSpan = document.createElement("span");
    metaSpan.className = "bubble-meta";
    metaSpan.textContent = meta;
    bubble.appendChild(metaSpan);
  }
  transcript.appendChild(bubble);
  transcript.scrollTop = transcript.scrollHeight;
  return bubble;
}

function updateOrAddPartial(text, language) {
  clearHint();
  if (!currentPartialBubble) {
    currentPartialBubble = document.createElement("div");
    currentPartialBubble.className = "bubble bubble-user bubble-partial";
    transcript.appendChild(currentPartialBubble);
  }
  currentPartialBubble.textContent = text;
  transcript.scrollTop = transcript.scrollHeight;
  setLanguageBadge(language);
}

function finalizePartial(text, language) {
  if (currentPartialBubble) {
    currentPartialBubble.remove();
    currentPartialBubble = null;
  }
  if (text) {
    addBubble("user", text);
    setLanguageBadge(language);
  }
}

// Simple linear interpolation resampler: good enough for speech, no
// external library needed for a demo running from a plain HTML page.
function resample(buffer, fromRate, toRate) {
  if (fromRate === toRate) return buffer;
  const ratio = fromRate / toRate;
  const newLength = Math.floor(buffer.length / ratio);
  const result = new Float32Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const srcIndex = i * ratio;
    const srcFloor = Math.floor(srcIndex);
    const frac = srcIndex - srcFloor;
    const s0 = buffer[srcFloor] || 0;
    const s1 = buffer[srcFloor + 1] || 0;
    result[i] = s0 + (s1 - s0) * frac;
  }
  return result;
}

function concatFloat32(a, b) {
  const result = new Float32Array(a.length + b.length);
  result.set(a, 0);
  result.set(b, a.length);
  return result;
}

function handleAudioProcess(event) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;

  const inputData = event.inputBuffer.getChannelData(0);
  const nativeRate = audioContext.sampleRate;

  const combined = concatFloat32(resampleRemainder, inputData);
  const resampled = resample(combined, nativeRate, SAMPLE_RATE);
  resampleRemainder = new Float32Array(0);

  sendBuffer = concatFloat32(sendBuffer, resampled);

  while (sendBuffer.length >= CHUNK_SAMPLES) {
    const chunk = sendBuffer.slice(0, CHUNK_SAMPLES);
    sendBuffer = sendBuffer.slice(CHUNK_SAMPLES);
    socket.send(chunk.buffer);

    if (!calibrationDone) {
      calibrationSamplesSent += CHUNK_SAMPLES;
      if (calibrationSamplesSent >= CALIBRATION_SECONDS * SAMPLE_RATE) {
        calibrationDone = true;
      }
    }
  }
}

function handleServerMessage(event) {
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch (err) {
    return;
  }

  switch (payload.type) {
    case "status":
      setStatus(payload.state);
      break;
    case "partial_transcript":
      updateOrAddPartial(payload.text, payload.language);
      break;
    case "final_transcript":
      finalizePartial(payload.text, payload.language);
      break;
    case "agent_response":
      addBubble("assistant", payload.text, payload.mode);
      setModeBadge(payload.mode);
      break;
    case "speaking_done":
      break;
    case "error":
      setStatus("error");
      addBubble("assistant", `Error: ${payload.message}`);
      break;
    default:
      break;
  }
}

async function startConversation() {
  if (isActive || isConnecting) return;
  isConnecting = true;
  micButton.disabled = true;

  const backendUrl = backendUrlInput.value.trim();
  if (!backendUrl) {
    connectionNote.textContent = "Enter a backend WebSocket URL first.";
    isConnecting = false;
    micButton.disabled = false;
    return;
  }

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    connectionNote.textContent = `Microphone access denied or unavailable: ${err.message}`;
    isConnecting = false;
    micButton.disabled = false;
    return;
  }

  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  // ScriptProcessorNode is deprecated in favor of AudioWorklet, but needs
  // no separate worklet module file to load, which keeps this a single
  // page demo; a production rewrite should move this to AudioWorklet.
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);
  processorNode.onaudioprocess = handleAudioProcess;
  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);

  resampleRemainder = new Float32Array(0);
  sendBuffer = new Float32Array(0);
  calibrationSamplesSent = 0;
  calibrationDone = false;

  socket = new WebSocket(backendUrl);
  socket.binaryType = "arraybuffer";

  socket.onopen = () => {
    connectionNote.textContent = "Connected. Calibrating to the room, then listening.";
    const languageHint = languageSelect.value || null;
    socket.send(JSON.stringify({ type: "start_session", language_hint: languageHint }));
    setLanguageBadge(languageHint);
  };

  socket.onmessage = handleServerMessage;

  socket.onclose = () => {
    connectionNote.textContent = "Disconnected.";
    stopConversation();
  };

  socket.onerror = () => {
    connectionNote.textContent = "WebSocket error. Is the backend running?";
    setStatus("error");
  };

  isActive = true;
  isConnecting = false;
  micButton.disabled = false;
  micButton.classList.add("listening");
  micLabel.textContent = "Stop conversation";
}

function stopConversation() {
  isActive = false;
  isConnecting = false;
  micButton.disabled = false;
  micButton.classList.remove("listening");
  micLabel.textContent = "Start conversation";
  setStatus("idle");

  if (socket && socket.readyState === WebSocket.OPEN) {
    try {
      socket.send(JSON.stringify({ type: "end_session" }));
    } catch (err) {
      /* socket already closing */
    }
    socket.close();
  }
  socket = null;

  if (processorNode) {
    processorNode.disconnect();
    processorNode = null;
  }
  if (sourceNode) {
    sourceNode.disconnect();
    sourceNode = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  if (currentPartialBubble) {
    currentPartialBubble.remove();
    currentPartialBubble = null;
  }
}

micButton.addEventListener("click", () => {
  if (isActive) {
    stopConversation();
  } else {
    startConversation();
  }
});
