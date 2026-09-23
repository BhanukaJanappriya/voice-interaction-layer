/*
 * 3D talking bot avatar (Three.js / WebGL).
 *
 * Renders a small, lit, gradient shaded head with blinking eyes and a
 * mouth that opens and closes per pipeline state, and, while the
 * assistant is speaking, steps through a sequence of mouth shapes paced
 * to the actual words in the reply (see speakText()).
 *
 * Honest limitation: this is a text paced approximation of lip sync, not
 * true audio driven viseme animation. The synthesized speech audio plays
 * on the backend machine's own speakers and never reaches the browser
 * (see ARCHITECTURE.md), so there is no audio waveform here to analyze.
 * Timing each word at a natural speech rate and guessing a mouth shape
 * from its vowels is the most honest approximation achievable without
 * streaming synthesized audio to the browser, which would be a real
 * architecture change (see README.md's suggested next steps), not a
 * frontend only one.
 *
 * Exposes window.bot3d = { available, setState(state), speakText(text),
 * stopSpeaking() }. If WebGL or the Three.js script failed to load,
 * available is false and every method is a no-op; app.js keeps driving
 * the flat CSS avatar in style.css either way (see the .bot.has-3d rule),
 * so the avatar still animates, just without the 3D layer.
 */

(function () {
  const container = document.getElementById("bot-avatar");
  const canvas = document.getElementById("bot-canvas");

  function supportsWebGL() {
    try {
      const test = document.createElement("canvas");
      return !!(
        window.WebGLRenderingContext &&
        (test.getContext("webgl") || test.getContext("experimental-webgl"))
      );
    } catch (err) {
      return false;
    }
  }

  if (!container || !canvas || typeof THREE === "undefined" || !supportsWebGL()) {
    console.log(
      "bot3d: 3D avatar unavailable (WebGL or the Three.js script did not load); using the flat CSS avatar instead."
    );
    window.bot3d = {
      available: false,
      setState() {},
      speakText() {},
      stopSpeaking() {},
    };
    return;
  }

  const COLORS = {
    idle: 0x2f6fed,
    calibrating: 0xe8a13d,
    listening: 0x2f6fed,
    thinking: 0xe8a13d,
    speaking: 0x43a047,
    error: 0xe05a5a,
  };

  const SIZE = 132;
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(SIZE, SIZE, false);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
  camera.position.set(0, 0, 6.2);

  // A blue to teal to green gradient texture, matching the brand palette,
  // generated on an offscreen 2D canvas since Three.js materials do not
  // take a CSS style gradient directly.
  function gradientTexture() {
    const c = document.createElement("canvas");
    c.width = 2;
    c.height = 256;
    const ctx = c.getContext("2d");
    const grad = ctx.createLinearGradient(0, 0, 0, 256);
    grad.addColorStop(0, "#2f6fed");
    grad.addColorStop(0.55, "#3cc8dc");
    grad.addColorStop(1, "#43a047");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 2, 256);
    const tex = new THREE.CanvasTexture(c);
    tex.needsUpdate = true;
    return tex;
  }

  const head = new THREE.Mesh(
    new THREE.SphereGeometry(1.7, 48, 48),
    new THREE.MeshStandardMaterial({ map: gradientTexture(), roughness: 0.35, metalness: 0.15 })
  );
  scene.add(head);

  const eyeGeometry = new THREE.SphereGeometry(0.16, 16, 16);
  const eyeMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.2 });
  const eyeLeft = new THREE.Mesh(eyeGeometry, eyeMaterial);
  const eyeRight = new THREE.Mesh(eyeGeometry, eyeMaterial);
  eyeLeft.position.set(-0.55, 0.35, 1.42);
  eyeRight.position.set(0.55, 0.35, 1.42);
  head.add(eyeLeft, eyeRight);

  // A flattened sphere stands in for the mouth: base scale gives it a
  // thin, wide resting shape, and speakText()/the animation loop scale it
  // further per word to approximate open/rounded/closed mouth shapes.
  const MOUTH_BASE_SCALE = { x: 1.0, y: 0.35, z: 0.55 };
  const mouth = new THREE.Mesh(
    new THREE.SphereGeometry(0.34, 24, 16),
    new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.3 })
  );
  mouth.scale.set(MOUTH_BASE_SCALE.x, MOUTH_BASE_SCALE.y, MOUTH_BASE_SCALE.z);
  mouth.position.set(0, -0.55, 1.45);
  head.add(mouth);

  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(2.05, 0.05, 12, 64),
    new THREE.MeshBasicMaterial({ color: COLORS.idle, transparent: true, opacity: 0.3 })
  );
  scene.add(ring);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const keyLight = new THREE.DirectionalLight(0xffffff, 0.9);
  keyLight.position.set(3, 4, 5);
  scene.add(keyLight);
  const rimLight = new THREE.DirectionalLight(0x66ffe0, 0.5);
  rimLight.position.set(-4, -2, -3);
  scene.add(rimLight);

  let currentState = "idle";
  let ringSpinSpeed = 0.004;

  function setState(state) {
    currentState = state;
    const color = COLORS[state] !== undefined ? COLORS[state] : COLORS.idle;
    ring.material.color.setHex(color);
    ringSpinSpeed =
      state === "speaking" ? 0.02 : state === "thinking" ? 0.015 : state === "listening" ? 0.01 : 0.004;
    if (state !== "speaking") {
      stopSpeaking();
    }
  }

  // A deliberately simple, explicitly approximate viseme guess from a
  // word's vowels: rounded for o/u, wide for a/e, mid for i, mostly
  // closed for consonant heavy words. Good enough to look like the mouth
  // is following the actual words without pretending to be phoneme
  // accurate lip sync.
  function wordShape(word) {
    const w = word.toLowerCase();
    if (/[ou]/.test(w)) return { x: 0.7, y: 1.7 };
    if (/[ae]/.test(w)) return { x: 1.35, y: 2.1 };
    if (/i/.test(w)) return { x: 1.05, y: 1.2 };
    return { x: 0.8, y: 0.6 };
  }

  const WORDS_PER_MINUTE = 165; // a natural conversational pace
  let speakTimer = null;
  let mouthTargetFactor = { x: 1, y: 0.6 };

  function speakText(text) {
    stopSpeaking();
    const words = (text || "").split(/\s+/).filter(Boolean);
    if (words.length === 0) return;

    const msPerWord = 60000 / WORDS_PER_MINUTE;
    let index = 0;

    function step() {
      if (index >= words.length) {
        mouthTargetFactor = { x: 1, y: 0.6 };
        speakTimer = null;
        return;
      }
      mouthTargetFactor = wordShape(words[index]);
      index += 1;
      speakTimer = setTimeout(step, msPerWord);
    }
    step();
  }

  function stopSpeaking() {
    if (speakTimer) {
      clearTimeout(speakTimer);
      speakTimer = null;
    }
    mouthTargetFactor = { x: 1, y: 0.6 };
  }

  // Reads the same --mx/--my custom properties app.js already writes on
  // mousemove for the background glow, rather than adding a second
  // mousemove listener here.
  function pointerFraction() {
    const styles = getComputedStyle(document.documentElement);
    const mx = parseFloat(styles.getPropertyValue("--mx")) || 50;
    const my = parseFloat(styles.getPropertyValue("--my")) || 30;
    return { x: (mx - 50) / 50, y: (my - 30) / 50 };
  }

  let blinkPhase = Math.random() * Math.PI * 2;
  let idlePhase = 0;

  function animate() {
    requestAnimationFrame(animate);

    idlePhase += 0.01;
    const pointer = pointerFraction();
    head.rotation.y += (pointer.x * 0.4 - head.rotation.y) * 0.05;
    head.rotation.x += (-pointer.y * 0.25 - head.rotation.x) * 0.05;
    head.rotation.y += Math.sin(idlePhase * 0.5) * 0.0015;
    head.position.y = Math.sin(idlePhase) * 0.04;

    blinkPhase += 0.02;
    const blinking = Math.sin(blinkPhase * 0.15) > 0.985;
    const blink = blinking ? 0.15 : 1;
    eyeLeft.scale.y += (blink - eyeLeft.scale.y) * 0.6;
    eyeRight.scale.y += (blink - eyeRight.scale.y) * 0.6;

    const talking = currentState === "speaking" ? 1 : 0;
    const targetX = MOUTH_BASE_SCALE.x * (1 + (mouthTargetFactor.x - 1) * talking);
    const targetY = MOUTH_BASE_SCALE.y * (talking ? mouthTargetFactor.y : 1);
    mouth.scale.x += (targetX - mouth.scale.x) * 0.35;
    mouth.scale.y += (targetY - mouth.scale.y) * 0.35;

    head.scale.setScalar(currentState === "thinking" ? 1 + Math.sin(idlePhase * 4) * 0.02 : 1);

    ring.rotation.z += ringSpinSpeed;
    ring.material.opacity += ((currentState === "idle" ? 0.25 : 0.55) - ring.material.opacity) * 0.05;

    renderer.render(scene, camera);
  }
  animate();

  container.classList.add("has-3d");
  window.bot3d = { available: true, setState, speakText, stopSpeaking };
})();
