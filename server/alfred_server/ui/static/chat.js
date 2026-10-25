// Alfred web chat: talks to POST /chat/stream and the confirmations API with the API token.
// Plain JS, no build step. Model output is only ever set as textContent, never as HTML.
"use strict";

const TOKEN_KEY = "alfred.apiToken";
const SESSION_KEY = "alfred.chatSession";
const memory = {}; // fallback when localStorage is unavailable

function load(key) {
  try {
    const value = window.localStorage.getItem(key);
    if (value !== null) return value;
  } catch (_) {
    // private window or storage blocked: fall back to memory
  }
  return memory[key] ?? null;
}

function save(key, value) {
  memory[key] = value;
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch (_) {
    // kept in memory only
  }
}

function newSessionId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

function sessionId() {
  let id = load(SESSION_KEY);
  if (!id) {
    id = newSessionId();
    save(SESSION_KEY, id);
  }
  return id;
}

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

class AuthError extends Error {}

async function api(path, options = {}) {
  const token = load(TOKEN_KEY);
  if (!token) throw new AuthError("No API token");
  const headers = { Authorization: "Bearer " + token, ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  if (response.status === 401) {
    save(TOKEN_KEY, null);
    showTokenForm("That token was rejected. Enter it again.");
    throw new AuthError("Invalid token");
  }
  return response;
}

async function errorText(response) {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch (_) {
    // not JSON
  }
  return "Request failed (" + response.status + ")";
}

// --- Token -------------------------------------------------------------------------------

function showTokenForm(message) {
  $("token-form").hidden = false;
  const error = $("token-error");
  error.hidden = !message;
  error.textContent = message || "";
  $("token-input").focus();
}

$("token").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("token-input").value.trim();
  if (!value) return;
  save(TOKEN_KEY, value);
  $("token-input").value = "";
  $("token-form").hidden = true;
  refreshConfirmations();
  $("message").focus();
});

$("forget-token").addEventListener("click", () => {
  save(TOKEN_KEY, null);
  showTokenForm();
});

// --- Conversation ------------------------------------------------------------------------

function showSession() {
  $("session").textContent = "session " + sessionId().slice(0, 8);
}

$("new-session").addEventListener("click", () => {
  save(SESSION_KEY, newSessionId());
  $("log").replaceChildren();
  showSession();
});

function addMessage(who, text) {
  const item = el("article", "message " + who);
  const body = el("p", "text", text);
  item.append(body);
  $("log").append(item);
  item.scrollIntoView({ block: "end" });
  return { item, body };
}

function formatCost(gbp) {
  if (gbp === null || gbp === undefined) return null;
  if (gbp < 0.01) return "£" + gbp.toFixed(4);
  return "£" + gbp.toFixed(2);
}

function describeTurn(turn) {
  const parts = [
    turn.specialist,
    turn.alias + " (" + turn.tier + ")",
    turn.reason,
  ];
  if (turn.budget_code && turn.budget_code !== "ok") parts.push("budget: " + turn.budget_code);
  parts.push(turn.tools && turn.tools.length ? "tools: " + turn.tools.join(", ") : "no tools");
  const cost = formatCost(turn.cost_gbp);
  if (cost) parts.push(cost);
  return parts.join(" · ");
}

// Yields [event, data] for each Server-Sent Event in the response body.
async function* sseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      try {
        yield [event, JSON.parse(data)];
      } catch (_) {
        // ignore malformed events
      }
    }
  }
}

async function send(text) {
  addMessage("user", text);
  const reply = addMessage("alfred", "…");
  reply.item.classList.add("pending");
  let received = "";
  try {
    const response = await api("/chat/stream", {
      method: "POST",
      body: JSON.stringify({ text, session_id: sessionId(), device_type: "text" }),
    });
    if (!response.ok) {
      throw new Error(await errorText(response));
    }
    for await (const [event, data] of sseEvents(response)) {
      if (event === "token") {
        received += data.text;
        reply.body.textContent = received;
      } else if (event === "done") {
        reply.body.textContent = data.reply;
        if (data.turn) reply.item.append(el("p", "meta", describeTurn(data.turn)));
      } else if (event === "error") {
        throw new Error(data.message || "The agent run failed");
      }
    }
  } catch (error) {
    reply.item.classList.add("failed");
    reply.body.textContent = received || "";
    reply.item.append(el("p", "error", error instanceof AuthError ? "Not sent: no valid API token." : error.message));
  } finally {
    reply.item.classList.remove("pending");
    reply.item.scrollIntoView({ block: "end" });
  }
  refreshConfirmations();
}

// One turn at a time: a second stream on the same session would interleave Agno runs. Enter
// calls requestSubmit(), which fires even while the Send button is disabled.
let sending = false;

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (sending) return;
  const input = $("message");
  const text = input.value.trim();
  if (!text) return;
  if (!load(TOKEN_KEY)) {
    showTokenForm("Enter the API token first.");
    return;
  }
  input.value = "";
  sending = true;
  $("send").disabled = true;
  try {
    await send(text);
  } finally {
    sending = false;
    $("send").disabled = false;
    input.focus();
  }
});

$("message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});

// --- Confirmations (gated tools, STU-57) --------------------------------------------------

let confirmationsAvailable = true;

function renderConfirmations(items) {
  const list = $("confirmation-list");
  list.replaceChildren();
  for (const item of items) {
    const row = el("li", "confirmation");
    row.append(el("p", "summary", item.summary || item.tool || "A tool wants to run"));
    const details = [item.tier];
    if (item.expires_at) details.push("expires " + new Date(item.expires_at).toLocaleTimeString());
    if (item.tier === "irreversible") details.push("can only be approved in the app");
    row.append(el("p", "meta", details.filter(Boolean).join(" · ")));
    const buttons = el("div", "buttons");
    for (const [label, approve] of [["Approve", true], ["Deny", false]]) {
      const button = el("button", approve ? "approve" : "deny", label);
      button.type = "button";
      button.addEventListener("click", () => resolveConfirmation(item.id, approve, row));
      buttons.append(button);
    }
    row.append(buttons);
    list.append(row);
  }
  $("confirmations").hidden = items.length === 0;
}

async function refreshConfirmations() {
  if (!confirmationsAvailable || !load(TOKEN_KEY)) return;
  try {
    const response = await api("/confirmations/pending");
    if (response.status === 404) {
      // The confirmations API isn't on this server yet.
      confirmationsAvailable = false;
      $("confirmations").hidden = true;
      return;
    }
    if (!response.ok) return;
    const body = await response.json();
    const items = Array.isArray(body) ? body : body.pending || body.items || [];
    renderConfirmations(items);
  } catch (_) {
    // leave the panel as it is
  }
}

async function resolveConfirmation(id, approve, row) {
  for (const button of row.querySelectorAll("button")) button.disabled = true;
  try {
    const response = await api("/confirmations/" + encodeURIComponent(id), {
      method: "POST",
      body: JSON.stringify({ approve }),
    });
    if (!response.ok) {
      row.append(el("p", "error", await errorText(response)));
      for (const button of row.querySelectorAll("button")) button.disabled = false;
      return;
    }
    let message = approve ? "Approved." : "Denied.";
    try {
      const body = await response.json();
      // Alfred's reply (the resumed run's answer, or "OK, I won't …"), else the status.
      if (body && typeof body.reply === "string") message = body.reply;
      else if (body && typeof body.message === "string") message = body.message;
    } catch (_) {
      // no body
    }
    addMessage("alfred", message);
  } catch (error) {
    row.append(el("p", "error", error.message));
  }
  refreshConfirmations();
}

// --- Start -------------------------------------------------------------------------------

showSession();
if (!load(TOKEN_KEY)) showTokenForm();
else refreshConfirmations();
