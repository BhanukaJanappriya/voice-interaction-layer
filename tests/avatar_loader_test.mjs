// Run with: node --test tests/avatar_loader_test.mjs
//
// The new avatar must never leave the page without a face: when WebGL, import
// maps or the assets are missing, avatar.js dispatches "avatar:fallback" on
// document and resolves ready with false, and loader.js removes its stage so
// the legacy bot3d.js avatar stays in charge. No browser is needed: the DOM
// pieces these files touch are faked.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const avatarDir = path.join(here, "..", "frontend", "avatar");
const indexHtml = fs.readFileSync(path.join(here, "..", "frontend", "index.html"), "utf8");

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
}

function makeDocument({ webgl }) {
  const listeners = {};
  const fired = [];
  const doc = {
    readyState: "complete",
    fired,
    createElement(tag) {
      if (tag === "canvas") return { getContext: () => (webgl ? {} : null) };
      return { style: {}, classList: { add() {}, remove() {} }, appendChild() {}, setAttribute() {} };
    },
    addEventListener(type, fn) {
      (listeners[type] = listeners[type] || []).push(fn);
    },
    dispatchEvent(event) {
      fired.push(event);
      for (const fn of listeners[event.type] || []) fn(event);
      return true;
    },
    querySelector: () => null,
    getElementById: () => null,
    head: { appendChild() {} },
  };
  return doc;
}

let counter = 0;
// avatar.js keeps state at module level, so each scenario gets a fresh copy.
async function loadAvatar({ webgl = true, importmap = true } = {}) {
  const doc = makeDocument({ webgl });
  const win = { devicePixelRatio: 1, addEventListener() {}, removeEventListener() {} };
  globalThis.window = win;
  globalThis.document = doc;
  globalThis.CustomEvent = FakeEvent;
  globalThis.HTMLScriptElement = { supports: (what) => (what === "importmap" ? importmap : false) };
  globalThis.fetch = async () => {
    throw new Error("network is down");
  };
  const source = fs.readFileSync(path.join(avatarDir, "avatar.js"), "utf8");
  const copy = path.join(os.tmpdir(), `avatar_test_copy_${process.pid}_${counter++}.mjs`);
  fs.writeFileSync(copy, source);
  try {
    await import(pathToFileURL(copy).href);
  } finally {
    fs.rmSync(copy, { force: true });
  }
  return { renderer: win.avatarRenderer, doc };
}

const container = () => ({ appendChild() {}, style: {}, classList: { add() {}, remove() {} } });

test("avatar.js exposes the contract surface", async () => {
  const { renderer } = await loadAvatar();
  assert.ok(renderer, "window.avatarRenderer must exist");
  for (const name of ["init", "setGender", "setState", "speakAudio", "stop", "destroy"]) {
    assert.equal(typeof renderer[name], "function", name);
  }
  assert.ok(renderer.ready && typeof renderer.ready.then === "function", "ready is a promise");
});

test("no WebGL falls back with a fallback event and ready false", async () => {
  const { renderer, doc } = await loadAvatar({ webgl: false });
  const ok = await renderer.init(container(), {});
  assert.equal(ok, false);
  assert.equal(await renderer.ready, false);
  const event = doc.fired.find((e) => e.type === "avatar:fallback");
  assert.ok(event, "avatar:fallback was not dispatched");
  assert.equal(event.detail.reason, "no-webgl");
  assert.equal(renderer.isActive(), false);
});

test("no import map support falls back", async () => {
  const { renderer, doc } = await loadAvatar({ importmap: false });
  assert.equal(await renderer.init(container(), {}), false);
  assert.equal(doc.fired.find((e) => e.type === "avatar:fallback").detail.reason, "no-importmap");
});

test("no container falls back", async () => {
  const { renderer, doc } = await loadAvatar();
  assert.equal(await renderer.init(null, {}), false);
  assert.equal(doc.fired.find((e) => e.type === "avatar:fallback").detail.reason, "no-container");
});

test("a failed asset or library load falls back instead of throwing", async () => {
  // WebGL looks fine here, but TalkingHead cannot be imported in plain Node
  // (it needs the browser and an import map), the same effect as a 404.
  const { renderer, doc } = await loadAvatar();
  const ok = await renderer.init(container(), { initTimeoutMs: 5000 });
  assert.equal(ok, false);
  const event = doc.fired.find((e) => e.type === "avatar:fallback");
  assert.ok(event, "avatar:fallback was not dispatched");
  assert.match(event.detail.reason, /import-failed|init-failed|asset-missing/);
  assert.equal(renderer.isActive(), false);
});

test("methods are safe to call after a fallback", async () => {
  const { renderer } = await loadAvatar({ webgl: false });
  await renderer.init(container(), {});
  assert.doesNotThrow(() => {
    renderer.setState("speaking");
    renderer.stop();
    renderer.destroy();
  });
});

// loader.js

function runLoader({ config, supportsImportmap = true, search = "" }) {
  const listeners = {};
  const classes = new Set();
  const stage = { parentNode: null };
  const container = {
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
    appendChild(el) {
      el.parentNode = container;
      container.children.push(el);
    },
    removeChild(el) {
      container.children = container.children.filter((c) => c !== el);
    },
    children: [],
  };
  const doc = {
    readyState: "complete",
    currentScript: { src: "http://localhost/avatar/loader.js" },
    getElementById: (id) => (id === "bot-avatar" ? container : null),
    createElement: () => ({ style: {} }),
    querySelector: () => null,
    head: { appendChild() {} },
    addEventListener: (type, fn) => (listeners[type] = listeners[type] || []).push(fn),
    dispatchEvent(event) {
      for (const fn of listeners[event.type] || []) fn(event);
    },
  };
  const requested = [];
  const win = {
    location: { search },
    fetch: async (url) => {
      requested.push(url);
      if (config === "error") throw new Error("offline");
      if (config === "404") return { ok: false };
      return { ok: true, json: async () => ({ avatar_renderer: config }) };
    },
    console,
    HTMLScriptElement: { supports: () => supportsImportmap },
  };
  const context = vm.createContext({
    window: win,
    document: doc,
    fetch: win.fetch,
    console,
    JSON,
    HTMLScriptElement: win.HTMLScriptElement,
    Function: function () {
      // new Function("u", "return import(u)") in a browser; here the import fails like a 404.
      return () => Promise.reject(new Error("Failed to fetch dynamically imported module"));
    },
    setTimeout,
  });
  win.document = doc;
  const code = fs.readFileSync(path.join(avatarDir, "loader.js"), "utf8");
  new vm.Script(code, { filename: "loader.js" }).runInContext(context);
  return { win, doc, container, classes, requested };
}

const settle = () => new Promise((r) => setTimeout(r, 30));

test("loader leaves the legacy avatar alone for renderer legacy", async () => {
  const { win, container, classes } = runLoader({ config: "legacy" });
  await settle();
  assert.equal(container.children.length, 0, "no stage may be added");
  assert.equal(classes.has("has-th"), false);
  assert.equal(win.avatarBridge.mode, "legacy");
});

test("loader stays legacy when the config route is missing or the network is down", async () => {
  for (const config of ["404", "error"]) {
    const { win, container } = runLoader({ config });
    await settle();
    assert.equal(container.children.length, 0, config);
    assert.equal(win.avatarBridge.mode, "legacy", config);
  }
});

test("loader cleans up and reports fallback when the avatar module fails to load", async () => {
  const { win, container, classes } = runLoader({ config: "talkinghead" });
  await settle();
  assert.equal(win.avatarBridge.mode, "fallback");
  assert.equal(container.children.length, 0, "the th stage must be removed");
  assert.equal(classes.has("has-th"), false, "has-th must never be set for a failed avatar");
});

test("loader falls back when import maps are unsupported", async () => {
  const { win, container } = runLoader({ config: "talkinghead", supportsImportmap: false });
  await settle();
  assert.equal(win.avatarBridge.mode, "fallback");
  assert.equal(container.children.length, 0);
});

test("avatar:fallback event removes the new stage and has-th", async () => {
  const { win, doc, container, classes } = runLoader({ config: "talkinghead", search: "?avatar=talkinghead" });
  classes.add("has-th");
  doc.dispatchEvent(new FakeEvent("avatar:fallback", { detail: { reason: "no-webgl" } }));
  assert.equal(classes.has("has-th"), false);
  assert.equal(win.avatarBridge.mode, "fallback");
});

test("avatarBridge is safe to call at any time", async () => {
  const { win } = runLoader({ config: "legacy" });
  await settle();
  const bridge = win.avatarBridge;
  assert.doesNotThrow(() => {
    bridge.setState("listening");
    bridge.setGender("male");
    bridge.attachAudioStream(null);
    bridge.speakAudio(null);
    bridge.speakSentence("Hello", 100);
    bridge.stop();
  });
  assert.equal(bridge.isActive(), false);
});

test("index.html keeps the legacy avatar, so a fallback has something to fall back to", () => {
  assert.match(indexHtml, /<script[^>]+src=["']bot3d\.js["']/);
  assert.match(indexHtml, /<canvas[^>]+id=["']bot-canvas["']/);
});
