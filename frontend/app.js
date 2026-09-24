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
 * shows captions live instead. Streaming synthesized audio back to the
 * browser is a reasonable next step, noted in README.md, but out of scope
 * here since the target environment for this demo is one machine.
 */

const SAMPLE_RATE = 16000;
const CHUNK_SECONDS = 0.5;
const CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_SECONDS;
const CALIBRATION_SECONDS = 1.0; // matches backend/server.py's calibration window

const botAvatar = document.getElementById("bot-avatar");
const transcript = document.getElementById("transcript");
const backendUrlInput = document.getElementById("backend-url");

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
let isMuted = false;
// Gates whether captured audio actually gets sent over the WebSocket (see
// setStatus() below, which flips this per status event, and
// handleAudioProcess(), which checks it, alongside isMuted). Audio is
// only sent while the server has told us it is actually listening
// ("listening" or the initial "calibrating"), never while it is
// "thinking" or "speaking". On this project's single machine demo setup,
// the assistant's spoken reply plays on that same machine's speakers, so
// a microphone that kept streaming through "speaking" could physically
// pick up the assistant's own voice and have it transcribed and treated
// as the next thing the user said. This is what actually prevents that:
// nothing captured during those two states ever reaches the server at
// all, not just a server side filter on top of an already sent, already
// ambiguous chunk.
let audioSendAllowed = false;
let currentPartialBubble = null;
let selectedLanguage = null;
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

// A canned greeting rather than one generated by the LLM: a fixed
// introduction is instant and reliable. This copy is only for the text
// chat screen, in the language picked on the first screen; on the voice
// screen the backend speaks its own copy of the same introductions out
// loud (see _GREETINGS in backend/server.py). "Guest" is used in the
// selected language when no name was given.
const CHAT_GREETINGS = {
  en: {
    guest: "Guest",
    text: (name) =>
      `Welcome to SLT Mobitel, ${name}! I'm your voice assistant, and I understand ` +
      "English, Sinhala, and Tamil, so just start speaking whenever you're ready.",
  },
  si: {
    guest: "අමුත්තා",
    text: (name) =>
      `SLT Mobitel වෙත සාදරයෙන් පිළිගනිමු, ${name}! මම ඔබේ හඬ සහායකයා. මට ඉංග්‍රීසි, ` +
      "සිංහල සහ දෙමළ භාෂා තේරෙනවා, ඒ නිසා ඔබ සූදානම් වූ විට කතා කිරීම ආරම්භ කරන්න.",
  },
  ta: {
    guest: "விருந்தினர்",
    text: (name) =>
      `SLT Mobitel-க்கு வரவேற்கிறோம், ${name}! நான் உங்கள் குரல் உதவியாளர். எனக்கு ` +
      "ஆங்கிலம், சிங்களம் மற்றும் தமிழ் புரியும், எனவே நீங்கள் தயாரானதும் பேசத் தொடங்குங்கள்.",
  },
};

function greetingText() {
  const greeting = CHAT_GREETINGS[selectedLanguage] || CHAT_GREETINGS.en;
  return greeting.text(userName || greeting.guest);
}

function chatGreeting() {
  if (greetingsGiven.chat) return;
  greetingsGiven.chat = true;
  addBubble("assistant", greetingText(), "Introduction", chatTranscript);
}

function proceedToSelectedMode() {
  const mode = selectedMode || "voice";
  navigateTo(mode);
  if (mode === "voice") {
    // The voice screen's own introduction (and the conversation itself)
    // starts from showMicPermissionModal()/beginVoiceSession() below, not
    // here: mic permission is asked first, and only after that is granted
    // does the assistant introduce itself and start listening. See those
    // functions for the full sequencing and why.
    showMicPermissionModal();
  } else {
    chatGreeting();
  }
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
  audioSendAllowed = state === "listening" || state === "calibrating";
  // Drives the flat CSS avatar's mouth/ring/waveform styling (see style.css's
  // .bot.state-* rules) by replacing only the state-* class, so the has-3d
  // class bot3d.js adds (see bot3d.js) survives every status change.
  if (botAvatar) {
    botAvatar.className = botAvatar.className.replace(/\bstate-\S+/, "").trim();
    botAvatar.classList.add(`state-${state}`);
  }
  // Drives the 3D avatar's ring color/speed and mouth idle shape; a no-op
  // if bot3d.js could not start (see its own WebGL feature check). The
  // word paced mouth animation itself is started per sentence by the
  // speaking_sentence event (see handleServerMessage), which the backend
  // sends right as each sentence's audio begins.
  if (window.bot3d) {
    window.bot3d.setState(state);
  }
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

// Captions: mirrors only the assistant's own current line near the avatar
// (see index.html's #captions-overlay), never the user's own live speech
// transcript, so this reads as subtitles for what the assistant is
// saying, not a two sided transcript. setCaption() is therefore only
// called from the handlers below that carry the assistant's own text
// (updateStreamingReply, finalizeStreamingReply, the greeting, and
// errors), not from updateOrAddPartial/finalizePartial, which handle the
// user's side and still update the full history in #transcript as
// before, just without touching the caption overlay.
const captionsTextEl = document.getElementById("captions-text");

function setCaption(text) {
  if (captionsTextEl) captionsTextEl.textContent = text || "";
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
}

function finalizePartial(text) {
  if (currentPartialBubble) {
    currentPartialBubble.remove();
    currentPartialBubble = null;
  }
  if (text) {
    addBubble("user", text);
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
  setCaption(text);
}

function finalizeStreamingReply(text, mode) {
  if (currentStreamingReplyBubble) {
    currentStreamingReplyBubble.remove();
    currentStreamingReplyBubble = null;
  }
  addBubble("assistant", text, mode);
  setCaption(text);
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

    // Captured audio is always removed from sendBuffer above regardless
    // (so it cannot grow unbounded while paused), but only actually sent
    // while audioSendAllowed is true and the user has not muted
    // themselves; see where audioSendAllowed is set, in setStatus(), for
    // why. This chunk is silently dropped, not queued for later, while
    // the assistant is thinking, speaking, or the mic is muted.
    if (audioSendAllowed && !isMuted) {
      socket.send(chunk.buffer);
    }

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
      finalizePartial(payload.text);
      break;
    case "agent_response_chunk":
      updateStreamingReply(payload.text);
      break;
    case "agent_response":
      finalizeStreamingReply(payload.text, payload.mode);
      break;
    case "speaking_sentence":
      // The reply is now spoken while it is still being generated, so the
      // full text may not exist yet when audio starts; animating one
      // sentence at a time, as each one actually starts playing, keeps
      // the mouth in step with the real audio.
      if (window.bot3d) window.bot3d.speakText(payload.text);
      break;
    case "speaking_done":
      if (window.bot3d) window.bot3d.stopSpeaking();
      break;
    case "user_name":
      // The user introduced themselves by voice ("my name is ...", see
      // _extract_user_name in backend/server.py); remember it the same way
      // a name typed on the user screen is remembered.
      userName = payload.name;
      try {
        localStorage.setItem("userName", payload.name);
      } catch (err) {
        /* localStorage unavailable; the name just will not be remembered next visit */
      }
      break;
    case "end_chat":
      // The backend has just spoken its closing goodbye (see
      // _sounds_like_closing_signal/_CLOSING_GOODBYE_TEXT in
      // backend/server.py) and is about to close the connection on its
      // own; endVoiceChat() below tears the session down the same way a
      // manual cut button press does, so both paths land in one place.
      endVoiceChat();
      break;
    case "error":
      setStatus("error");
      addBubble("assistant", `Error: ${payload.message}`);
      setCaption(`Error: ${payload.message}`);
      break;
    default:
      break;
  }
}

// Mic permission modal: shown once on entering the voice screen, before
// the browser's own native permission prompt, so a first time visitor has
// context for why that prompt is about to appear rather than being
// surprised by an unexplained browser popup. Clicking "Allow microphone"
// runs beginVoiceSession() below: the real getUserMedia() call (and, with
// it, the actual browser prompt) happens as part of startConversation(),
// and the backend speaks its introduction as soon as it connects. Dismissing this modal
// just hides it; the mute button still starts the conversation later if
// someone dismisses this now (see its own click handler).
const micPermissionModal = document.getElementById("mic-permission-modal");
const allowMicButton = document.getElementById("allow-mic-button");
const dismissMicModalButton = document.getElementById("dismiss-mic-modal");

async function showMicPermissionModal() {
  if (!micPermissionModal) return;
  // Best effort: skip the explanatory popup entirely, and go straight to
  // introducing itself and starting, if the browser already reports
  // microphone access as granted, since there is nothing left to explain
  // or ask permission for. Not every browser supports querying the
  // "microphone" permission name, so this silently falls through to
  // showing the modal if the check itself fails.
  try {
    if (navigator.permissions && navigator.permissions.query) {
      const status = await navigator.permissions.query({ name: "microphone" });
      if (status.state === "granted") {
        beginVoiceSession();
        return;
      }
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
    beginVoiceSession();
  });
}

if (dismissMicModalButton) {
  dismissMicModalButton.addEventListener("click", hideMicPermissionModal);
}

// Starts the microphone and backend connection straight away. Text to
// speech is on by default: the backend speaks its introduction out loud,
// addressing the visitor by name or as Guest, as the very first thing in
// the session, then calibrates and starts listening (see voice_ws in
// backend/server.py). greetingsGiven.voice asks it to skip the
// introduction when reconnecting later in the same visit (the mic button
// after a dropped connection), so it only introduces itself once.
async function beginVoiceSession() {
  await startConversation(!greetingsGiven.voice);
  // Only counts as greeted once the session actually started; if the mic
  // was denied, the next attempt should still introduce itself.
  if (isActive) greetingsGiven.voice = true;
}

async function startConversation(greet = false) {
  if (isActive || isConnecting) return;
  isConnecting = true;

  const backendUrl = backendUrlInput.value.trim();
  if (!backendUrl) {
    setCaption("No backend URL is configured.");
    isConnecting = false;
    return;
  }

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setCaption(`Microphone access denied or unavailable: ${err.message}`);
    setStatus("error");
    isConnecting = false;
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
    socket.send(
      JSON.stringify({ type: "start_session", language_hint: selectedLanguage, user_name: userName, greet })
    );
  };

  socket.onmessage = handleServerMessage;

  socket.onclose = () => {
    stopConversation();
  };

  socket.onerror = () => {
    setCaption("Could not reach the backend. Is it running?");
    setStatus("error");
  };

  isActive = true;
  isConnecting = false;
  isMuted = false;
  updateMicButtonUI();
}

function stopConversation() {
  isActive = false;
  isConnecting = false;
  isMuted = false;
  audioSendAllowed = false;
  setStatus("idle");
  updateMicButtonUI();

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

// Mic button doubles as the conversation's only manual start/mute control:
// if the conversation has not started yet (someone dismissed the mic
// permission modal, or the WebSocket dropped), clicking it runs the exact
// same beginVoiceSession() the modal's own Allow button runs; once active,
// it toggles isMuted instead, which pauses sending audio (see
// handleAudioProcess()) without tearing down the connection. Ending the
// conversation outright is the separate end-chat button below, not this one.
const micToggleButton = document.getElementById("mic-toggle");
const micIcon = document.getElementById("mic-icon");

const ICON_MIC_INNER =
  '<rect x="9" y="2" width="6" height="12" rx="3"></rect><path d="M5 10v1a7 7 0 0 0 14 0v-1"></path>' +
  '<line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line>';
const ICON_MIC_OFF_INNER =
  '<line x1="1" y1="1" x2="23" y2="23"></line>' +
  '<path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"></path>' +
  '<path d="M17 16.95A7 7 0 0 1 5 12v-2"></path><path d="M19 10v2a7 7 0 0 1-.11 1.23"></path>' +
  '<line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line>';

function updateMicButtonUI() {
  if (!micToggleButton) return;
  if (!isActive) {
    micToggleButton.setAttribute("aria-pressed", "false");
    micToggleButton.setAttribute("aria-label", "Start the conversation");
    if (micIcon) micIcon.innerHTML = ICON_MIC_INNER;
  } else if (isMuted) {
    micToggleButton.setAttribute("aria-pressed", "true");
    micToggleButton.setAttribute("aria-label", "Unmute microphone");
    if (micIcon) micIcon.innerHTML = ICON_MIC_OFF_INNER;
  } else {
    micToggleButton.setAttribute("aria-pressed", "false");
    micToggleButton.setAttribute("aria-label", "Mute microphone");
    if (micIcon) micIcon.innerHTML = ICON_MIC_INNER;
  }
}

if (micToggleButton) {
  micToggleButton.addEventListener("click", () => {
    if (!isActive) {
      hideMicPermissionModal();
      beginVoiceSession();
      return;
    }
    isMuted = !isMuted;
    updateMicButtonUI();
  });
}

// Ends the conversation outright, unlike mic-toggle's mute (which just
// pauses sending audio). Reused for both a manual tap here and the
// backend's own end_chat event (see handleServerMessage above), so a
// conversation that ends itself, once the user sounds satisfied (see
// _sounds_like_closing_signal in backend/server.py), behaves identically
// to one the user ends by hand.
const endChatButton = document.getElementById("end-chat-button");

function endVoiceChat() {
  stopConversation();
  greetingsGiven.voice = false;
  navigateTo("select");
}

if (endChatButton) {
  endChatButton.addEventListener("click", endVoiceChat);
}

// Logout: this app has no real account system (see KNOWN_LIMITATIONS.md),
// so "logging out" just forgets the locally remembered name and sends the
// visitor back to the very first screen, the closest honest equivalent to
// signing out of something that was never a real login.
const logoutButton = document.getElementById("logout-button");

if (logoutButton) {
  logoutButton.addEventListener("click", () => {
    stopConversation();
    userName = null;
    greetingsGiven.voice = false;
    greetingsGiven.chat = false;
    try {
      localStorage.removeItem("userName");
    } catch (err) {
      /* localStorage unavailable; nothing to clear */
    }
    navigateTo("language");
  });
}

// Captions on/off: toggles only the live overlay's visibility; the full
// transcript in the history panel keeps accumulating either way, since
// captions and history are deliberately separate concerns (see
// index.html's comments on #captions-overlay and #history-panel).
const captionsToggleButton = document.getElementById("captions-toggle");
const captionsOverlay = document.getElementById("captions-overlay");

if (captionsToggleButton) {
  captionsToggleButton.addEventListener("click", () => {
    const nowVisible = captionsToggleButton.getAttribute("aria-pressed") !== "true";
    captionsToggleButton.setAttribute("aria-pressed", String(nowVisible));
    if (captionsOverlay) captionsOverlay.classList.toggle("hidden", !nowVisible);
  });
}

// Conversation history: a simple show/hide overlay over the same
// #transcript element every screen already renders bubbles into, so
// opening it costs nothing beyond toggling one class.
const historyToggleButton = document.getElementById("history-toggle");
const historyPanel = document.getElementById("history-panel");
const closeHistoryButton = document.getElementById("close-history");

if (historyToggleButton) {
  historyToggleButton.addEventListener("click", () => {
    if (historyPanel) historyPanel.classList.remove("hidden");
  });
}

if (closeHistoryButton) {
  closeHistoryButton.addEventListener("click", () => {
    if (historyPanel) historyPanel.classList.add("hidden");
  });
}

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
        body: JSON.stringify({ text, language: selectedLanguage, user_name: userName }),
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
