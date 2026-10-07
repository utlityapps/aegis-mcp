// Aegis voice simulator: a real MCP client (Streamable HTTP, JSON-RPC 2.0) standing in for Alexa+.
//
// Everything Aegis says comes from the server's `say` text. The only local logic is the phrase matcher
// that picks a tool, which is the job Alexa+'s language model does in production. Replies the page writes
// itself (help, "say yes or no") are tagged in the chat so they are never mistaken for Aegis.
"use strict";

const MCP_URL = "/mcp";
const MAX_LOG_ENTRIES = 60;

// ---------------------------------------------------------------------------------------------------------
// MCP client
// ---------------------------------------------------------------------------------------------------------

class McpClient {
  constructor(url, onTraffic) {
    this.url = url;
    this.onTraffic = onTraffic;
    this.nextId = 1;
    this.protocolVersion = null;
    this.sessionId = null;
    this.tools = [];
  }

  async connect(requestedVersion) {
    this.protocolVersion = null;
    this.sessionId = null;
    const init = await this.request("initialize", {
      protocolVersion: requestedVersion,
      capabilities: {},
      clientInfo: { name: "Aegis Voice Simulator", version: "1.0.0" },
    });
    this.protocolVersion = init.protocolVersion;
    await this.notify("notifications/initialized");
    const listed = await this.request("tools/list", {});
    this.tools = listed.tools || [];
    return { server: init.serverInfo || {}, protocolVersion: init.protocolVersion, tools: this.tools };
  }

  callTool(name, args) {
    return this.request("tools/call", { name, arguments: args });
  }

  async request(method, params) {
    const message = { jsonrpc: "2.0", id: `sim-${this.nextId++}`, method, params };
    const { body } = await this.post(message);
    if (!body) throw new McpError("The server sent an empty reply.");
    if (body.error) throw new McpError(body.error.message || "JSON-RPC error", body.error);
    return body.result;
  }

  async notify(method, params) {
    const message = { jsonrpc: "2.0", method };
    if (params) message.params = params;
    await this.post(message);
  }

  async post(message) {
    const headers = { "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
    if (this.protocolVersion) headers["MCP-Protocol-Version"] = this.protocolVersion;
    if (this.sessionId) headers["Mcp-Session-Id"] = this.sessionId;

    const started = performance.now();
    let response;
    try {
      response = await fetch(this.url, { method: "POST", headers, body: JSON.stringify(message), cache: "no-store" });
    } catch (err) {
      this.onTraffic({ message, status: 0, body: null, ms: performance.now() - started, failed: true });
      throw new McpError("Couldn't reach the Aegis server.");
    }
    const session = response.headers.get("Mcp-Session-Id");
    if (session) this.sessionId = session;

    const text = await response.text();
    const body = parseBody(text, response.headers.get("Content-Type") || "", message.id);
    const ms = performance.now() - started;
    this.onTraffic({ message, status: response.status, body, raw: body ? null : text, ms, failed: !response.ok });
    if (!response.ok && !(body && body.error)) throw new McpError(`HTTP ${response.status}`);
    return { body };
  }
}

class McpError extends Error {
  constructor(message, rpcError) {
    super(message);
    this.rpcError = rpcError || null;
  }
}

// JSON responses are the norm (the server runs with json_response=True); SSE is accepted for completeness.
function parseBody(text, contentType, id) {
  if (!text) return null;
  if (contentType.includes("text/event-stream")) {
    for (const chunk of text.split(/\r?\n\r?\n/)) {
      const data = chunk.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
      if (!data) continue;
      try {
        const parsed = JSON.parse(data);
        if (parsed.id === id) return parsed;
      } catch (_) { /* skip a malformed event */ }
    }
    return null;
  }
  try {
    return JSON.parse(text);
  } catch (_) {
    return null;
  }
}

// ---------------------------------------------------------------------------------------------------------
// Conversation: the phrase matcher that stands in for Alexa+ choosing a tool
// ---------------------------------------------------------------------------------------------------------

const YES = /\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|please do|confirm|absolutely|of course)\b/;
const NO = /\b(no|nope|cancel|don't|do not|never ?mind|not now|stop)\b/;
const MORE = /\b(more|next|continue|keep going|go on|tell me more)\b/;
const EXPLAIN = /\b(why|explain|warning signs?|red flags?|how do you know|what's wrong|what is wrong)\b/;
const LIST = /\b(what|which|any|how many|list|read)\b.*\b(voicemails?|messages?)\b|\bwho (called|left)\b/;
const CHECK = /\b(check|scam|look at|safe|real|legit|legitimate|voicemail|message|call)\b/;
const LATEST = /\b(last|latest|newest|most recent|just got|new one)\b/;
// "the one from Medicare" must not read as "one" = first, so a bare number counts only on its own.
const ORDINALS = [
  /\b(first|1st|number one)\b|^one$/,
  /\b(second|2nd|number two)\b|^two$/,
  /\b(third|3rd|number three)\b|^three$/,
  /\b(fourth|4th|number four)\b|^four$/,
  /\b(fifth|5th|number five)\b|^five$/,
];
const HINT_FILLER = /\b(please|for me|for scams?|is (it|this) a scam|real|safe)\b/g;

function freshState() {
  return {
    voicemailId: null,   // the voicemail being discussed (never shown or spoken)
    verdict: null,
    pending: null,       // { tool, token } while a block or report waits for yes/no
    candidates: null,    // check_voicemail's "which one?" list
    listItems: null,     // the last list_voicemails page, for "check the second one"
    listCursor: null,
    explainNext: null,
    last: null,          // the last thing Aegis did: list | check | candidates | explain | staged
  };
}

function normalize(text) {
  return text.toLowerCase().replace(/[’]/g, "'").replace(/[^a-z0-9' -]/g, " ").replace(/\s+/g, " ").trim();
}

function pickOrdinal(text, items) {
  if (!items || !items.length) return null;
  if (/\b(last one|the last)\b/.test(text) && !/\bvoicemail\b/.test(text)) return items[items.length - 1];
  for (let i = 0; i < items.length && i < ORDINALS.length; i++) {
    if (ORDINALS[i].test(text)) return items[i];
  }
  return null;
}

function pickByName(text, items) {
  if (!items) return null;
  const words = new Set(text.split(" ").filter((w) => w.length > 2));
  let best = null;
  let bestScore = 0;
  for (const item of items) {
    const label = normalize(item.caller_label || "");
    const score = label.split(" ").filter((w) => w.length > 2 && words.has(w)).length;
    if (score > bestScore) [best, bestScore] = [item, score];
  }
  return best;
}

// The person's words about who called, e.g. "the voicemail I just got from the IRS" -> "the IRS".
function callerHint(text) {
  let hint = null;
  const from = text.match(/\bfrom (.+)$/);
  if (from) hint = from[1];
  else {
    const named = text.match(/\b(?:check|is) (?:the |my |that )?(.+?) (?:voicemail|message|call)\b/);
    if (named) hint = named[1];
  }
  if (!hint) return null;
  hint = hint.replace(HINT_FILLER, " ").replace(/\s+/g, " ").trim();
  if (!hint || LATEST.test(hint) || /^(a|one|it|this|that)$/.test(hint)) return null;
  return hint.slice(0, 80);
}

// Returns { tool, args } to call Aegis, or { local } for a reply the page writes itself.
function route(raw, s) {
  const text = normalize(raw);
  if (!text) return { local: "I didn't catch that. Try asking me to check a voicemail." };
  const yes = YES.test(text) && !NO.test(text);
  const no = NO.test(text) && !yes;

  if (s.pending) {
    if (yes) return { tool: s.pending.tool, args: { approval_token: s.pending.token, decision: "approve" } };
    if (no) return { tool: s.pending.tool, args: { approval_token: s.pending.token, decision: "reject" } };
    if (!/\b(block|report|check|list|why|explain)\b/.test(text)) {
      return { local: "Please answer yes or no, so I know whether to go ahead." };
    }
  }

  if (s.candidates) {
    const chosen = pickByName(text, s.candidates) || pickOrdinal(text, s.candidates);
    if (chosen) return { tool: "check_voicemail", args: { voicemail_id: chosen.voicemail_id } };
  }

  if (/\bblock\b/.test(text)) return needVoicemail(s) || { tool: "block_number", args: { voicemail_id: s.voicemailId } };
  if (/\breport\b/.test(text)) return needVoicemail(s) || { tool: "report_scam", args: { voicemail_id: s.voicemailId } };
  if (EXPLAIN.test(text)) {
    return needVoicemail(s) || { tool: "explain_red_flags", args: { voicemail_id: s.voicemailId } };
  }

  if (MORE.test(text) || yes) {
    if (s.last === "explain" && s.explainNext !== null) {
      return { tool: "explain_red_flags", args: { voicemail_id: s.voicemailId, start: s.explainNext } };
    }
    if (s.last === "list" && s.listCursor) return { tool: "list_voicemails", args: { cursor: s.listCursor } };
    if ((s.last === "check" || s.last === "explain") && s.voicemailId) {
      return yes
        ? { local: "Would you like me to block the number, or report it as a scam?" }
        : { tool: "explain_red_flags", args: { voicemail_id: s.voicemailId } };
    }
  }

  if (LIST.test(text) && !/\bfrom\b/.test(text)) return { tool: "list_voicemails", args: {} };

  if (s.last === "list") {
    const chosen = pickOrdinal(text, s.listItems);
    if (chosen) return { tool: "check_voicemail", args: { voicemail_id: chosen.voicemail_id } };
  }

  if (CHECK.test(text) || /\bfrom\b/.test(text)) {
    const hint = callerHint(text);
    if (hint) return { tool: "check_voicemail", args: { caller_hint: hint } };
    return { tool: "check_voicemail", args: {} };
  }

  if (no || /\b(thanks|thank you|that's all|goodbye|bye)\b/.test(text)) {
    return { local: "Okay. I'm here whenever you want me to check a message." };
  }
  return {
    local: "I can check your voicemails for scams. Try: “What voicemails do I have?” or “Check the voicemail from the bank.”",
  };
}

function needVoicemail(s) {
  return s.voicemailId ? null : { local: "Which voicemail do you mean? Ask me to check one first, for example the one from the bank." };
}

// Update conversation state from a successful tool result.
function remember(s, tool, sc) {
  if (tool === "list_voicemails") {
    Object.assign(s, { listItems: sc.voicemails || [], listCursor: sc.next_cursor || null, last: "list", candidates: null });
  } else if (tool === "check_voicemail") {
    if (sc.status === "checked" && sc.voicemail) {
      Object.assign(s, {
        voicemailId: sc.voicemail.voicemail_id, verdict: sc.verdict, explainNext: null, candidates: null, last: "check",
      });
    } else if (sc.status === "ambiguous") {
      Object.assign(s, { candidates: sc.candidates || [], last: "candidates" });
    }
  } else if (tool === "explain_red_flags") {
    Object.assign(s, { explainNext: sc.next_start ?? null, last: "explain" });
  } else if (tool === "block_number" || tool === "report_scam") {
    s.pending = sc.status === "staged" && sc.approval_token ? { tool, token: sc.approval_token } : null;
    s.last = s.pending ? "staged" : "check";
  }
}

function suggestions(s) {
  if (s.pending) return ["Yes", "No"];
  if (s.candidates) return s.candidates.slice(0, 3).map((c) => `The one from ${c.caller_label}`);
  if (s.last === "explain" && s.explainNext !== null) return ["Tell me more", "Block them", "Report it"];
  if ((s.last === "check" || s.last === "explain") && (s.verdict === "SCAM" || s.verdict === "SUSPICIOUS")) {
    return ["Why does it look like a scam?", "Block them", "Report it"];
  }
  if (s.last === "list") return ["Check the first one", "Check the second one", s.listCursor ? "Tell me more" : "Check the message from Sarah"];
  return [
    "What voicemails do I have?",
    "Check the voicemail I just got from the IRS",
    "Check the message from Sarah",
    "Check the one from the office",
  ];
}

// ---------------------------------------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const els = {};
let client;
let state = freshState();
let connected = false;
let busy = false;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addBubble(who, text, extra) {
  const item = el("li", `bubble ${who}`);
  const label = el("span", "who", who === "user" ? "You" : "Alexa (Aegis)");
  if (who === "local") label.textContent = "Simulator";
  item.append(label);
  if (extra && extra.verdict) {
    const badge = el("span", `verdict v-${extra.verdict.toLowerCase()}`, `${extra.verdict} · risk ${extra.risk}/100`);
    item.append(badge);
  }
  item.append(el("p", "", text));
  if (who === "local") item.append(el("span", "tag", "written by this page, not Aegis"));
  if (extra && extra.receipt) item.append(el("span", "tag", "simulated: true · practice run, nothing really blocked or reported"));
  els.chat.append(item);
  els.chat.scrollTop = els.chat.scrollHeight;
}

function logTraffic({ message, status, body, raw, ms, failed }) {
  for (const open of els.log.querySelectorAll("details[open]")) open.open = false;
  const item = el("li", `entry${failed ? " bad" : ""}`);
  const details = el("details");
  details.open = true;
  const summary = el("summary");
  const label = message.method === "tools/call" ? `tools/call · ${message.params.name}` : message.method;
  summary.append(el("span", "arrow", "→"), el("span", "method", label));
  const isToolError = body && body.result && body.result.isError;
  const statusText = status === 0 ? "no connection" : `${status}${isToolError ? " · isError" : ""}`;
  summary.append(el("span", `meta${failed || isToolError ? " warn" : ""}`, `${statusText} · ${ms.toFixed(0)} ms`));
  details.append(summary);
  details.append(el("div", "dir", "Request"), el("pre", "", JSON.stringify(message, null, 2)));
  const answer = body ? JSON.stringify(body, null, 2) : raw ? raw : "(no body: notification accepted)";
  details.append(el("div", "dir", "Response"), el("pre", "", answer));
  item.append(details);
  els.log.prepend(item);
  while (els.log.children.length > MAX_LOG_ENTRIES) els.log.lastChild.remove();
}

function setConnection(kind, text) {
  els.connDot.className = `dot ${kind}`;
  els.connText.textContent = text;
}

function renderChips() {
  els.chips.replaceChildren(
    ...suggestions(state).map((phrase) => {
      const chip = el("button", "chip", phrase);
      chip.type = "button";
      chip.addEventListener("click", () => handle(phrase));
      return chip;
    }),
  );
}

// --- speech out -------------------------------------------------------------------------------------------

let voice = null;
function pickVoice() {
  if (!("speechSynthesis" in window)) return;
  const voices = speechSynthesis.getVoices().filter((v) => v.lang && v.lang.startsWith("en"));
  const preferred = ["Samantha", "Google US English", "Microsoft Aria", "Microsoft Jenny", "Karen", "Moira"];
  voice = preferred.map((name) => voices.find((v) => v.name.includes(name))).find(Boolean) || voices[0] || null;
}

function speak(text) {
  if (!els.speak.checked || !("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  if (voice) utterance.voice = voice;
  utterance.rate = 0.95;
  utterance.onstart = () => els.ring.classList.add("speaking");
  utterance.onend = utterance.onerror = () => els.ring.classList.remove("speaking");
  speechSynthesis.speak(utterance);
}

// --- speech in --------------------------------------------------------------------------------------------

function setupMic() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    els.mic.disabled = true;
    els.mic.title = "Speech input isn't available in this browser. Type instead, or use Chrome.";
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  let listening = false;

  recognition.onresult = (event) => {
    const result = event.results[event.results.length - 1];
    els.input.value = result[0].transcript;
    if (result.isFinal) {
      recognition.stop();
      handle(els.input.value);
    }
  };
  recognition.onend = () => {
    listening = false;
    els.mic.classList.remove("on");
    els.ring.classList.remove("listening");
  };
  recognition.onerror = (event) => {
    if (event.error === "not-allowed") {
      addBubble("local", "The microphone is blocked for this page. You can type instead.");
    }
  };
  els.mic.addEventListener("click", () => {
    if (listening) return recognition.stop();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    listening = true;
    els.input.value = "";
    els.mic.classList.add("on");
    els.ring.classList.add("listening");
    recognition.start();
  });
}

// --- turns ------------------------------------------------------------------------------------------------

async function handle(raw) {
  const text = raw.trim();
  if (!text || busy) return;
  els.input.value = "";
  addBubble("user", text);

  if (!connected) {
    addBubble("local", "I can't reach the Aegis server yet. Make sure it's running, then press Reconnect.");
    return;
  }
  const decision = route(text, state);
  if (decision.local) {
    addBubble("local", decision.local);
    speak(decision.local);
    renderChips();
    return;
  }

  // Moving on to anything else drops a waiting block or report, so a later "yes" can't approve it by surprise.
  if (!decision.args.approval_token) state.pending = null;
  busy = true;
  els.ring.classList.add("thinking");
  try {
    const result = await client.callTool(decision.tool, decision.args);
    const sc = result.structuredContent || {};
    if (result.isError) {
      const say = (sc.error && sc.error.say) || "Something went wrong. Please try again.";
      // A refused or expired approval can't be retried with the same token.
      if (decision.args.approval_token) state.pending = null;
      addBubble("aegis", say);
      speak(say);
    } else {
      remember(state, decision.tool, sc);
      const extra = {};
      if (decision.tool === "check_voicemail" && sc.status === "checked") Object.assign(extra, { verdict: sc.verdict, risk: sc.risk_score });
      if (sc.receipt && sc.receipt.simulated) extra.receipt = true;
      addBubble("aegis", sc.say, extra);
      speak(sc.say);
    }
  } catch (err) {
    const say = "Sorry, I couldn't reach Aegis just now. Please try again in a moment.";
    addBubble("local", say);
    speak(say);
    if (!(err instanceof McpError) || !err.rpcError) markDisconnected();
  } finally {
    busy = false;
    els.ring.classList.remove("thinking");
    renderChips();
  }
}

function markDisconnected() {
  connected = false;
  setConnection("bad", "Disconnected · press Reconnect");
}

async function connect() {
  connected = false;
  setConnection("wait", "Connecting…");
  client = new McpClient(MCP_URL, logTraffic);
  try {
    const info = await client.connect(els.proto.value);
    connected = true;
    const name = info.server.title || info.server.name || "server";
    setConnection("ok", `Connected to ${name} ${info.server.version || ""} · MCP ${info.protocolVersion} · ${info.tools.length} tools`);
  } catch (err) {
    setConnection("bad", "Can't reach the Aegis server · start it, then press Reconnect");
  }
}

function resetConversation() {
  state = freshState();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  els.chat.replaceChildren();
  addBubble("local", "New conversation. Ask me about your voicemails.");
  renderChips();
}

function start() {
  Object.assign(els, {
    chat: $("chat"), log: $("log"), chips: $("chips"), input: $("utterance"), mic: $("mic"), ring: $("ring"),
    speak: $("speak"), proto: $("proto"), connDot: $("conn-dot"), connText: $("conn-text"),
  });
  $("ask").addEventListener("submit", (event) => {
    event.preventDefault();
    handle(els.input.value);
  });
  $("reconnect").addEventListener("click", connect);
  els.proto.addEventListener("change", connect);
  $("reset").addEventListener("click", resetConversation);
  if ("speechSynthesis" in window) {
    pickVoice();
    speechSynthesis.onvoiceschanged = pickVoice;
  } else {
    els.speak.disabled = true;
    els.speak.checked = false;
  }
  setupMic();
  resetConversation();
  connect();
}

if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", start);
if (typeof module !== "undefined") module.exports = { McpClient, route, remember, suggestions, freshState, callerHint, parseBody };
