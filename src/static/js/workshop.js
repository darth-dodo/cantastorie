/* Workshop operator UI — vanilla JS, event delegation, HTMX-friendly */
(function () {
  "use strict";

  // ── Armed delete ──────────────────────────────────────────────────────
  // First tap arms; second tap fires the HTMX request.
  // Outside tap or 3s timeout disarms.
  var _armedTimer = null;

  function disarm(btn) {
    btn.removeAttribute("data-armed");
    btn.textContent = btn.dataset.origLabel || "×";
    if (_armedTimer) { clearTimeout(_armedTimer); _armedTimer = null; }
  }

  document.addEventListener("click", function (e) {
    // Disarm any button not being clicked
    var armed = document.querySelectorAll("[data-delete-btn][data-armed]");
    armed.forEach(function (btn) {
      if (btn !== e.target && !btn.contains(e.target)) disarm(btn);
    });

    var btn = e.target.closest("[data-delete-btn]");
    if (!btn) return;

    if (!btn.hasAttribute("data-armed")) {
      // First tap: arm it
      e.preventDefault();
      e.stopImmediatePropagation();
      if (!btn.dataset.origLabel) btn.dataset.origLabel = btn.textContent.trim();
      btn.setAttribute("data-armed", "1");
      btn.textContent = "Sure?";
      _armedTimer = setTimeout(function () { disarm(btn); }, 3000);
    }
    // Second tap: armed → let HTMX fire naturally (no prevention)
  }, true); // capture phase so we intercept before HTMX

  // ── Audio pill ─────────────────────────────────────────────────────────
  var _playingAudio = null;

  function formatTime(secs) {
    var m = Math.floor(secs / 60);
    var s = Math.floor(secs % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function initAudioPill(pill) {
    var playBtn = pill.querySelector("[data-audio-play]");
    var progress = pill.querySelector("[data-audio-progress]");
    var timeEl = pill.querySelector("[data-audio-time]");
    var audio = pill.querySelector("[data-audio-src]");
    if (!playBtn || !audio) return;

    audio.addEventListener("timeupdate", function () {
      if (!audio.duration) return;
      var pct = (audio.currentTime / audio.duration) * 100;
      if (progress) progress.style.width = pct + "%";
      if (timeEl) timeEl.textContent = formatTime(audio.currentTime);
    });

    audio.addEventListener("ended", function () {
      playBtn.textContent = "▶";
      if (progress) progress.style.width = "0%";
      if (timeEl) timeEl.textContent = "0:00";
      _playingAudio = null;
    });

    playBtn.addEventListener("click", function () {
      if (_playingAudio && _playingAudio !== audio) {
        _playingAudio.pause();
        var otherPill = _playingAudio.closest("[data-audio-pill]");
        if (otherPill) {
          var ob = otherPill.querySelector("[data-audio-play]");
          if (ob) ob.textContent = "▶";
        }
      }
      if (audio.paused) {
        audio.play();
        playBtn.textContent = "⏸";
        _playingAudio = audio;
      } else {
        audio.pause();
        playBtn.textContent = "▶";
        _playingAudio = null;
      }
    });
  }

  // ── Init ───────────────────────────────────────────────────────────────
  function initAll(root) {
    root = root || document;
    root.querySelectorAll("[data-audio-pill]").forEach(initAudioPill);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initAll(document);
  });

  // Re-init after HTMX swaps
  document.addEventListener("htmx:afterSwap", function (e) {
    initAll(e.detail.elt);
  });
})();
