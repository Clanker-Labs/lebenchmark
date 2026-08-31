/* The core — behaviour. Clanker-Labs/branding · brand/core.md
 *
 * No dependencies, no build step, no animation loop. Everything that moves is
 * a CSS animation; this file only ever sets an attribute or a custom property.
 * That is deliberate: a dashboard is left open for days on a machine that has
 * been OOM-killed, and a requestAnimationFrame loop runs for all of them.
 */
(function (global) {
  "use strict";

  const STATES = ["idle", "listening", "thinking", "speaking", "alert"];

  const MARKUP =
    '<div class="clanker-core__glow"></div>' +
    '<div class="clanker-core__ring clanker-core__ring--outer"></div>' +
    '<div class="clanker-core__ring clanker-core__ring--mid"></div>' +
    '<div class="clanker-core__ring clanker-core__ring--inner"></div>' +
    '<div class="clanker-core__voice"></div>' +
    '<div class="clanker-core__orb"></div>';

  /* How fast the displayed level falls back to zero. Rising is instant and
     falling is smoothed, which matches how hearing works: you notice a sound
     starting immediately and its ending gradually. A symmetric smoother makes
     speech look like it lags behind the speaker. */
  const DECAY = 0.82;

  class Core {
    constructor(el) {
      this.el = el;
      if (!el.querySelector(".clanker-core__orb")) el.innerHTML = MARKUP;
      el.classList.add("clanker-core");
      this._level = 0;
      this._decayTimer = null;
      this.setState(el.dataset.state || "idle");
    }

    setState(state) {
      if (!STATES.includes(state)) {
        // Named rather than ignored: a typo'd state silently doing nothing is
        // how a UI ends up permanently idle while the agent is working.
        console.warn("core: unknown state " + state + "; expected one of " + STATES.join(", "));
        return this;
      }
      // `alert` re-armed by removing the attribute first, so the one-shot
      // animation restarts even when the state was already alert. Without this
      // a second alert while the first is showing produces no motion at all.
      if (state === "alert" && this.el.dataset.state === "alert") {
        this.el.removeAttribute("data-state");
        void this.el.offsetWidth;         // force a reflow so the animation resets
      }
      this.el.dataset.state = state;
      if (state !== "listening") this.setLevel(0);
      return this;
    }

    get state() { return this.el.dataset.state; }

    /** Voice amplitude, 0..1. Clamped, because a level above 1 grows the ring
     *  past its own container and looks like a bug rather than a loud noise. */
    setLevel(level) {
      const next = Math.max(0, Math.min(1, Number(level) || 0));
      this._level = next > this._level ? next : this._level * DECAY + next * (1 - DECAY);
      this.el.style.setProperty("--core-level", this._level.toFixed(3));
      return this;
    }

    /** Drive the level from a MediaStream. Returns a stop function.
     *
     *  This is the one place a loop is unavoidable — an analyser has to be
     *  polled — so it exists only while listening and the caller is handed the
     *  means to stop it. It stops itself if the track ends.
     */
    listenTo(stream, AudioCtx) {
      const Ctx = AudioCtx || global.AudioContext || global.webkitAudioContext;
      if (!Ctx) {
        console.warn("core: no AudioContext; level will not follow the microphone");
        return function () {};
      }
      const ctx = new Ctx();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      // Smoothed by the analyser itself, which is cheaper than doing it here
      // and is what stops the ring twitching on the noise floor.
      analyser.smoothingTimeConstant = 0.8;
      ctx.createMediaStreamSource(stream).connect(analyser);

      const buf = new Uint8Array(analyser.frequencyBinCount);
      let running = true;
      const self = this;

      function tick() {
        if (!running) return;
        analyser.getByteTimeDomainData(buf);
        // RMS around the 128 midpoint, scaled so ordinary speech sits near the
        // middle of the range rather than pinned at either end.
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        self.setLevel(Math.sqrt(sum / buf.length) * 3.2);
        global.requestAnimationFrame(tick);
      }
      tick();

      return function stop() {
        running = false;
        self.setLevel(0);
        ctx.close().catch(function () {});
      };
    }
  }

  Core.attach = function (elOrSelector) {
    const el = typeof elOrSelector === "string"
      ? document.querySelector(elOrSelector) : elOrSelector;
    if (!el) throw new Error("core: no element for " + elOrSelector);
    return new Core(el);
  };
  Core.STATES = STATES;

  if (typeof module !== "undefined" && module.exports) module.exports = Core;
  else global.Core = Core;
})(typeof window !== "undefined" ? window : globalThis);
