"use strict";
const $ = (id) => document.getElementById(id);
let demo = false,
  selected = null,
  current = null,
  sensor = null,
  pending = false,
  calibrationMessageUntil = 0;
const text = (id, value) => {
  $(id).textContent = value;
};
function node(tag, content, cls) {
  const el = document.createElement(tag);
  if (content !== undefined) el.textContent = content;
  if (cls) el.className = cls;
  return el;
}
function demoData() {
  const stamp = new Date().toISOString();
  return {
    dashboard: {
      event_count: 162,
      verified_count: 1,
      last_event_at: stamp,
      transfers: [
        {
          transfer_id: "DE001001",
          bytes: 31078,
          total: 156,
          received: 156,
          status: "verified",
          observed_chunks: Array.from({ length: 156 }, (_, i) => i + 1),
        },
      ],
      recent_events: [
        {
          type: "image_complete",
          transfer_id: "DE001001",
          sha256_ok: true,
          ingested_at: stamp,
        },
        {
          type: "chunk",
          transfer_id: "DE001001",
          chunk: 156,
          total: 156,
          ingested_at: stamp,
        },
        { type: "receiver_ready", ingested_at: stamp },
      ],
      classifications: [
        {
          image_id: "IMG-DEMO-01",
          classification: "CLEAR",
          confidence: 0.972,
          recommended_action: "keep",
          transfer_id: "DE001001",
          source: "demo",
        },
        {
          image_id: "IMG-DEMO-02",
          classification: "CLOUDY",
          confidence: 0.813,
          recommended_action: "defer",
          source: "demo",
        },
        {
          image_id: "IMG-DEMO-03",
          classification: "NOT_VISIBLE",
          confidence: 0.941,
          recommended_action: "discard",
          source: "demo",
        },
      ],
    },
    telemetry: {
      calibration: null,
      samples: Array.from({ length: 70 }, (_, i) => ({
        raw: {
          accel_x_g: 0.02 + Math.sin(i / 8) * 0.32,
          accel_y_g: -0.01 + Math.cos(i / 7) * 0.16,
          accel_z_g: 1.01 + Math.sin(i / 10) * 0.12,
          gyro_x_dps: 0.2,
          gyro_y_dps: -0.1,
          gyro_z_dps: 0.05,
        },
        corrected: null,
        source: "demo",
      })),
    },
  };
}
function render() {
  const d = current;
  text("event-count", d.event_count.toLocaleString());
  text("verified-count", d.verified_count);
  if (!d.transfers.some((t) => t.transfer_id === selected))
    selected = d.transfers[0]?.transfer_id;
  const transfer = d.transfers.find((t) => t.transfer_id === selected);
  text(
    "progress-count",
    transfer ? `${transfer.received} / ${transfer.total ?? "?"}` : "—",
  );
  text("sample-count", sensor.samples.length);
  text(
    "freshness",
    d.last_event_at
      ? `Last event ${new Date(d.last_event_at).toLocaleTimeString()}`
      : "Waiting for first contact",
  );
  $("transfer-rows").replaceChildren();
  for (const t of d.transfers) {
    const row = node("tr"),
      cell = node("td"),
      button = node("button", t.transfer_id);
    button.onclick = () => {
      selected = t.transfer_id;
      render();
    };
    row.classList.toggle("is-selected", t.transfer_id === selected);
    button.setAttribute("aria-pressed", String(t.transfer_id === selected));
    cell.append(button);
    row.append(cell);
    row.append(
      node("td", t.bytes ? `${(t.bytes / 1024).toFixed(1)} KB` : "—"),
      node("td", `${t.received} / ${t.total ?? "?"}`),
    );
    const state = node("td");
    state.append(node("span", t.status.toUpperCase(), `badge ${t.status}`));
    row.append(state);
    $("transfer-rows").append(row);
  }
  $("transfer-empty").hidden = d.transfers.length > 0;
  text("packet-title", `PACKET MAP / ${transfer?.transfer_id ?? "WAITING"}`);
  $("packet-map").replaceChildren();
  const observed = new Set(transfer?.observed_chunks ?? []),
    count = Math.min(transfer?.total ?? 0, 512);
  for (let i = 1; i <= count; i++) {
    const cell = node(
      "span",
      undefined,
      `packet${observed.has(i) ? " received" : ""}`,
    );
    cell.title = `Chunk ${i}: ${observed.has(i) ? "observed" : "unobserved"}`;
    $("packet-map").append(cell);
  }
  text(
    "packet-note",
    transfer
      ? `Showing ${count} of ${transfer.total ?? 0} chunks. Unobserved events can reflect a late bridge connection.`
      : "Select a transfer to inspect its events.",
  );
  $("events").replaceChildren();
  for (const event of d.recent_events) {
    const row = node("div", undefined, "event-row");
    row.append(
      node("time", new Date(event.ingested_at).toLocaleTimeString()),
      node("span", event.type.replaceAll("_", " ")),
      node(
        "span",
        event.transfer_id
          ? `${event.transfer_id}${event.chunk ? ` · chunk ${event.chunk}/${event.total}` : ""}${event.sha256_ok === true ? " · SHA-256 verified" : ""}`
          : "Ground station event",
      ),
    );
    $("events").append(row);
  }
  if (!d.recent_events.length)
    $("events").append(
      node("p", "Events will appear here as the host forwards them.", "empty"),
    );
  $("classification-rows").replaceChildren();
  const reports = d.classifications ?? [];
  $("classification-empty").hidden = reports.length > 0;
  for (const report of reports) {
    const row = node("tr");
    row.append(
      node("td", report.image_id),
      node("td", report.classification),
      node("td", `${(report.confidence * 100).toFixed(1)}%`),
      node("td", report.recommended_action ?? "—"),
      node(
        "td",
        `${report.transfer_id ?? "Unlinked"} · ${report.source === "demo" ? "Demo" : report.source === "pi_http" ? "Pi HTTP" : "LoRa"}`,
      ),
    );
    $("classification-rows").append(row);
  }
  renderSensor();
  document.dispatchEvent(
    new CustomEvent("aerolink:update", {
      detail: { dashboard: d, sensor, transfer, demo },
    }),
  );
}
function renderSensor() {
  const samples = sensor.samples,
    useCorrected = $("corrected").checked;
  const window = sensor.calibration_window;
  text(
    "capture-status",
    demo
      ? "Demo samples cannot be calibrated."
      : `${window?.fresh_samples ?? 0} fresh samples in latest capture / 50 required. Stationarity is checked when you calibrate.`,
  );
  $("reset-calibration").disabled = demo || !sensor.calibration;
  $("bias-values").replaceChildren();
  if (sensor.calibration) {
    for (const [axis, offset] of Object.entries(sensor.calibration.offsets)) {
      const item = node(
        "div",
        axis.replace("accel_", "A ").replace("gyro_", "G "),
      );
      item.append(node("strong", offset.toFixed(4)));
      $("bias-values").append(item);
    }
  }
  $("imu-empty").hidden = samples.length > 0;
  text(
    "imu-source",
    samples.length
      ? demo
        ? "DEMO / SYNTHETIC"
        : samples.at(-1).source === "pi_http"
          ? "Pi → HTTP · not LoRa"
          : "LoRa telemetry"
      : "No samples",
  );
  if (!sensor.calibration && useCorrected) {
    $("corrected").checked = false;
  }
  const values = samples
      .map((s) => ($("corrected").checked ? s.corrected : s.raw))
      .filter(Boolean),
    last = values.at(-1);
  $("chart-lines").replaceChildren();
  ["accel_x_g", "accel_y_g", "accel_z_g"].forEach((key, index) => {
    if (!values.length) return;
    const line = document.createElementNS(
      "http://www.w3.org/2000/svg",
      "polyline",
    );
    line.setAttribute(
      "points",
      values
        .map(
          (v, i) =>
            `${(i * 600) / Math.max(values.length - 1, 1)},${125 - (Math.max(-1.5, Math.min(1.5, v[key])) + 1.5) * 35}`,
        )
        .join(" "),
    );
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", ["#bd5434", "#537866", "#697794"][index]);
    line.setAttribute("stroke-width", "2");
    $("chart-lines").append(line);
  });
  $("axis-values").replaceChildren();
  [
    "accel_x_g",
    "accel_y_g",
    "accel_z_g",
    "gyro_x_dps",
    "gyro_y_dps",
    "gyro_z_dps",
  ].forEach((key) => {
    const item = node(
      "div",
      key
        .replace("accel_", "A · ")
        .replace("gyro_", "G · ")
        .replace("_g", "")
        .replace("_dps", "")
        .toUpperCase(),
    );
    item.append(
      node(
        "strong",
        last
          ? `${last[key].toFixed(3)} ${key.startsWith("accel") ? "g" : "°/s"}`
          : "—",
      ),
    );
    $("axis-values").append(item);
  });
  if (!$("calibrate").disabled && Date.now() > calibrationMessageUntil)
    text(
      "calibration-status",
      sensor.calibration
        ? `Saved ${new Date(sensor.calibration.created_at).toLocaleString()} · ${sensor.calibration.samples} samples · ${sensor.calibration.gravity_axis}`
        : "No calibration saved.",
    );
}
async function refresh() {
  if (pending) return;
  pending = true;
  try {
    if (demo) {
      const data = demoData();
      current = data.dashboard;
      sensor = data.telemetry;
      text("connection", "Demo · synthetic data");
    } else {
      const responses = await Promise.all([
        fetch("/api/dashboard"),
        fetch(`/api/telemetry?device=${encodeURIComponent($("device").value)}`),
      ]);
      if (responses.some((r) => !r.ok)) throw new Error("API request failed");
      [current, sensor] = await Promise.all(responses.map((r) => r.json()));
      text("connection", "API connected · refresh 2s");
    }
    if (!demo) $("notice").hidden = true;
    render();
  } catch (error) {
    text("connection", "API offline · retrying");
    $("notice").hidden = false;
    text(
      "notice",
      "The backend is unavailable. Displayed values may be stale; reconnecting automatically.",
    );
  } finally {
    pending = false;
  }
}
$("demo").onclick = () => {
  demo = !demo;
  selected = null;
  text("demo", demo ? "Return to live" : "Explore demo");
  text("source", demo ? "Synthetic demonstration" : "Hardware events");
  $("notice").hidden = !demo;
  text(
    "notice",
    "DEMO MODE — synthetic browser-only data. No records are written to the backend. Calibration is disabled.",
  );
  $("calibrate").disabled = demo;
  refresh();
};
$("corrected").onchange = renderSensor;
$("device").onchange = refresh;
$("calibrate").onclick = async () => {
  $("calibrate").disabled = true;
  text("calibration-status", "Checking sample stability…");
  try {
    const headers = { "Content-Type": "application/json" };
    if ($("api-token").value)
      headers.Authorization = "Bearer " + $("api-token").value;
    const response = await fetch("/api/calibrations", {
      method: "POST",
      headers,
      body: JSON.stringify({
        device: $("device").value,
        gravity_axis: $("gravity").value,
        samples: 50,
      }),
    });
    const result = await response.json();
    if (!response.ok)
      throw new Error(
        typeof result.detail === "string"
          ? result.detail
          : "Calibration request rejected",
      );
    text(
      "calibration-status",
      "Bias profile saved. Enable Corrected to compare.",
    );
  } catch (error) {
    text("calibration-status", error.message);
  } finally {
    calibrationMessageUntil = Date.now() + 20000;
    $("calibrate").disabled = false;
  }
};
$("api-docs").href = "/openapi.json";
$("reset-calibration").onclick = async () => {
  if (demo) return;
  const device = $("device").value;
  const headers = {};
  if ($("api-token").value)
    headers.Authorization = "Bearer " + $("api-token").value;
  try {
    const response = await fetch(
      "/api/calibrations/" + encodeURIComponent(device),
      { method: "DELETE", headers },
    );
    if (!response.ok) throw new Error("Reset failed; check the server token.");
    $("corrected").checked = false;
    text("calibration-status", "Calibration reset; raw samples are preserved.");
    calibrationMessageUntil = Date.now() + 20000;
    await refresh();
  } catch (error) {
    text("calibration-status", error.message);
    calibrationMessageUntil = Date.now() + 20000;
  }
};
refresh();
setInterval(refresh, 2000);
