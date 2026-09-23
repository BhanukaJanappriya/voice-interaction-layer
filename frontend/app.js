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
let selectedLanguage = null;
let lastResponseText = "";
let selectedMode = null; // "voice" | "chat", set when leaving the select screen
let userName = null; // null means Guest; set from the existing user name prompt
let greetingsGiven = { voice: false, chat: false }; // avoids re-greeting on every back-and-forth navigation

// Screen router: a language picker, a start chat / start voice choice, a
// new or existing user check, then the actual chat or voice screen. Plain
// hash based routing (#language, #select, #user, #voice, #chat) rather
// than a library, consistent with the rest of this frontend's no
// framework approach. The language screen is always the entry point, per
// the required flow, regardless of what hash a reload happens to land on.
const SCREEN_IDS = ["language", "select", "user", "voice", "chat"];

function navigateTo(screenName) {
  if (!SCREEN_IDS.includes(screenName)) screenName = "language";
  SCREEN_IDS.forEach((id) => {
    const el = document.getElementById(`screen-${id}`);
    if (el) el.classList.toggle("active", id === screenName);
  });
  window.location.hash = screenName;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".choice-button[data-language]").forEach((button) => {
  button.addEventListener("click", () => {
    selectedLanguage = button.getAttribute("data-language");
    navigateTo("select");
  });
});

document.querySelectorAll("[data-nav]").forEach((el) => {
  el.addEventListener("click", (event) => {
    event.preventDefault();
    navigateTo(el.getAttribute("data-nav"));
  });
});

// New or existing user screen: gates both the chat and voice screens
// (reached from either "Start Chat" or "Start Voice") so the agent can
// address the visitor by name, if given, or as Guest otherwise. This is a
// purely local, honest personalization, not a real login: no account
// system exists, and clicking "Existing user" just asks for a name to use
// rather than checking it against anything. See KNOWN_LIMITATIONS.md.
const goChatButton = document.getElementById("go-chat");
const goVoiceButton = document.getElementById("go-voice");
const goExistingButton = document.getElementById("go-existing");
const goNewButton = document.getElementById("go-new");
const userChoiceButtons = document.getElementById("user-choice-buttons");
const existingNamePrompt = document.getElementById("existing-name-prompt");
const userNameInput = document.getElementById("user-name-input");
const confirmNameButton = document.getElementById("confirm-name");
const cancelExistingButton = document.getElementById("cancel-existing");

function resetUserScreen() {
  if (userChoiceButtons) userChoiceButtons.classList.remove("hidden");
  if (existingNamePrompt) existingNamePrompt.classList.add("hidden");
}

function goToUserScreen(mode) {
  selectedMode = mode;
  resetUserScreen();
  navigateTo("user");
}

if (goChatButton) goChatButton.addEventListener("click", () => goToUserScreen("chat"));
if (goVoiceButton) goVoiceButton.addEventListener("click", () => goToUserScreen("voice"));

if (goExistingButton) {
  goExistingButton.addEventListener("click", () => {
    userChoiceButtons.classList.add("hidden");
    existingNamePrompt.classList.remove("hidden");
    try {
      const saved = localStorage.getItem("userName");
      if (saved) userNameInput.value = saved;
    } catch (err) {
      /* localStorage unavailable; the field just starts empty */
    }
    userNameInput.focus();
  });
}

if (cancelExistingButton) {
  cancelExistingButton.addEventListener("click", resetUserScreen);
}

if (goNewButton) {
  goNewButton.addEventListener("click", () => {
    userName = null;
    proceedToSelectedMode();
  });
}

if (confirmNameButton) {
  confirmNameButton.addEventListener("click", () => {
    const name = userNameInput.value.trim();
    userName = name || null;
    try {
      if (name) localStorage.setItem("userName", name);
    } catch (err) {
      /* localStorage unavailable; the name just will not be remembered next visit */
    }
    proceedToSelectedMode();
  });
}

if (userNameInput) {
  userNameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      confirmNameButton.click();
    }
  });
}

function greetingName() {
  return userName || "Guest";
}

// A canned, client built greeting rather than one generated by the LLM:
// the backend agent can take several seconds to respond (see
// KNOWN_LIMITATIONS.md's latency notes), which would make the very first
// thing a visitor sees feel laggy. A fixed introduction is instant and
// reliable, at the cost of not being able to say anything the LLM itself
// could add; that tradeoff favors a snappy first impression here.
function greetingText() {
  return (
    `Hello, ${greetingName()}! I'm your SLT Mobitel voice assistant. ` +
    "I understand English, Sinhala, and Tamil, so just start speaking whenever you're ready."
  );
}

function estimateSpeakingMs(text) {
  const words = (text || "").split(/\s+/).filter(Boolean).length;
  return Math.max(1200, (words / 165) * 60000);
}

function giveGreeting() {
  if (selectedMode === "voice" && !greetingsGiven.voice) {
    greetingsGiven.voice = true;
    const text = greetingText();
    // setStatus("speaking") itself calls bot3d.speakText(lastResponseText)
    // (see setStatus above), the same mechanism the real WebSocket driven
    // conversation already uses once agent_response arrives; setting
    // lastResponseText here first reuses that one code path instead of
    // calling speakText() a second time with its own copy of the text.
    lastResponseText = text;
    addBubble("assistant", text, "Introduction");
    setStatus("speaking");
    setTimeout(() => {
      if (window.bot3d) window.bot3d.stopSpeaking();
      setStatus("idle");
    }, estimateSpeakingMs(text));
  } else if (selectedMode === "chat" && !greetingsGiven.chat) {
    greetingsGiven.chat = true;
    addBubble("assistant", greetingText(), "Introduction", chatTranscript);
  }
}

function proceedToSelectedMode() {
  const mode = selectedMode || "voice";
  navigateTo(mode);
  giveGreeting();
  if (mode === "voice") showMicPermissionModal();
}

// Always start on the language screen; a saved hash from a previous visit
// is not honored on a fresh load, matching the required first landing page.
navigateTo("language");

// Theme toggle (light/dark), persisted so it survives a reload. The
// initial theme is also applied by a small inline script in index.html's
// <head>, before this file loads, so there is no flash of the wrong theme.
const themeToggle = document.getElementById("theme-toggle");
const themeToggleIcon = document.getElementById("theme-toggle-icon");

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

// Single stroke line icons (matching every other icon in this app), swapped
// by replacing innerHTML rather than textContent, since these are markup,
// not emoji characters.
const ICON_SUN =
  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"></circle>' +
  '<line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line>' +
  '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>' +
  '<line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line>' +
  '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>';
const ICON_MOON =
  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>';

function applyThemeIcon() {
  if (themeToggleIcon) themeToggleIcon.innerHTML = currentTheme() === "dark" ? ICON_SUN : ICON_MOON;
}

if (themeToggle) {
  applyThemeIcon();
  themeToggle.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("theme", next);
    } catch (err) {
      /* localStorage unavailable; theme choice just will not persist */
    }
    applyThemeIcon();
  });
}

// Cursor sensitive background glow: writes the pointer position as CSS
// custom properties that style.css's .bg-glow radial gradient reads.
// Throttled to one update per animation frame so it stays smooth without
// flooding style recalculation on every mousemove event.
let glowUpdateQueued = false;
let lastPointer = { x: "50%", y: "30%" };

function applyGlowPosition() {
  glowUpdateQueued = false;
  document.documentElement.style.setProperty("--mx", lastPointer.x);
  document.documentElement.style.setProperty("--my", lastPointer.y);
}

window.addEventListener("mousemove", (event) => {
  lastPointer = {
    x: `${(event.clientX / window.innerWidth) * 100}%`,
    y: `${(event.clientY / window.innerHeight) * 100}%`,
  };
  if (!glowUpdateQueued) {
    glowUpdateQueued = true;
    requestAnimationFrame(applyGlowPosition);
  }
});

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
  // Drives the flat CSS avatar's mouth/ring/waveform styling (see style.css's
  // .bot.state-* rules) by replacing only the state-* class, so the has-3d
  // class bot3d.js adds (see bot3d.js) survives every status change.
  if (botAvatar) {
    botAvatar.className = botAvatar.className.replace(/\bstate-\S+/, "").trim();
    botAvatar.classList.add(`state-${state}`);
  }
  // Drives the 3D avatar's ring color/speed and mouth idle shape; a no-op
  // if bot3d.js could not start (see its own WebGL feature check). The
  // word paced mouth animation starts here, when the server actually
  // begins speaking, using the text from the agent_response event that
  // arrived just before it (see handleServerMessage's "agent_response"
  // case, which stashes it in lastResponseText).
  if (window.bot3d) {
    window.bot3d.setState(state);
    if (state === "speaking") {
      window.bot3d.speakText(lastResponseText);
    }
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

function clearHint(container) {
  const hint = (container || transcript).querySelector(".hint");
  if (hint) hint.remove();
}

function addBubble(role, text, meta, container) {
  const target = container || transcript;
  clearHint(target);
  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role}`;
  bubble.textContent = text;
  if (meta) {
    const metaSpan = document.createElement("span");
    metaSpan.className = "bubble-meta";
    metaSpan.textContent = meta;
    bubble.appendChild(metaSpan);
  }
  target.appendChild(bubble);
  target.scrollTop = target.scrollHeight;
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

// Mirrors updateOrAddPartial()/finalizePartial() above, but for the
// agent's own reply streaming in (see agent_response_chunk in
// handleServerMessage), so the reply visibly appears while the backend
// is still generating it (during the "thinking" status) rather than only
// once it is fully ready.
let currentStreamingReplyBubble = null;

function updateStreamingReply(text) {
  clearHint();
  if (!currentStreamingReplyBubble) {
    currentStreamingReplyBubble = document.createElement("div");
    currentStreamingReplyBubble.className = "bubble bubble-assistant bubble-partial";
    transcript.appendChild(currentStreamingReplyBubble);
  }
  currentStreamingReplyBubble.textContent = text;
  transcript.scrollTop = transcript.scrollHeight;
}

function finalizeStreamingReply(text, mode) {
  if (currentStreamingReplyBubble) {
    currentStreamingReplyBubble.remove();
    currentStreamingReplyBubble = null;
  }
  addBubble("assistant", text, mode);
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
    case "agent_response_chunk":
      updateStreamingReply(payload.text);
      break;
    case "agent_response":
      lastResponseText = payload.text || "";
      finalizeStreamingReply(payload.text, payload.mode);
      setModeBadge(payload.mode);
      break;
    case "speaking_done":
      if (window.bot3d) window.bot3d.stopSpeaking();
      break;
    case "error":
      setStatus("error");
      addBubble("assistant", `Error: ${payload.message}`);
      break;
    default:
      break;
  }
}

// Mic permission modal: shown once on entering the voice screen, before
// the browser's own native permission prompt, so a first time visitor has
// context for why that prompt is about to appear rather than being
// surprised by an unexplained browser popup. Clicking "Allow microphone"
// is what actually triggers startConversation()'s getUserMedia() call
// (and, with it, the real browser prompt); dismissing this modal just
// hides it; the "Start conversation" button still works normally if
// someone dismisses this and wants to start later.
const micPermissionModal = document.getElementById("mic-permission-modal");
const allowMicButton = document.getElementById("allow-mic-button");
const dismissMicModalButton = document.getElementById("dismiss-mic-modal");

async function showMicPermissionModal() {
  if (!micPermissionModal) return;
  // Best effort: skip the explanatory popup entirely if the browser
  // already reports microphone access as granted, since there is nothing
  // left to explain or ask permission for. Not every browser supports
  // querying the "microphone" permission name, so this silently falls
  // through to showing the modal if the check itself fails.
  try {
    if (navigator.permissions && navigator.permissions.query) {
      const status = await navigator.permissions.query({ name: "microphone" });
      if (status.state === "granted") return;
    }
  } catch (err) {
    /* Permissions API unsupported for "microphone" in this browser; show the modal as normal */
  }
  micPermissionModal.classList.remove("hidden");
}

function hideMicPermissionModal() {
  if (micPermissionModal) micPermissionModal.classList.add("hidden");
}

if (allowMicButton) {
  allowMicButton.addEventListener("click", () => {
    hideMicPermissionModal();
    startConversation();
  });
}

if (dismissMicModalButton) {
  dismissMicModalButton.addEventListener("click", hideMicPermissionModal);
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
    socket.send(JSON.stringify({ type: "start_session", language_hint: selectedLanguage }));
    setLanguageBadge(selectedLanguage);
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
  if (currentStreamingReplyBubble) {
    currentStreamingReplyBubble.remove();
    currentStreamingReplyBubble = null;
  }
}

micButton.addEventListener("click", () => {
  if (isActive) {
    stopConversation();
  } else {
    startConversation();
  }
});

// Text chat screen: a lightweight alternative to the voice flow, talking
// to the backend's POST /chat endpoint (see backend/server.py), which
// runs the same Orchestrator used by the voice pipeline, just without any
// STT or TTS involved. Derives the chat endpoint from the same backend
// field the voice screen uses, so there is only one place to configure
// the backend location.
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatTranscript = document.getElementById("chat-transcript");
const chatModeBadge = document.getElementById("chat-mode-badge");

function chatHttpUrl() {
  const wsUrl = backendUrlInput.value.trim();
  return wsUrl.replace(/^ws/i, "http").replace(/\/ws\/voice.*$/i, "") + "/chat";
}

function setChatModeBadge(mode) {
  if (!chatModeBadge) return;
  if (!mode) {
    chatModeBadge.textContent = "Agent mode: —";
    chatModeBadge.className = "badge badge-muted";
    return;
  }
  const isCloud = mode.toLowerCase().includes("cloud");
  chatModeBadge.textContent = `Agent mode: ${mode}`;
  chatModeBadge.className = `badge ${isCloud ? "badge-cloud" : "badge-local"}`;
}

if (chatForm) {
  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;

    addBubble("user", text, null, chatTranscript);
    chatInput.value = "";
    chatInput.disabled = true;

    try {
      const response = await fetch(chatHttpUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, language: selectedLanguage }),
      });
      if (!response.ok) {
        throw new Error(`Backend returned ${response.status}`);
      }
      const data = await response.json();
      addBubble("assistant", data.reply, data.mode, chatTranscript);
      setChatModeBadge(data.mode);
    } catch (err) {
      addBubble("assistant", `Error: could not reach the backend (${err.message}).`, null, chatTranscript);
    } finally {
      chatInput.disabled = false;
      chatInput.focus();
    }
  });
}
