/* SlimeFi — real-time 3D slime mascot (Three.js r128 UMD global build).
 *
 * Frameless glossy jelly slime rendered live with WebGL for the Home hero.
 * Replaces the flat pet photo. FAILS SILENTLY: if THREE failed to load, or
 * WebGL is unavailable, mount() returns false and the fallback <img> inside
 * #slime3d stays visible. The fallback is hidden only AFTER the first
 * successful frame has rendered.
 *
 * API: window.Slime3D = { mount(el) }
 * Auto-mounts on DOMContentLoaded (or immediately) if #slime3d exists.
 *
 * Performance (low-end Android Telegram webviews): sphere segments <= 48,
 * 42 sparkle points, 10 bubbles, no post-processing, no transmission
 * material, DPR capped at 2. Rendering pauses when the tab is hidden or
 * the Home view is not the active view.
 */
(function () {
  'use strict';

  /* ---------- canvas-generated textures ---------- */

  function starTexture() {
    var c = document.createElement('canvas');
    c.width = c.height = 64;
    var g = c.getContext('2d');
    var grad = g.createRadialGradient(32, 32, 0, 32, 32, 30);
    grad.addColorStop(0, 'rgba(255,255,255,0.9)');
    grad.addColorStop(0.4, 'rgba(255,246,200,0.45)');
    grad.addColorStop(1, 'rgba(255,246,200,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    g.fillStyle = 'rgba(255,255,255,1)';
    g.beginPath(); // 4-point star
    g.moveTo(32, 3);
    g.quadraticCurveTo(36.5, 27.5, 61, 32);
    g.quadraticCurveTo(36.5, 36.5, 32, 61);
    g.quadraticCurveTo(27.5, 36.5, 3, 32);
    g.quadraticCurveTo(27.5, 27.5, 32, 3);
    g.fill();
    return new THREE.CanvasTexture(c);
  }

  function shadowTexture() {
    var c = document.createElement('canvas');
    c.width = 128; c.height = 64;
    var g = c.getContext('2d');
    var grad = g.createRadialGradient(64, 32, 2, 64, 32, 60);
    grad.addColorStop(0, 'rgba(2,8,14,0.55)');
    grad.addColorStop(0.6, 'rgba(2,8,14,0.28)');
    grad.addColorStop(1, 'rgba(2,8,14,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 64);
    return new THREE.CanvasTexture(c);
  }

  /* ---------- mount ---------- */

  function mount(el) {
    if (!el || el.__slime3d) return false;
    if (typeof THREE === 'undefined') return false;

    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        alpha: true, antialias: true, powerPreference: 'low-power'
      });
    } catch (e) { return false; }
    if (!renderer) return false;
    try { if (!renderer.getContext()) return false; } catch (e2) { return false; }

    el.__slime3d = true;
    var fallbackImg = el.querySelector('img');

    var W = el.clientWidth || 150;
    var H = el.clientHeight || 150;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(W, H);
    renderer.setClearColor(0x000000, 0);
    el.appendChild(renderer.domElement);

    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(38, W / H, 0.1, 50);
    camera.position.set(0, 0.1, 3.95);
    camera.lookAt(0, -0.02, 0);

    /* lights: ambient + key + cool rim (no shadows, no post FX) */
    scene.add(new THREE.AmbientLight(0x9db8ff, 0.55));
    var key = new THREE.DirectionalLight(0xffffff, 0.8);
    key.position.set(1.6, 2.4, 4.2);
    scene.add(key);
    var rim = new THREE.DirectionalLight(0x8fe3ff, 0.45);
    rim.position.set(-3.5, 1.2, -2.5);
    scene.add(rim);

    var slime = new THREE.Group();
    scene.add(slime);

    /* soft ground shadow (fake, textured plane — not part of the slime
       group so it stays put while the slime hops) */
    var shadow = new THREE.Mesh(
      new THREE.PlaneGeometry(2.7, 1.35),
      new THREE.MeshBasicMaterial({
        map: shadowTexture(), transparent: true, depthWrite: false
      })
    );
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.y = -1.07;
    shadow.renderOrder = 0;
    scene.add(shadow);

    /* gel puddle base */
    var puddle = new THREE.Mesh(
      new THREE.SphereGeometry(1.02, 40, 24),
      new THREE.MeshPhongMaterial({
        color: 0x4aa8ff, transparent: true, opacity: 0.9,
        shininess: 85, specular: 0xcfe9ff
      })
    );
    puddle.scale.set(1.02, 0.16, 1.02);
    puddle.position.y = -0.88;
    puddle.renderOrder = 1;
    slime.add(puddle);

    /* inner core — deeper blue, gives the jelly its depth */
    var core = new THREE.Mesh(
      new THREE.SphereGeometry(0.8, 40, 28),
      new THREE.MeshPhongMaterial({
        color: 0x2f8fff, transparent: true, opacity: 0.5,
        shininess: 60, specular: 0x9fd4ff, depthWrite: false
      })
    );
    core.scale.set(1, 0.86, 1);
    core.position.y = 0.02;
    core.renderOrder = 2;
    slime.add(core);

    /* bubbles floating inside the jelly */
    var bubbleMat = new THREE.MeshPhongMaterial({
      color: 0xffffff, transparent: true, opacity: 0.4,
      shininess: 120, specular: 0xffffff, depthWrite: false
    });
    var bubbles = [];
    for (var i = 0; i < 10; i++) {
      var b = new THREE.Mesh(
        new THREE.SphereGeometry(0.03 + Math.random() * 0.05, 12, 10),
        bubbleMat
      );
      var ang = Math.random() * Math.PI * 2;
      var rad = Math.random() * 0.55;
      b.position.set(
        Math.cos(ang) * rad,
        -0.5 + Math.random() * 1.0,
        Math.sin(ang) * rad * 0.7 + 0.15
      );
      b.renderOrder = 3;
      b.userData.speed = 0.05 + Math.random() * 0.1;
      slime.add(b);
      bubbles.push(b);
    }

    /* main jelly dome — vertices wobbled on the CPU each frame */
    var domeGeo = new THREE.SphereGeometry(1, 44, 30);
    var domeBase = domeGeo.attributes.position.array.slice();
    var dome = new THREE.Mesh(domeGeo, new THREE.MeshPhongMaterial({
      color: 0x74c0ff, transparent: true, opacity: 0.92,
      shininess: 55, specular: 0xa9d6ff, depthWrite: false
    }));
    dome.scale.set(1, 0.94, 1);
    dome.renderOrder = 4;
    slime.add(dome);

    /* eyes: dark glossy ovals with a white highlight each */
    function makeEye(side) {
      var grp = new THREE.Group();
      var eye = new THREE.Mesh(
        new THREE.SphereGeometry(0.125, 24, 18),
        new THREE.MeshPhongMaterial({
          color: 0x0a1c38, shininess: 130, specular: 0xffffff
        })
      );
      eye.scale.set(0.85, 1.35, 0.5);
      eye.renderOrder = 6;
      grp.add(eye);
      var hl = new THREE.Mesh(
        new THREE.SphereGeometry(0.038, 12, 10),
        new THREE.MeshBasicMaterial({ color: 0xffffff })
      );
      hl.position.set(-0.03 * side, 0.07, 0.055);
      hl.renderOrder = 6;
      grp.add(hl);
      grp.position.set(0.30 * side, 0.10, 0.92);
      grp.rotation.y = 0.3 * side;
      return grp;
    }
    var eyeL = makeEye(-1);
    var eyeR = makeEye(1);
    slime.add(eyeL);
    slime.add(eyeR);

    /* blush: small pink translucent flattened spheres on the cheeks */
    var blushMat = new THREE.MeshBasicMaterial({
      color: 0xff9ec7, transparent: true, opacity: 0.55, depthWrite: false
    });
    [-1, 1].forEach(function (side) {
      var bl = new THREE.Mesh(new THREE.SphereGeometry(0.11, 18, 14), blushMat);
      bl.scale.set(1.35, 0.65, 0.35);
      bl.position.set(0.52 * side, -0.18, 0.78);
      bl.rotation.y = 0.55 * side;
      bl.renderOrder = 6;
      slime.add(bl);
    });

    /* sparkles: THREE.Points with the generated star sprite */
    var SPARKS = 42;
    var sPos = new Float32Array(SPARKS * 3);
    var sBase = new Float32Array(SPARKS * 3);
    var sPhase = new Float32Array(SPARKS);
    for (var s = 0; s < SPARKS; s++) {
      var sa = Math.random() * Math.PI * 2;
      var sr = 1.15 + Math.random() * 0.5;
      sBase[s * 3] = Math.cos(sa) * sr;
      sBase[s * 3 + 1] = -0.7 + Math.random() * 1.75;
      sBase[s * 3 + 2] = Math.sin(sa) * sr;
      sPhase[s] = Math.random() * Math.PI * 2;
    }
    sPos.set(sBase);
    var sparkGeo = new THREE.BufferGeometry();
    sparkGeo.setAttribute('position', new THREE.BufferAttribute(sPos, 3));
    var sparks = new THREE.Points(sparkGeo, new THREE.PointsMaterial({
      size: 0.17, map: starTexture(), transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending, color: 0xfff3b0, opacity: 0.95
    }));
    sparks.renderOrder = 6;
    scene.add(sparks);

    /* ---------- animation ---------- */

    var clock = new THREE.Clock();
    var nextHop = 2.5 + Math.random() * 2;
    var hopStart = -1;
    var bounceStart = -1;
    var blinkStart = -1;
    var nextBlink = 2 + Math.random() * 2.5;
    var firstFrame = true;

    function animate() {
      var dt = Math.min(clock.getDelta(), 0.05);
      var t = clock.elapsedTime;

      /* jelly wobble: displace dome vertices along their normals */
      var pos = domeGeo.attributes.position;
      var arr = pos.array;
      for (var i = 0; i < arr.length; i += 3) {
        var bx = domeBase[i], by = domeBase[i + 1], bz = domeBase[i + 2];
        var len = Math.sqrt(bx * bx + by * by + bz * bz) || 1;
        var off = Math.sin(t * 2.3 + by * 4.0 + bx * 2.5) * 0.018;
        arr[i] = bx + (bx / len) * off;
        arr[i + 1] = by + (by / len) * off;
        arr[i + 2] = bz + (bz / len) * off;
      }
      pos.needsUpdate = true;
      domeGeo.computeVertexNormals();

      /* breathing squash */
      var br = Math.sin(t * 1.9);
      var sy = 1 + br * 0.03;
      var sxz = 1 - br * 0.014;

      /* gentle hop every ~4s */
      var hopY = 0;
      if (hopStart < 0 && t > nextHop) hopStart = t;
      if (hopStart >= 0) {
        var hp = (t - hopStart) / 0.62;
        if (hp >= 1) {
          hopStart = -1;
          nextHop = t + 3.2 + Math.random() * 1.6;
        } else {
          hopY = Math.sin(hp * Math.PI) * 0.17;
        }
      }

      /* tap squash-and-stretch */
      var bSy = 1, bSxz = 1;
      if (bounceStart >= 0) {
        var bp = (t - bounceStart) / 0.55;
        if (bp >= 1) {
          bounceStart = -1;
        } else {
          var bs = Math.sin(bp * Math.PI);
          bSy = 1 - 0.26 * bs;
          bSxz = 1 + 0.16 * bs;
        }
      }

      slime.position.y = hopY;
      slime.scale.set(sxz * bSxz, sy * bSy, sxz * bSxz);

      /* blink */
      if (blinkStart < 0 && t > nextBlink) blinkStart = t;
      var eyeSY = 1;
      if (blinkStart >= 0) {
        var ep = (t - blinkStart) / 0.16;
        if (ep >= 1) {
          blinkStart = -1;
          nextBlink = t + 2.4 + Math.random() * 2.4;
        } else {
          eyeSY = 1 - 0.88 * Math.sin(ep * Math.PI);
        }
      }
      eyeL.scale.y = eyeSY;
      eyeR.scale.y = eyeSY;

      /* bubbles drift up and wrap */
      for (var bi = 0; bi < bubbles.length; bi++) {
        var bb = bubbles[bi];
        bb.position.y += bb.userData.speed * dt;
        if (bb.position.y > 0.55) bb.position.y = -0.55;
      }

      /* sparkles: slow orbit + per-point bob */
      sparks.rotation.y = t * 0.18;
      var sp = sparkGeo.attributes.position.array;
      for (var pi = 0; pi < SPARKS; pi++) {
        sp[pi * 3 + 1] = sBase[pi * 3 + 1] + Math.sin(t * 1.3 + sPhase[pi]) * 0.05;
      }
      sparkGeo.attributes.position.needsUpdate = true;
      sparks.material.opacity = 0.8 + Math.sin(t * 2.1) * 0.18;

      renderer.render(scene, camera);

      if (firstFrame) {
        firstFrame = false;
        if (fallbackImg) fallbackImg.style.display = 'none';
        el.classList.add('slime3d-on');
      }
    }

    /* ---------- run / pause gating ---------- */

    var loopOn = false;
    var lastW = W, lastH = H;

    function resizeIfNeeded() {
      var w = el.clientWidth, h = el.clientHeight;
      if (!w || !h || (w === lastW && h === lastH)) return;
      lastW = w; lastH = h;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }

    function shouldRun() {
      if (document.hidden) return false;
      var home = document.getElementById('view-home');
      if (home && !home.classList.contains('active')) return false;
      return true;
    }

    function gate() {
      resizeIfNeeded();
      var run = shouldRun();
      if (run && !loopOn) {
        loopOn = true;
        clock.getDelta(); /* swallow the paused gap */
        renderer.setAnimationLoop(animate);
      } else if (!run && loopOn) {
        loopOn = false;
        renderer.setAnimationLoop(null);
      }
    }
    setInterval(gate, 500);
    document.addEventListener('visibilitychange', gate);
    window.addEventListener('resize', resizeIfNeeded);
    gate();

    /* tap the slime -> bounce */
    renderer.domElement.addEventListener('pointerdown', function () {
      if (bounceStart < 0) bounceStart = clock.elapsedTime;
    });

    return true;
  }

  window.Slime3D = { mount: mount };

  function auto() {
    var el = document.getElementById('slime3d');
    if (el) mount(el);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', auto);
  } else {
    auto();
  }
})();
