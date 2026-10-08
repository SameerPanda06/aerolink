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
          radio: {
            rssi_dbm: -42,
            snr_db: 9.5,
            packet_bytes: 95,
            payload_bytes: 87,
            samples: 159,
            rssi_mean: -43,
            snr_mean: 9.2,
            rssi_min: -47,
            rssi_max: -40,
            observed_frame_bytes: 33700,
          },
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
      node(
        "td",
        t.radio
          ? `${t.radio.rssi_dbm} dBm / ${t.radio.snr_db} dB`
          : "Not recorded",
      ),
      node("td", t.radio ? `${t.radio.packet_bytes} B` : "—"),
    );
    const state = node("td");
    state.append(node("span", t.status.toUpperCase(), `badge ${t.status}`));
    row.append(state);
    const imageCell = node("td");
    if (imageUrl(t)) {
      const view = node("button", "View ↗");
      view.onclick = () => {
        selected = t.transfer_id;
        render();
        openImage();
      };
      imageCell.append(view);
    } else
      imageCell.append(
        node("span", demo ? "Demo" : "Awaiting export", "image-pending"),
      );
    row.append(imageCell);
    $("transfer-rows").append(row);
  }
  $("transfer-empty").hidden = d.transfers.length > 0;
  renderTransferDetails(transfer);
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
        `${report.transfer_id ?? "Unlinked"} · ${report.source === "demo" ? "Demo" : report.source === "pi_http" ? "Pi HTTP" : "LoRa"}${report.status ? ` · ${report.status}` : ''}`,
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
function imageUrl(transfer) {
  return !demo &&
    /^[A-F0-9]{8}$/.test(transfer?.transfer_id ?? "") &&
    transfer?.image
    ? `/api/transfers/${transfer.transfer_id}/image`
    : null;
}
function renderTransferDetails(transfer) {
  const meta = transfer?.metadata, timing = transfer?.timing;
  const urlThumb = imageUrl(transfer);
  const thumbnail = $('selected-thumbnail');
  thumbnail.hidden = !urlThumb;
  $('thumbnail-placeholder').hidden = !!urlThumb;
  if (urlThumb && thumbnail.getAttribute('src') !== urlThumb) thumbnail.src = urlThumb;
  if (!urlThumb) thumbnail.removeAttribute('src');
  text('thumbnail-placeholder', transfer?.status === 'failed' ? 'Image verification failed' : transfer?.status === 'verified' ? 'Verified · preparing preview' : transfer ? 'Receiving image' : 'Awaiting image');
  text('selected-image-name', meta ? `${meta.mission_id} / ${meta.image_id}` : transfer?.transfer_id ?? 'Waiting for metadata');
  text('selected-prediction', meta ? `${meta.classification} · ${(meta.confidence * 100).toFixed(1)}% confidence · ${meta.action}` : 'No classification metadata received for this transfer.');
  const age = timing?.last_activity_at ? (Date.now() - Date.parse(timing.last_activity_at)) / 1000 : Infinity;
  const active = !demo && transfer?.status === 'receiving' && age >= 0 && age < 8;
  $('transfer-activity').classList.toggle('receiving-pulse', active);
  text('transfer-activity', urlThumb ? 'Image available · independently verified' : transfer?.status === 'verified' ? 'Receiver verified · awaiting export' : transfer?.status === 'failed' ? 'Checksum failed' : active ? 'Receiving image chunks…' : transfer ? 'Waiting for further packets' : 'Waiting for transfer');
  $('image-progress').value = transfer?.total ? Math.min(100, 100 * transfer.received / transfer.total) : 0;
  $('stage-export').classList.toggle('is-done', !!urlThumb);
  const facts = $('selected-facts');
  facts.replaceChildren();
  const elapsed = timing?.elapsed_seconds;
  const rate = timing?.receiving_bytes_per_second;
  const entries = [
    ['Progress', transfer ? `${transfer.received} / ${transfer.total ?? '?'} chunks` : '—'],
    ['Observed elapsed time', elapsed != null ? `${elapsed.toFixed(1)} s` : '—'],
    ['Measured image receiving rate', rate != null ? `${rate.toFixed(1)} B/s` : '—'],
    ['Captured at', meta?.captured_at ? new Date(meta.captured_at).toLocaleString() : 'Unknown'],
    ['Image source', meta?.capture_source ?? 'Unknown'],
    ['Geographic bounds (W, S, E, N)', meta?.bbox ? meta.bbox.map(v => v.toFixed(4)).join(', ') : 'Unknown'],
    ['Compression', meta ? `JPEG quality ${meta.jpeg_quality} · ${meta.original_bytes.toLocaleString()} → ${meta.compressed_bytes.toLocaleString()} B` : 'Unknown'],
    ['Dimensions (original → transmitted)', meta ? `${meta.original_dimensions.join(' × ')} → ${meta.transmitted_dimensions.join(' × ')}` : 'Unknown'],
    ['Altitude', meta?.altitude_m_agl != null ? `${meta.altitude_m_agl} m (${meta.altitude_reference})` : 'Unknown'],
    ['Scene cloud cover', meta?.cloud_cover_percent != null ? `${meta.cloud_cover_percent.toFixed(2)}% (catalog)` : 'Unknown'],
  ];
  for (const [label, value] of entries) {
    const field = node('div'); field.append(node('dt', label), node('dd', value)); facts.append(field);
  }
  $('metadata-download').hidden = !meta || demo;
  if (meta && !demo) $('metadata-download').href = `/api/transfers/${transfer.transfer_id}/metadata`;
  else $('metadata-download').removeAttribute('href');
  const radio = transfer?.radio;
  text(
    "signal-label",
    radio
      ? radio.rssi_dbm >= -80
        ? "Strong received power"
        : radio.rssi_dbm >= -100
          ? "Moderate received power"
          : "Low received power"
      : "No recorded radio measurements",
  );
  text("signal-value", radio ? `${radio.rssi_dbm} dBm` : "—");
  $("signal-meter").value = radio?.rssi_dbm ?? -140;
  $("signal-meter").hidden = !radio;
  text("radio-snr", radio ? `${radio.snr_db} dB` : "—");
  text("radio-frame", radio ? `${radio.packet_bytes} bytes` : "—");
  text("radio-payload", radio ? `${radio.payload_bytes} bytes` : "—");
  text(
    "radio-average",
    radio ? `${radio.rssi_mean} dBm / ${radio.snr_mean} dB` : "—",
  );
  text(
    "radio-range",
    radio ? `${radio.rssi_min} to ${radio.rssi_max} dBm` : "—",
  );
  text(
    "radio-observed",
    radio
      ? `${radio.samples} / ${radio.observed_frame_bytes.toLocaleString()} B`
      : "—",
  );
  const url = imageUrl(transfer);
  const image = $("received-image");
  image.hidden = !url;
  $("image-placeholder").hidden = !!url;
  text(
    "image-placeholder",
    demo
      ? "Demo illustrates signal details. It does not contain a received photograph."
      : transfer?.status === "verified"
        ? "Verified on the ESP32. Waiting for USB export and server checksum verification."
        : "The receiver must complete and verify this image before export.",
  );
  text("image-state", url ? "Server SHA-256 verified" : "Waiting for export");
  if (url && image.getAttribute("src") !== url) image.src = url;
  if (!url) image.removeAttribute("src");
  $("image-open").disabled = !url;
  $("image-download").hidden = !url;
  if (url) $("image-download").href = `${url}?download=true`;
  else $("image-download").removeAttribute("href");
  text(
    "image-hash",
    transfer?.image
      ? `SHA-256 ${transfer.image.sha256}`
      : "Full SHA-256 will appear after server verification.",
  );
}
function openImage() {
  const transfer = current?.transfers.find(
    (item) => item.transfer_id === selected,
  );
  const url = imageUrl(transfer);
  if (!url) return;
  $("image-full").src = url;
  text("image-dialog-title", `Received image / ${transfer.transfer_id}`);
  $("image-dialog").showModal();
}
$("image-open").onclick = openImage;
$("image-close").onclick = () => $("image-dialog").close();
$("received-image").onerror = () => {
  $("received-image").hidden = true;
  $("image-placeholder").hidden = false;
  text(
    "image-placeholder",
    "Image could not be loaded. Check the server connection and select the transfer again.",
  );
};
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
  const stamp = samples.at(-1)?.recorded_at;
  const sampleAge = stamp ? (Date.now() - Date.parse(stamp)) / 1000 : Infinity;
  const fresh = sampleAge >= 0 && sampleAge < 10;
  text('telemetry-freshness', demo ? 'Demo telemetry' : !samples.length ? 'No samples · start Pi telemetry publisher' : fresh ? `Live · ${sampleAge.toFixed(1)} s old` : 'Stale · showing last recorded sample');
  if (last) {
    const {accel_x_g: x, accel_y_g: y, accel_z_g: z} = last;
    const gravity = Math.hypot(x, y, z);
    if (gravity > 0.8 && gravity < 1.2) {
      const roll = Math.atan2(y, z) * 180 / Math.PI;
      const pitch = Math.atan2(-x, Math.hypot(y, z)) * 180 / Math.PI;
      $('attitude-board').setAttribute('transform', `translate(0 ${pitch / 4}) rotate(${roll} 120 60)`);
      text('tilt-reading', `Roll ${roll.toFixed(1)}° · Pitch ${pitch.toFixed(1)}°`);
    } else {
      text('tilt-reading', 'Tilt unavailable · acceleration outside gravity range');
      $('attitude-board').removeAttribute('transform');
    }
  } else { text('tilt-reading', 'Waiting for MPU6050'); $('attitude-board').removeAttribute('transform'); }
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

function updateDoppler() {
  const frequency = Number($('carrier-mhz').value), velocity = Number($('radial-velocity').value);
  if (!$('carrier-mhz').value || !$('radial-velocity').value || !Number.isFinite(frequency) || frequency < 1 || frequency > 100000 || !Number.isFinite(velocity) || Math.abs(velocity) > 15000) {
    text('doppler-result', 'Enter a carrier from 1–100000 MHz and velocity from −15000 to 15000 m/s.'); return;
  }
  const shift = -frequency * 1e6 * velocity / 299792458;
  text('doppler-result', `Calculated shift: ${shift.toFixed(1)} Hz · Expected receive frequency: ${(frequency + shift / 1e6).toFixed(6)} MHz · Transmit precompensation: ${(-shift).toFixed(1)} Hz (first order)`);
}
$('carrier-mhz').oninput = updateDoppler;
$('radial-velocity').oninput = updateDoppler;
updateDoppler();
