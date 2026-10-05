// Aegis simulated Fire TV display: reads cards from the live /events/firetv stream and renders them.
// The display token comes from the URL fragment (#token=...), which browsers never send to a server,
// and is passed only in the Authorization header. All card text is set with textContent, never HTML.
"use strict";

const BADGES = { info: "HEADS UP", caution: "CAUTION", alert: "SCAM WARNING" };
const MAX_BACKOFF_MS = 10000;

function tokenFromFragment(hash) {
  const params = new URLSearchParams((hash || "").replace(/^#/, ""));
  return params.get("token") || "";
}

// Splits a growing text buffer into complete SSE events; returns [events, leftover].
function parseSse(buffer) {
  const events = [];
  const blocks = buffer.split("\n\n");
  const leftover = blocks.pop();
  for (const block of blocks) {
    let event = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data.push(line.slice(6));
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return [events, leftover];
}

function isCard(value) {
  return Boolean(value) && value.schema === "aegis.firetv.card/v1" && typeof value.title === "string";
}

function renderCard(doc, card) {
  const node = doc.getElementById("card");
  node.dataset.severity = ["info", "caution", "alert"].includes(card.severity) ? card.severity : "info";
  doc.getElementById("card-badge").textContent = BADGES[node.dataset.severity];
  doc.getElementById("card-title").textContent = card.title;
  doc.getElementById("card-body").textContent = card.body;

  const evidence = doc.getElementById("card-evidence");
  evidence.replaceChildren(...(card.evidence || []).slice(0, 3).map((line) => {
    const item = doc.createElement("li");
    item.textContent = line;
    return item;
  }));

  doc.getElementById("card-threat").textContent = card.threat
    ? `Verdict: ${card.threat.verdict} · risk ${card.threat.risk_score}/100 · decided by fixed rules`
    : "";

  doc.getElementById("card-buttons").replaceChildren(...(card.buttons || []).map((button) => {
    const chip = doc.createElement("span");
    chip.textContent = button.label;
    return chip;
  }));

  node.hidden = false;
  node.style.animation = "none";
  void node.offsetWidth; // restart the slide-in for each new card
  node.style.animation = "";
}

function setStatus(doc, state, text) {
  const status = doc.getElementById("status");
  status.dataset.state = state;
  status.textContent = text;
}

async function listen(doc, token, fetchImpl, attempt = 0) {
  try {
    const response = await fetchImpl("/events/firetv", {
      headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
      cache: "no-store",
    });
    if (response.status === 401) {
      setStatus(doc, "error", "Display token rejected");
      return;
    }
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    setStatus(doc, "connected", "Live");
    attempt = 0;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let events;
      [events, buffer] = parseSse(buffer);
      for (const { event, data } of events) {
        if (event !== "card") continue;
        try {
          const card = JSON.parse(data);
          if (isCard(card)) renderCard(doc, card);
        } catch (error) {
          console.warn("ignored a malformed card", error);
        }
      }
    }
    throw new Error("stream closed");
  } catch (error) {
    const delay = Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempt);
    setStatus(doc, "reconnecting", `Reconnecting in ${Math.round(delay / 1000)}s…`);
    setTimeout(() => listen(doc, token, fetchImpl, attempt + 1), delay);
  }
}

function start(doc, win) {
  const token = tokenFromFragment(win.location.hash);
  if (!token) {
    doc.getElementById("help").hidden = false;
    setStatus(doc, "error", "No display token");
    return;
  }
  listen(doc, token, win.fetch.bind(win));
}

if (typeof document !== "undefined" && typeof window !== "undefined") {
  start(document, window);
}
if (typeof module !== "undefined") {
  module.exports = { tokenFromFragment, parseSse, isCard, renderCard };
}
