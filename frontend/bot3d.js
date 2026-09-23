/*
 * 3D talking bot avatar (Three.js / WebGL).
 *
 * Renders a small, lit, gradient shaded head with hair, a two tone hat,
 * ears, and blinking eyes whose pupils and whose head rotation both track
 * the cursor (see pointerFraction() and its use in animate()), plus a
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

  // Eyes are white spheres with a small dark pupil each; the pupils shift
  // within the eye toward the cursor every frame (see animate()'s pointer
  // tracking below), which reads as the face actually looking at the
  // cursor, not just the head turning toward it.
  const eyeGeometry = new THREE.SphereGeometry(0.18, 16, 16);
  const eyeMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.2 });
  const pupilGeometry = new THREE.SphereGeometry(0.075, 12, 12);
  const pupilMaterial = new THREE.MeshStandardMaterial({ color: 0x16222c, roughness: 0.4 });

  const eyeLeft = new THREE.Mesh(eyeGeometry, eyeMaterial);
  const eyeRight = new THREE.Mesh(eyeGeometry, eyeMaterial);
  eyeLeft.position.set(-0.58, 0.35, 1.45);
  eyeRight.position.set(0.58, 0.35, 1.45);
  head.add(eyeLeft, eyeRight);

  const pupilLeft = new THREE.Mesh(pupilGeometry, pupilMaterial);
  const pupilRight = new THREE.Mesh(pupilGeometry, pupilMaterial);
  pupilLeft.position.set(0, 0, 0.15);
  pupilRight.position.set(0, 0, 0.15);
  eyeLeft.add(pupilLeft);
  eyeRight.add(pupilRight);

  // A flattened sphere stands in for the mouth: base scale gives it a
  // wide, noticeably sized resting shape (bigger than a realistic human
  // mouth would be, deliberately, so it reads clearly at this avatar's
  // small on screen size), and speakText()/the animation loop scale it
  // further per word to approximate open/rounded/closed mouth shapes.
  const MOUTH_BASE_SCALE = { x: 1.4, y: 0.55, z: 0.6 };
  const mouth = new THREE.Mesh(
    new THREE.SphereGeometry(0.4, 24, 16),
    new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.3 })
  );
  mouth.scale.set(MOUTH_BASE_SCALE.x, MOUTH_BASE_SCALE.y, MOUTH_BASE_SCALE.z);
  mouth.position.set(0, -0.55, 1.45);
  head.add(mouth);

  // Ears: small flattened spheres on either side of the head, tinted with
  // the brand blue so they read as a deliberate feature rather than a
  // rendering glitch.
  const earGeometry = new THREE.SphereGeometry(0.32, 16, 16);
  const earMaterial = new THREE.MeshStandardMaterial({ color: 0x2f6fed, roughness: 0.4 });
  const earLeft = new THREE.Mesh(earGeometry, earMaterial);
  const earRight = new THREE.Mesh(earGeometry, earMaterial);
  earLeft.scale.set(0.55, 0.9, 0.4);
  earRight.scale.set(0.55, 0.9, 0.4);
  earLeft.position.set(-1.55, 0.05, 0.15);
  earRight.position.set(1.55, 0.05, 0.15);
  head.add(earLeft, earRight);

  // Hat: a small full sphere ("topper") centered right at the head's own
  // north pole, plus a contrasting band around its equator. Centering it
  // at the pole, rather than trying to fit a brim shape at some lower,
  // wider latitude of the head, means its lower half naturally buries
  // itself in the head's rapidly narrowing top (where the head's own
  // radius is small) with no gap and no geometry math to get precisely
  // right, while its upper half is simply outside the head sphere and
  // fully visible; the band is sized to clear both the dome and the
  // head's own (very small, this close to the pole) radius at that
  // height, so, unlike an earlier version of this that used a brim
  // positioned lower down, it does not end up embedded inside the
  // opaque head and invisible.
  const hatDome = new THREE.Mesh(
    new THREE.SphereGeometry(0.85, 24, 16),
    new THREE.MeshStandardMaterial({ color: 0x2f6fed, roughness: 0.4 })
  );
  hatDome.position.set(0, 1.78, 0);
  head.add(hatDome);

  const hatBrim = new THREE.Mesh(
    new THREE.CylinderGeometry(0.89, 0.89, 0.22, 24, 1, true),
    new THREE.MeshStandardMaterial({ color: 0x43a047, roughness: 0.4, side: THREE.DoubleSide })
  );
  hatBrim.position.set(0, 1.74, 0);
  head.add(hatBrim);

  // Hair: a fringe band just below the hat brim, built as a partial
  // sphere slightly wider than the head so it does not z-fight with it.
  // thetaStart begins right where the brim ends and thetaLength stops
  // well above the eyes, so hat, hair, and face stack cleanly instead of
  // the hair sphere (which wraps all the way around at any given theta)
  // hiding the brim behind it.
  const hair = new THREE.Mesh(
    new THREE.SphereGeometry(1.73, 32, 16, 0, Math.PI * 2, Math.PI * 0.33, Math.PI * 0.09),
    new THREE.MeshStandardMaterial({ color: 0x2b2320, roughness: 0.65 })
  );
  head.add(hair);

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
    // Cursor tracking: the head turns toward the pointer (a wide range, at
    // a snappy but still smoothed catch up rate) and the pupils shift
    // inside the eyes toward it too, so the face reads as actually
    // looking at the cursor rather than just idly rotating.
    const pointer = pointerFraction();
    head.rotation.y += (pointer.x * 0.9 - head.rotation.y) * 0.12;
    head.rotation.x += (-pointer.y * 0.55 - head.rotation.x) * 0.12;
    head.rotation.y += Math.sin(idlePhase * 0.5) * 0.0015;
    head.position.y = Math.sin(idlePhase) * 0.04;

    const pupilOffsetX = Math.max(-1, Math.min(1, pointer.x)) * 0.075;
    const pupilOffsetY = Math.max(-1, Math.min(1, -pointer.y)) * 0.06;
    pupilLeft.position.x += (pupilOffsetX - pupilLeft.position.x) * 0.3;
    pupilLeft.position.y += (pupilOffsetY - pupilLeft.position.y) * 0.3;
    pupilRight.position.x += (pupilOffsetX - pupilRight.position.x) * 0.3;
    pupilRight.position.y += (pupilOffsetY - pupilRight.position.y) * 0.3;

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
