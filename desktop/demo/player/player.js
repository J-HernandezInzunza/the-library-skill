/**
 * The storyboard player: a visible cursor, and the steps that drive it.
 *
 * Plain browser JS, injected into the page rather than bundled, so it never enters the
 * app's module graph and has no build step of its own.
 *
 * Deliberately knows nothing about what is driving it. The recorder in ../record is one
 * caller; Playwright's `page.addInitScript` + `page.evaluate` would be another, unchanged.
 * Everything a driver needs is on `window.__demo`.
 *
 * Why the clicking happens here rather than in the driver: a recording is judged on
 * pacing, and pacing made of round trips is pacing you cannot repeat. A step that waits
 * 400ms waits 400ms every take. It also sidesteps the 30s ceiling on a single BrowserOS
 * neo `run` call, since the page plays the whole storyboard while the driver only watches.
 *
 * The one thing it does not fake is hit-testing: a click is dispatched to whatever
 * `elementFromPoint` reports under the cursor, not to the element the step looked up. A
 * step aimed at something covered by an overlay fails here exactly as it would for a hand
 * on the mouse, which is the property that makes these recordings worth trusting.
 */
(() => {
  const CURSOR_ID = "__demo_cursor";
  const MOVE_MS = 520;
  const SETTLE_MS = 120;

  /** macOS-ish arrow: white fill, dark outline, so it reads on light and dark panels. */
  const CURSOR_SVG = `
    <svg width="24" height="28" viewBox="0 0 24 28" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 2.2 L3 21.4 L8.1 16.7 L11.4 24.4 L14.9 22.9 L11.6 15.4 L18.4 15.1 Z"
            fill="#fff" stroke="#11121a" stroke-width="1.6" stroke-linejoin="round"/>
    </svg>`;

  function ensureCursor() {
    let cursor = document.getElementById(CURSOR_ID);
    if (cursor) return cursor;

    cursor = document.createElement("div");
    cursor.id = CURSOR_ID;
    cursor.innerHTML = CURSOR_SVG;
    Object.assign(cursor.style, {
      position: "fixed",
      top: "0",
      left: "0",
      // Never let the decoration eat the click it is decorating.
      pointerEvents: "none",
      zIndex: "2147483647",
      // The arrow tip is the hotspot, so the SVG hangs down-right of the coordinate.
      transform: "translate(0px, 0px)",
      filter: "drop-shadow(0 2px 4px rgba(0,0,0,.45))",
      opacity: "0",
      transition: "opacity 180ms ease",
    });
    document.body.appendChild(cursor);
    return cursor;
  }

  const state = {
    x: window.innerWidth * 0.5,
    y: window.innerHeight * 0.62,
  };

  function placeCursor(x, y) {
    state.x = x;
    state.y = y;
    const cursor = ensureCursor();
    cursor.style.transform = `translate(${x}px, ${y}px)`;
    cursor.style.opacity = "1";
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));

  /** Ease-in-out: a constant-speed cursor is the tell that nobody is holding it. */
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

  async function glideTo(x, y, ms = MOVE_MS) {
    const fromX = state.x;
    const fromY = state.y;
    const start = performance.now();

    // A zero-distance move still needs the cursor placed, and dividing by the duration
    // below would otherwise run a full tween to where it already is.
    if (Math.hypot(x - fromX, y - fromY) < 1) {
      placeCursor(x, y);
      return;
    }

    for (;;) {
      const elapsed = performance.now() - start;
      const t = Math.min(1, elapsed / ms);
      const eased = ease(t);
      placeCursor(fromX + (x - fromX) * eased, fromY + (y - fromY) * eased);
      if (t >= 1) return;
      await frame();
    }
  }

  /** A short ring at the click point, so the moment of the click is visible in a frame. */
  async function ripple(x, y) {
    const ring = document.createElement("div");
    Object.assign(ring.style, {
      position: "fixed",
      left: `${x}px`,
      top: `${y}px`,
      width: "12px",
      height: "12px",
      margin: "-6px 0 0 -6px",
      borderRadius: "50%",
      border: "2px solid rgba(255,255,255,.9)",
      boxShadow: "0 0 0 1px rgba(0,0,0,.5)",
      pointerEvents: "none",
      zIndex: "2147483646",
      transform: "scale(.4)",
      opacity: "1",
      transition: "transform 380ms ease-out, opacity 380ms ease-out",
    });
    document.body.appendChild(ring);
    await frame();
    ring.style.transform = "scale(2.6)";
    ring.style.opacity = "0";
    setTimeout(() => ring.remove(), 420);
  }

  function visible(element) {
    const box = element.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  }

  /**
   * The smallest visible element whose own text is `text`.
   *
   * Smallest, because every ancestor up to `<body>` also contains the string, and the
   * useful target is the leaf. Own text, so a card is not matched by its child's label.
   */
  function byText(text, exact) {
    const wanted = text.trim();
    const hits = [];
    for (const element of document.querySelectorAll("body *")) {
      if (!visible(element)) continue;
      const own = Array.from(element.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent.trim())
        .join(" ")
        .trim();
      if (!own) continue;
      const match = exact ? own === wanted : own.includes(wanted);
      if (match) hits.push(element);
    }
    if (!hits.length) return null;
    return hits.sort((a, b) => {
      const boxA = a.getBoundingClientRect();
      const boxB = b.getBoundingClientRect();
      return boxA.width * boxA.height - boxB.width * boxB.height;
    })[0];
  }

  function locate(step) {
    if (step.sel) {
      const found = document.querySelector(step.sel);
      return found && visible(found) ? found : null;
    }
    if (step.text) return byText(step.text, step.exact !== false);
    throw new Error(`step needs "sel" or "text": ${JSON.stringify(step)}`);
  }

  async function waitForStep(step, timeout = 8000) {
    const deadline = performance.now() + timeout;
    for (;;) {
      const found = locate(step);
      if (found) return found;
      if (performance.now() > deadline) {
        throw new Error(
          `timed out after ${timeout}ms waiting for ${step.sel ?? `text "${step.text}"`}`,
        );
      }
      await sleep(60);
    }
  }

  /** Bring the target into view before aiming at it, or the cursor points off-screen. */
  async function reveal(element) {
    const box = element.getBoundingClientRect();
    const inside = box.top >= 0 && box.bottom <= window.innerHeight;
    if (inside) return;
    element.scrollIntoView({ block: "center", behavior: "smooth" });
    // Smooth scrolling has no completion event; settle by watching the box stop moving.
    let previous = null;
    for (let tick = 0; tick < 40; tick++) {
      await sleep(50);
      const top = element.getBoundingClientRect().top;
      if (previous !== null && Math.abs(top - previous) < 0.5) return;
      previous = top;
    }
  }

  /** Where on the element to aim. Defaults to the centre. */
  function aimPoint(element, step) {
    const box = element.getBoundingClientRect();
    const atX = step.at?.x ?? 0.5;
    const atY = step.at?.y ?? 0.5;
    return { x: box.left + box.width * atX, y: box.top + box.height * atY };
  }

  /**
   * Dispatch a click at a point, to whatever is actually under it.
   *
   * The full pointer/mouse sequence rather than `element.click()`: a control that reacts
   * on `mousedown` (and the app has them) would otherwise show none of its pressed state,
   * and a recording of a button that never looks pressed is the thing being recorded.
   */
  function clickAt(x, y) {
    const hit = document.elementFromPoint(x, y);
    if (!hit) throw new Error(`nothing under the cursor at ${Math.round(x)},${Math.round(y)}`);

    const shared = { bubbles: true, cancelable: true, clientX: x, clientY: y, view: window };
    hit.dispatchEvent(new PointerEvent("pointerdown", { ...shared, pointerId: 1 }));
    hit.dispatchEvent(new MouseEvent("mousedown", shared));
    hit.dispatchEvent(new PointerEvent("pointerup", { ...shared, pointerId: 1 }));
    hit.dispatchEvent(new MouseEvent("mouseup", shared));
    hit.dispatchEvent(new MouseEvent("click", shared));
    return hit;
  }

  const steps = {
    async pause(step) {
      await sleep(step.ms ?? 600);
    },

    async wait(step) {
      await waitForStep(step, step.timeout);
      await sleep(step.settle ?? SETTLE_MS);
    },

    async hover(step) {
      const element = await waitForStep(step, step.timeout);
      await reveal(element);
      const point = aimPoint(element, step);
      await glideTo(point.x, point.y, step.ms ?? MOVE_MS);
      const hit = document.elementFromPoint(point.x, point.y);
      hit?.dispatchEvent(
        new MouseEvent("mouseover", {
          bubbles: true,
          clientX: point.x,
          clientY: point.y,
          view: window,
        }),
      );
      await sleep(step.settle ?? SETTLE_MS);
    },

    async click(step) {
      const element = await waitForStep(step, step.timeout);
      await reveal(element);
      const point = aimPoint(element, step);
      await glideTo(point.x, point.y, step.ms ?? MOVE_MS);
      // A beat between arriving and pressing; landing and clicking in one frame reads as
      // a teleport even with the cursor drawn.
      await sleep(step.before ?? 180);
      await ripple(point.x, point.y);
      clickAt(point.x, point.y);
      await sleep(step.settle ?? 260);
    },

    async type(step) {
      const element = await waitForStep(step, step.timeout);
      await reveal(element);
      const point = aimPoint(element, step);
      await glideTo(point.x, point.y, step.ms ?? MOVE_MS);
      await ripple(point.x, point.y);
      clickAt(point.x, point.y);
      element.focus();

      const perChar = step.perChar ?? 55;
      for (const character of step.text ?? "") {
        element.value = (element.value ?? "") + character;
        element.dispatchEvent(new Event("input", { bubbles: true }));
        await sleep(perChar);
      }
      element.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(step.settle ?? 260);
    },
  };

  const demo = {
    /** `idle` | `running` | `done` | `failed`. The recorder polls this. */
    state: "idle",
    error: null,
    /** The index of the step being run, for a failure message that says where. */
    at: -1,

    /** Put the cursor somewhere sensible without moving it, before the first frame. */
    arm(x, y) {
      placeCursor(x ?? window.innerWidth * 0.5, y ?? window.innerHeight * 0.62);
      const cursor = ensureCursor();
      cursor.style.opacity = "1";
    },

    hide() {
      const cursor = document.getElementById(CURSOR_ID);
      if (cursor) cursor.style.opacity = "0";
    },

    async play(storyboard) {
      const list = Array.isArray(storyboard) ? storyboard : storyboard.steps;
      demo.state = "running";
      demo.error = null;
      demo.at = -1;

      try {
        for (let index = 0; index < list.length; index++) {
          const step = list[index];
          demo.at = index;
          const run = steps[step.do];
          if (!run) throw new Error(`unknown step "${step.do}"`);
          await run(step);
        }
        demo.state = "done";
      } catch (failure) {
        demo.error = `step ${demo.at} (${JSON.stringify(list[demo.at] ?? null)}): ${failure.message}`;
        demo.state = "failed";
      }
      return { state: demo.state, error: demo.error };
    },
  };

  window.__demo = demo;
})();
