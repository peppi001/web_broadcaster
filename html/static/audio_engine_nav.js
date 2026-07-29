(function () {
  "use strict";

  // Action logging disabled.
  function logLine(_msg) {
    return;
  }

  let uptimeStartedAtMs = null;
  let uptimeTimer = null;

  function plural(count, singular, pluralForm) {
    return count === 1 ? singular : pluralForm;
  }

  function formatUptime(ms) {
    if (!Number.isFinite(ms) || ms < 0) ms = 0;

    const totalMinutes = Math.floor(ms / 60000);
    const days = Math.floor(totalMinutes / (60 * 24));
    const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
    const minutes = totalMinutes % 60;

    const parts = [];
    if (days > 0) parts.push(days + " " + plural(days, "day", "days"));
    if (hours > 0 || days > 0) parts.push(hours + " " + plural(hours, "hour", "hours"));
    parts.push(minutes + " " + plural(minutes, "minute", "minutes"));

    if (parts.length === 1) return parts[0];
    if (parts.length === 2) return parts[0] + " and " + parts[1];
    return parts[0] + ", " + parts[1] + " and " + parts[2];
  }

  function updateUptimeNow() {
    const el = document.getElementById("audio-engine-uptime");
    if (!el) return;

    if (!uptimeStartedAtMs) {
      el.textContent = "";
      return;
    }

    const diff = Date.now() - uptimeStartedAtMs;
    el.textContent = "Uptime: " + formatUptime(diff);
  }

  function setUptime(startedAtIso, running) {
    const el = document.getElementById("audio-engine-uptime");
    if (!el) return;

    if (!running || !startedAtIso) {
      uptimeStartedAtMs = null;
      el.textContent = "";
      el.classList.add("hidden");
      if (uptimeTimer) {
        clearInterval(uptimeTimer);
        uptimeTimer = null;
      }
      return;
    }

    const started = new Date(startedAtIso);
    if (Number.isNaN(started.getTime())) {
      uptimeStartedAtMs = null;
      el.textContent = "";
      el.classList.add("hidden");
      if (uptimeTimer) {
        clearInterval(uptimeTimer);
        uptimeTimer = null;
      }
      return;
    }

    uptimeStartedAtMs = started.getTime();
    el.classList.remove("hidden");
    updateUptimeNow();

    if (!uptimeTimer) {
      uptimeTimer = setInterval(updateUptimeNow, 1000);
    }
  }

  function isRunning(statusData) {
    if (!statusData) return false;
    if (statusData.status && String(statusData.status).toLowerCase() === "running") return true;
    return Boolean(statusData.pid);
  }

  async function fetchAudioEngineStatus() {
    try {
      const res = await fetch("/api/audio-engine/status", { cache: "no-store" });
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      logLine(`Status fetch failed: ${e && e.message ? e.message : String(e)}`);
      return null;
    }
  }

  function setButtonState(btn, running) {
    if (!btn) return;
    const rockerImg = document.getElementById("audio-engine-rocker");
    const statusBadge = document.getElementById("audio-engine-status-badge");
    const onSrc = btn.dataset.onSrc;
    const offSrc = btn.dataset.offSrc;
    if (running) {
      btn.classList.remove("audio-engine-start");
      btn.classList.add("audio-engine-stop");
      btn.dataset.state = "running";

	      if (rockerImg && onSrc && rockerImg.tagName === "IMG") rockerImg.src = onSrc;
      btn.title = "Stop";
      btn.setAttribute("aria-label", "Stop");

      if (statusBadge) {
        statusBadge.textContent = "RUNNING";
        statusBadge.classList.remove("badge-off");
        statusBadge.classList.add("badge-on");
      }
    } else {
      btn.classList.remove("audio-engine-stop");
      btn.classList.add("audio-engine-start");
      btn.dataset.state = "stopped";

	      if (rockerImg && offSrc && rockerImg.tagName === "IMG") rockerImg.src = offSrc;
      btn.title = "Start";
      btn.setAttribute("aria-label", "Start");

      if (statusBadge) {
        statusBadge.textContent = "STOPPED";
        statusBadge.classList.remove("badge-on");
        statusBadge.classList.add("badge-off");
      }
    }
  }

  async function refresh(btn) {
    const statusData = await fetchAudioEngineStatus();
    const running = isRunning(statusData);
    setButtonState(btn, running);
    setUptime(statusData ? statusData.started_at : null, running);
  }

  async function toggleAudioEngine(btn) {
    if (!btn || btn.disabled) return false;

    const running = btn.dataset.state === "running";
    const cmd = running ? "stop" : "start";

    logLine(`Click: ${cmd.toUpperCase()} (running=${running ? "yes" : "no"})`);

    if (cmd === "stop") {
      const ok = await confirmStop();
      if (!ok) return false;
    }

    btn.disabled = true;
    try {
      const res = await fetch("/audio-engine/" + cmd, { method: "POST" });
      logLine(`HTTP ${res.status} ${res.ok ? "OK" : "ERROR"} -> /audio-engine/${cmd}`);
      const bodyText = await res.text();
      if (bodyText) {
        try {
          const obj = JSON.parse(bodyText);
          if (obj && typeof obj === "object") {
            if (obj.success === false && obj.error) {
              logLine(`Server error: ${obj.error}`);
            } else {
              logLine(`Response: ${bodyText}`);
            }
          } else {
            logLine(`Response: ${bodyText}`);
          }
        } catch (e) {
          logLine(`Response: ${bodyText}`);
        }
      }
    } catch (e) {
      logLine(`Fetch failed: ${e && e.message ? e.message : String(e)}`);
    }

    await refresh(btn);
    btn.disabled = false;
    return true;
  }

  window.webBroadcasterAudioEngine = window.webBroadcasterAudioEngine || {};
  window.webBroadcasterAudioEngine.refresh = refresh;
  window.webBroadcasterAudioEngine.toggle = toggleAudioEngine;
  window.confirmStop = confirmStop;
  window.initStopConfirmModal = initStopConfirmModal;

  document.addEventListener("DOMContentLoaded", function () {
    initStopConfirmModal();

    const btn = document.getElementById("audio-engine-toggle");
    if (!btn) return;

    btn.addEventListener("click", async function () {
      await toggleAudioEngine(btn);
    });

    refresh(btn);
    setInterval(function () {
      refresh(btn);
    }, 5000);
  });
})();

function centerStopConfirmModal(backdrop) {
  if (!backdrop) return;
  const modal = backdrop.querySelector(".modal--floating") || backdrop.querySelector(".studio-floating-window") || backdrop;
  if (!modal) return;
  const width = modal.offsetWidth || parseInt(modal.style.width, 10) || 420;
  const height = modal.offsetHeight || parseInt(modal.style.height, 10) || 180;
  modal.style.left = Math.max(12, Math.round((window.innerWidth - width) / 2)) + "px";
  modal.style.top = Math.max(12, Math.round((window.innerHeight - height) / 2)) + "px";
}

function enableStopConfirmModalDrag(backdrop) {
  if (!backdrop || backdrop.dataset.dragReady === "true") return;
  const modal = backdrop.querySelector(".modal--floating") || backdrop.querySelector(".studio-floating-window") || backdrop;
  const titlebar = document.getElementById("stop-confirm-titlebar");
  const closeBtn = document.getElementById("stop-confirm-close");
  if (!modal || !titlebar) return;
  backdrop.dataset.dragReady = "true";
  let dragState = null;
  function stopDrag() {
    dragState = null;
    document.body.classList.remove("modal-dragging");
  }
  titlebar.addEventListener("pointerdown", function (event) {
    if (closeBtn && event.target === closeBtn) return;
    if (event.button !== 0) return;
    dragState = {
      startX: event.clientX,
      startY: event.clientY,
      left: modal.offsetLeft,
      top: modal.offsetTop
    };
    document.body.classList.add("modal-dragging");
    try { titlebar.setPointerCapture(event.pointerId); } catch (error) {}
    event.preventDefault();
  });
  titlebar.addEventListener("pointermove", function (event) {
    if (!dragState) return;
    const nextLeft = dragState.left + (event.clientX - dragState.startX);
    const nextTop = dragState.top + (event.clientY - dragState.startY);
    const maxLeft = Math.max(12, window.innerWidth - modal.offsetWidth - 12);
    const maxTop = Math.max(12, window.innerHeight - modal.offsetHeight - 12);
    modal.style.left = Math.min(Math.max(12, nextLeft), maxLeft) + "px";
    modal.style.top = Math.min(Math.max(12, nextTop), maxTop) + "px";
  });
  titlebar.addEventListener("pointerup", stopDrag);
  titlebar.addEventListener("pointercancel", stopDrag);
}

function initStopConfirmModal() {
  const backdrop = document.getElementById("stop-confirm-backdrop");
  if (!backdrop || backdrop.dataset.initDone === "true") return;

  const yesBtn = document.getElementById("stop-confirm-yes");
  const noBtn = document.getElementById("stop-confirm-no");
  const closeBtn = document.getElementById("stop-confirm-close");

  function hide() {
    backdrop.classList.remove("active");
    backdrop.setAttribute("aria-hidden", "true");
  }

  backdrop.dataset.initDone = "true";
  backdrop.style.zIndex = "10000";
  enableStopConfirmModalDrag(backdrop);

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && backdrop.classList.contains("active")) {
      hide();
    }
    if (e.key === "Enter" && backdrop.classList.contains("active")) {
      backdrop.dataset.result = "yes";
      hide();
    }
  });

  if (closeBtn) closeBtn.addEventListener("click", hide);
  if (noBtn) noBtn.addEventListener("click", hide);

  if (yesBtn) {
    yesBtn.addEventListener("click", function () {
      backdrop.dataset.result = "yes";
      hide();
    });
  }
}

function confirmStop() {
  const backdrop = document.getElementById("stop-confirm-backdrop");
  if (!backdrop) return Promise.resolve(true);

  backdrop.dataset.result = "";
  backdrop.style.zIndex = "10000";
  backdrop.classList.add("active");
  backdrop.setAttribute("aria-hidden", "false");
  centerStopConfirmModal(backdrop);

  const yesBtn = document.getElementById("stop-confirm-yes");
  if (yesBtn) {
    setTimeout(function () {
      try { yesBtn.focus(); } catch (e) {}
    }, 0);
  }

  return new Promise(function (resolve) {
    const start = Date.now();
    const timer = setInterval(function () {
      const result = backdrop.dataset.result;
      if (result === "yes") {
        clearInterval(timer);
        resolve(true);
        return;
      }

      if (!backdrop.classList.contains("active")) {
        clearInterval(timer);
        resolve(false);
        return;
      }

      if (Date.now() - start > 60000) {
        clearInterval(timer);
        backdrop.classList.remove("active");
        resolve(false);
      }
    }, 100);
  });
}
