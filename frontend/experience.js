"use strict";
(() => {
  const find = (id) => document.getElementById(id);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  let manualPause = false,
    snapshot = null,
    previousTransfer = null,
    previousChunks = new Set();
  let previousChart = "",
    inspectIndex = null;
  const counterValues = new Map(),
    counterAnimations = new Map(),
    chartAnimations = new Set();
  const motionEnabled = () =>
    !manualPause && !reduced.matches && !document.hidden;

  function updateMotion() {
    document.body.classList.toggle("motion-paused", !motionEnabled());
    find("motion-toggle").textContent = reduced.matches
      ? "Motion reduced"
      : manualPause
        ? "Resume motion"
        : "Pause motion";
    find("motion-toggle").setAttribute(
      "aria-pressed",
      String(manualPause || reduced.matches),
    );
    find("motion-toggle").disabled = reduced.matches;
    if (!motionEnabled()) {
      for (const [id, frame] of counterAnimations) {
        cancelAnimationFrame(frame);
        find(id).textContent = counterValues.get(id).toLocaleString();
      }
      counterAnimations.clear();
      for (const animation of chartAnimations) animation.finish();
      chartAnimations.clear();
    }
  }
  find("motion-toggle").addEventListener("click", () => {
    manualPause = !manualPause;
    updateMotion();
  });
  reduced.addEventListener("change", updateMotion);
  document.addEventListener("visibilitychange", updateMotion);
  document.body.classList.add("motion-ready");
  updateMotion();

  const reveals = document.querySelectorAll(".stats, .panel");
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries)
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
      },
      { threshold: 0.04, rootMargin: "0px 0px -15px 0px" },
    );
    for (const item of reveals) {
      item.classList.add("reveal");
      observer.observe(item);
    }
  }

  let navigationTarget = null,
    targetWasVisible = false;
  function updateNavigation() {
    const sections = [
      "overview",
      "transfers",
      "instruments",
      "classification",
      "activity",
    ];
    let active = "overview";
    for (const id of sections.slice(1))
      if (find(id).getBoundingClientRect().top <= 190) active = id;
    if (navigationTarget) {
      const bounds = find(navigationTarget).getBoundingClientRect();
      const visible = bounds.top < innerHeight && bounds.bottom > 0;
      if (targetWasVisible && !visible) navigationTarget = null;
      else {
        active = navigationTarget;
        targetWasVisible ||= visible;
      }
    }
    document.querySelectorAll("nav a").forEach((link) => {
      const selected = link.getAttribute("href") === "#" + active;
      link.classList.toggle("active", selected);
      if (selected) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  }
  document.querySelectorAll("nav a, .hero-actions a").forEach((link) => {
    link.addEventListener("click", () => {
      navigationTarget = link.getAttribute("href").slice(1);
      targetWasVisible = false;
      updateNavigation();
    });
  });
  const clearNavigationTarget = () => {
    navigationTarget = null;
    updateNavigation();
  };
  addEventListener("wheel", clearNavigationTarget, { passive: true });
  addEventListener("touchstart", clearNavigationTarget, { passive: true });
  addEventListener("keydown", (event) => {
    if (
      ["PageDown", "PageUp", "Home", "End", "ArrowDown", "ArrowUp"].includes(
        event.key,
      ) &&
      !event.target.matches("input,select,textarea")
    )
      clearNavigationTarget();
  });
  let scrollFrame = null;
  addEventListener(
    "scroll",
    () => {
      if (scrollFrame !== null) return;
      scrollFrame = requestAnimationFrame(() => {
        updateNavigation();
        scrollFrame = null;
      });
    },
    { passive: true },
  );
  updateNavigation();
  document.querySelectorAll("nav a").forEach((link) => {
    link.setAttribute("aria-label", link.querySelector("span").textContent);
  });

  function countTo(id, target) {
    const last = counterValues.get(id) ?? 0;
    counterValues.set(id, target);
    if (counterAnimations.has(id))
      cancelAnimationFrame(counterAnimations.get(id));
    if (!motionEnabled() || target <= last) {
      find(id).textContent = target.toLocaleString();
      return;
    }
    const start = performance.now();
    const tick = (time) => {
      const fraction = Math.min((time - start) / 650, 1),
        ease = 1 - Math.pow(1 - fraction, 3);
      find(id).textContent = Math.round(
        last + (target - last) * ease,
      ).toLocaleString();
      if (fraction < 1) counterAnimations.set(id, requestAnimationFrame(tick));
      else counterAnimations.delete(id);
    };
    counterAnimations.set(id, requestAnimationFrame(tick));
  }

  function renderInspection() {
    const slider = find("sample-position"),
      samples = snapshot?.sensor.samples ?? [];
    slider.disabled = !samples.length;
    slider.max = Math.max(samples.length - 1, 0);
    if (!samples.length) {
      find("sample-detail").textContent = "No readings yet";
      return;
    }
    const index =
      inspectIndex === null
        ? samples.length - 1
        : Math.min(inspectIndex, samples.length - 1);
    slider.value = index;
    const sample = samples[index],
      values = find("corrected").checked
        ? (sample.corrected ?? sample.raw)
        : sample.raw;
    const mode =
      find("corrected").checked && sample.corrected ? "corrected" : "raw";
    const label = `Sample ${index + 1}/${samples.length} · ${mode} · X ${values.accel_x_g.toFixed(3)} · Y ${values.accel_y_g.toFixed(3)} · Z ${values.accel_z_g.toFixed(3)} g`;
    find("sample-detail").textContent = label;
    slider.setAttribute("aria-valuetext", label);
    const x = (index * 600) / Math.max(samples.length - 1, 1);
    find("chart-marker").setAttribute("x1", x);
    find("chart-marker").setAttribute("x2", x);
  }
  find("sample-position").addEventListener("input", (event) => {
    inspectIndex = Number(event.target.value);
    renderInspection();
  });
  find("corrected").addEventListener("change", renderInspection);
  find("imu-chart").addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse" || !snapshot?.sensor.samples.length)
      return;
    const bounds = event.currentTarget.getBoundingClientRect();
    inspectIndex = Math.round(
      Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) *
        (snapshot.sensor.samples.length - 1),
    );
    renderInspection();
  });
  find("imu-chart").addEventListener("pointerleave", () => {
    inspectIndex = null;
    renderInspection();
  });

  document.addEventListener("aerolink:update", (event) => {
    snapshot = event.detail;
    const { dashboard, sensor, transfer, demo } = snapshot;
    find("science-source").textContent = demo
      ? "Synthetic demonstration"
      : "Real model output";
    find("stage-manifest").classList.toggle("is-done", !!transfer?.bytes);
    find("stage-chunks").classList.toggle(
      "is-done",
      !!transfer?.total && transfer.received === transfer.total,
    );
    find("stage-checksum").classList.toggle(
      "is-done",
      transfer?.status === "verified",
    );
    document.body.classList.toggle("demo-mode", demo);
    countTo("event-count", dashboard.event_count);
    countTo("verified-count", dashboard.verified_count);
    countTo("sample-count", sensor.samples.length);
    const percent = transfer
      ? transfer.status === "verified"
        ? 100
        : Math.min(
            100,
            Math.round(
              (transfer.received / Math.max(transfer.total ?? 1, 1)) * 100,
            ),
          )
      : 0;
    find("transfer-ring").setAttribute(
      "stroke-dasharray",
      `${percent} ${100 - percent}`,
    );
    find("transfer-percent").textContent = transfer ? `${percent}%` : "—";
    find("transfer-phase").textContent =
      transfer?.status === "verified"
        ? "CHECKSUM VERIFIED"
        : transfer?.status === "failed"
          ? "INTEGRITY FAILED"
          : transfer
            ? "RECEIVING CHUNKS"
            : "AWAITING TRANSFER";
    const ring = document.querySelector(".integrity-orbit");
    ring.classList.toggle("is-verified", transfer?.status === "verified");
    ring.classList.toggle("is-failed", transfer?.status === "failed");
    const incoming = new Set(transfer?.observed_chunks ?? []);
    if (previousTransfer === transfer?.transfer_id && motionEnabled()) {
      document.querySelectorAll(".packet").forEach((cell, index) => {
        if (incoming.has(index + 1) && !previousChunks.has(index + 1))
          cell.classList.add("is-new");
      });
    }
    previousTransfer = transfer?.transfer_id;
    previousChunks = incoming;
    const reports = dashboard.classifications ?? [];
    [
      ["CLEAR", "clear"],
      ["CLOUDY", "cloudy"],
      ["NOT_VISIBLE", "not-visible"],
    ].forEach(([label, id]) => {
      const count = reports.filter(
        (report) => report.classification === label,
      ).length;
      find("science-" + id).textContent = count;
      find(id + "-share").value = reports.length
        ? (count / reports.length) * 100
        : 0;
    });
    const signature = JSON.stringify([
      sensor.samples.at(-1),
      sensor.samples.length,
      find("corrected").checked,
    ]);
    if (signature !== previousChart && motionEnabled()) {
      for (const line of document.querySelectorAll("#chart-lines polyline")) {
        const length = line.getTotalLength();
        const animation = line.animate(
          [
            { strokeDasharray: length, strokeDashoffset: length },
            { strokeDasharray: length, strokeDashoffset: 0 },
          ],
          { duration: 700, easing: "ease-out" },
        );
        chartAnimations.add(animation);
        animation.onfinish = () => chartAnimations.delete(animation);
      }
    }
    previousChart = signature;
    renderInspection();
  });

  const connection = find("connection");
  new MutationObserver(() => {
    document.body.classList.toggle(
      "api-connected",
      connection.textContent.includes("API connected"),
    );
    document.body.classList.toggle(
      "api-offline",
      connection.textContent.includes("API offline"),
    );
  }).observe(connection, { childList: true });
  // A very fast local API can resolve before this deferred visual script loads.
  if (typeof current !== "undefined" && current && sensor) {
    const transfer = current.transfers.find(
      (item) => item.transfer_id === selected,
    );
    document.dispatchEvent(
      new CustomEvent("aerolink:update", {
        detail: { dashboard: current, sensor, transfer, demo },
      }),
    );
  }
})();
