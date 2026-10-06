/*
 * voicekit.js: a small, dependency free browser client for the voicekit
 * text to speech API (POST /api/tts/stream and WS /ws/tts).
 *
 *   const kit = new VoiceKit({ baseUrl: "https://host", apiKey: "..." });
 *   kit.onTimings = (words) => { ... };
 *   await kit.speak("Hello there.", { language: "en", voice: "female_natural" });
 *
 * Works as a plain <script> (window.VoiceKit) or a CommonJS / bundler module.
 *
 * Audio plays through WebAudio. Every chunk is scheduled to start exactly
 * when the previous one ends, on the audio clock, so there are no gaps or
 * clicks however unevenly the chunks arrive.
 *
 * Aligning words with the audio: the server sends word times in ms from the
 * start of the whole utterance. kit.audioStartTime is the AudioContext time at
 * which that utterance's audio starts, so a word is heard at
 * kit.audioStartTime + word.start_ms / 1000 on kit.audioContext.currentTime.
 * onTimings receives words already carrying startTime and endTime in that
 * clock, and kit.currentTime gives the playback position in ms, so a render
 * loop can do: words.filter(w => w.start_ms <= kit.currentTime).
 *
 * Limitations: word times exist only when the provider supplies them
 * (edge-tts yes, Google no); in that case use kit.analyser for lip sync.
 * Browsers start audio only after a user gesture, so call speak() from a click.
 */
(function (root) {
  "use strict";

  var LEAD_SECONDS = 0.08; // cushion before the first chunk, absorbs jitter

  function VoiceKit(options) {
    options = options || {};
    this.baseUrl = (options.baseUrl || "").replace(/\/+$/, "");
    this.apiKey = options.apiKey || "";
    this.transport = options.transport === "ws" ? "ws" : "http";
    this.onTimings = options.onTimings || null; // (words) => void
    this.onStart = options.onStart || null; // () => void, first audio scheduled
    this.onEnd = options.onEnd || null; // ({reason}) => void
    this.onError = options.onError || null; // (Error) => void
    this.onSentence = options.onSentence || null; // ({text, sentence_index}) => void
    this.audioStartTime = 0; // AudioContext time of utterance t = 0
    this._ctx = null;
    this._analyser = null;
    this._reset();
  }

  VoiceKit.prototype._reset = function () {
    this._nextTime = 0;
    this._sources = [];
    this._streamDone = false;
    this._started = false;
    this._finished = true;
    this._abort = null;
    this._socket = null;
    this._resolve = null;
    this.audioStartTime = 0;
  };

  Object.defineProperty(VoiceKit.prototype, "audioContext", {
    get: function () {
      if (!this._ctx) {
        var Ctx = root.AudioContext || root.webkitAudioContext;
        this._ctx = new Ctx();
        this._analyser = this._ctx.createAnalyser();
        this._analyser.fftSize = 512;
        this._analyser.connect(this._ctx.destination);
      }
      return this._ctx;
    },
  });

  /** AnalyserNode on the output, for audio driven lip sync. */
  Object.defineProperty(VoiceKit.prototype, "analyser", {
    get: function () {
      this.audioContext; // creates both
      return this._analyser;
    },
  });

  /** Playback position of the current utterance in ms (0 before it starts). */
  Object.defineProperty(VoiceKit.prototype, "currentTime", {
    get: function () {
      if (!this._ctx || !this._started) return 0;
      return Math.max(0, (this._ctx.currentTime - this.audioStartTime) * 1000);
    },
  });

  /** Converts a word start_ms (utterance time) to the AudioContext clock. */
  VoiceKit.prototype.audioTimeFor = function (ms) {
    return this.audioStartTime + ms / 1000;
  };

  VoiceKit.prototype.speak = function (text, opts) {
    var self = this;
    opts = opts || {};
    this.stop();
    var ctx = this.audioContext;
    if (ctx.state === "suspended") ctx.resume();
    this._finished = false;
    this._streamDone = false;
    this._started = false;
    return new Promise(function (resolve) {
      self._resolve = resolve;
      var start = self.transport === "ws" ? self._speakWs : self._speakHttp;
      start.call(self, text, opts.language || "en", opts.voice || "female_natural");
    });
  };

  VoiceKit.prototype._headers = function () {
    return { "Content-Type": "application/json", "X-API-Key": this.apiKey };
  };

  VoiceKit.prototype._speakHttp = function (text, language, voice) {
    var self = this;
    var controller = new AbortController();
    this._abort = controller;
    fetch(this.baseUrl + "/api/tts/stream", {
      method: "POST",
      headers: this._headers(),
      body: JSON.stringify({ text: text, language: language, voice: voice, format: "ndjson" }),
      signal: controller.signal,
    })
      .then(function (res) {
        if (!res.ok) {
          return res.text().then(function (body) {
            throw new Error("voicekit HTTP " + res.status + " " + body.slice(0, 120));
          });
        }
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";
        function pump() {
          return reader.read().then(function (r) {
            if (r.done) {
              if (buffer.trim()) self._handleMessage(JSON.parse(buffer));
              self._streamEnded();
              return;
            }
            buffer += decoder.decode(r.value, { stream: true });
            var lines = buffer.split("\n");
            buffer = lines.pop();
            for (var i = 0; i < lines.length; i++) {
              if (lines[i].trim()) self._handleMessage(JSON.parse(lines[i]));
            }
            return pump();
          });
        }
        return pump();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        self._fail(err);
      });
  };

  VoiceKit.prototype._speakWs = function (text, language, voice) {
    var self = this;
    var base = this.baseUrl || root.location.origin;
    var url = base.replace(/^http/, "ws") + "/ws/tts?api_key=" + encodeURIComponent(this.apiKey);
    var socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    this._socket = socket;
    this._wsRate = 24000;
    socket.onopen = function () {
      socket.send(JSON.stringify({ type: "speak", text: text, language: language, voice: voice }));
    };
    socket.onmessage = function (ev) {
      if (typeof ev.data === "string") {
        self._handleMessage(JSON.parse(ev.data));
      } else {
        self._playPcm(new Int16Array(ev.data), self._wsRate);
      }
    };
    socket.onerror = function () {
      self._fail(new Error("voicekit WebSocket error"));
    };
    socket.onclose = function () {
      if (self._socket === socket) self._streamEnded();
    };
  };

  VoiceKit.prototype._handleMessage = function (m) {
    switch (m.type) {
      case "audio": // ndjson
        this._playPcm(base64ToInt16(m.pcm), m.sample_rate);
        break;
      case "audio_start": // ws
        this._wsRate = m.sample_rate;
        break;
      case "timing": // ndjson, one word
        this._emitTimings([m]);
        break;
      case "timings": // ws, a batch
        this._emitTimings(m.words);
        break;
      case "sentence":
        if (this.onSentence) this.onSentence(m);
        break;
      case "done":
        this._streamEnded();
        break;
      case "error":
        this._fail(new Error(m.message || "voicekit error"));
        break;
    }
  };

  VoiceKit.prototype._emitTimings = function (words) {
    if (!this.onTimings || !words.length) return;
    var self = this;
    // audioStartTime is final once the first audio is scheduled; timings for
    // that audio arrive before or with it, so compute times lazily here.
    var out = words.map(function (w) {
      return {
        text: w.text,
        start_ms: w.start_ms,
        duration_ms: w.duration_ms,
        sentence_index: w.sentence_index,
        get startTime() {
          return self.audioTimeFor(w.start_ms);
        },
        get endTime() {
          return self.audioTimeFor(w.start_ms + w.duration_ms);
        },
      };
    });
    this.onTimings(out);
  };

  VoiceKit.prototype._playPcm = function (int16, rate) {
    if (this._finished || !int16.length) return;
    var ctx = this.audioContext;
    var buffer = ctx.createBuffer(1, int16.length, rate);
    var channel = buffer.getChannelData(0);
    for (var i = 0; i < int16.length; i++) channel[i] = int16[i] / 32768;
    var node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(this._analyser);

    var now = ctx.currentTime;
    if (!this._started) {
      this._nextTime = now + LEAD_SECONDS;
      this.audioStartTime = this._nextTime;
      this._started = true;
      if (this.onStart) this.onStart();
    } else if (this._nextTime < now) {
      // The network was slower than playback: there is an audible gap. Move
      // the utterance clock by the gap so word times stay aligned.
      var gap = now + 0.02 - this._nextTime;
      this.audioStartTime += gap;
      this._nextTime = now + 0.02;
    }
    node.start(this._nextTime);
    this._nextTime += buffer.duration;
    this._sources.push(node);
    var self = this;
    node.onended = function () {
      var i = self._sources.indexOf(node);
      if (i >= 0) self._sources.splice(i, 1);
      self._maybeFinish();
    };
  };

  VoiceKit.prototype._streamEnded = function () {
    this._streamDone = true;
    this._maybeFinish();
  };

  VoiceKit.prototype._maybeFinish = function () {
    if (this._streamDone && this._sources.length === 0) this._finish("done");
  };

  VoiceKit.prototype._finish = function (reason) {
    if (this._finished) return;
    this._finished = true;
    var resolve = this._resolve;
    this._resolve = null;
    this._closeTransport();
    if (this.onEnd) this.onEnd({ reason: reason });
    if (resolve) resolve({ reason: reason });
  };

  VoiceKit.prototype._fail = function (err) {
    if (this._finished) return;
    if (this.onError) this.onError(err);
    this._stopSources();
    this._finish("error");
  };

  VoiceKit.prototype._closeTransport = function () {
    if (this._abort) {
      this._abort.abort();
      this._abort = null;
    }
    if (this._socket) {
      var socket = this._socket;
      this._socket = null;
      try {
        if (socket.readyState === 1) socket.send(JSON.stringify({ type: "cancel" }));
        socket.close();
      } catch (e) {
        /* already closed */
      }
    }
  };

  VoiceKit.prototype._stopSources = function () {
    var sources = this._sources;
    this._sources = [];
    sources.forEach(function (s) {
      s.onended = null;
      try {
        s.stop();
      } catch (e) {
        /* not started yet */
      }
    });
  };

  /** Stops speaking at once (barge in): audio, network and synthesis. */
  VoiceKit.prototype.stop = function () {
    if (this._finished) return;
    this._stopSources();
    this._finish("stopped");
  };

  function base64ToInt16(b64) {
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Int16Array(bytes.buffer, 0, bytes.length >> 1);
  }

  if (typeof module !== "undefined" && module.exports) module.exports = VoiceKit;
  root.VoiceKit = VoiceKit;
})(typeof window !== "undefined" ? window : this);
