// Admin portal: sign in, external chat agents, MCP clients, recent activity.
//
// Talks only to this same server (/auth/admin/* and /admin/api/*) with
// credentials: "same-origin", so the httpOnly admin_session cookie does the
// authentication and no token is ever kept in JavaScript. Every value that
// came from the server is rendered with textContent, never innerHTML; the
// only innerHTML writes below insert the constant icon markup in ICONS.
// Confirmations are in page dialogs, not window.confirm/alert.

"use strict";

// Icons (same single stroke style as the main app)

const SVG_OPEN =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
const ICONS = {
  sun:
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"></circle>' +
    '<line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line>' +
    '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>' +
    '<line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line>' +
    '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>',
  moon:
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>',
  play: SVG_OPEN + '<polygon points="6 4 20 12 6 20 6 4"></polygon></svg>',
  edit: SVG_OPEN + '<path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"></path></svg>',
  trash:
    SVG_OPEN +
    '<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>' +
    '<path d="M10 11v6M14 11v6"></path><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path></svg>',
  revoke: SVG_OPEN + '<circle cx="12" cy="12" r="9"></circle><line x1="5.6" y1="5.6" x2="18.4" y2="18.4"></line></svg>',
  copy:
    SVG_OPEN +
    '<rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>',
  check: SVG_OPEN + '<polyline points="20 6 9 17 4 12"></polyline></svg>',
};

function iconSpan(name) {
  const span = document.createElement("span");
  span.className = "icon icon-arrow";
  span.innerHTML = ICONS[name]; // constant markup only
  return span;
}

const $ = (id) => document.getElementById(id);

// Theme toggle: same behavior and storage key as the main app (app.js).

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function applyThemeIcon() {
  const target = $("theme-toggle-icon");
  if (target) target.innerHTML = currentTheme() === "dark" ? ICONS.sun : ICONS.moon;
}

applyThemeIcon();
$("theme-toggle").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem("theme", next);
  } catch (err) {
    /* localStorage unavailable; theme choice just will not persist */
  }
  applyThemeIcon();
});

// Same cursor following glow as the main app.
let glowQueued = false;
let pointer = { x: "50%", y: "30%" };
window.addEventListener("mousemove", (event) => {
  pointer = {
    x: `${(event.clientX / window.innerWidth) * 100}%`,
    y: `${(event.clientY / window.innerHeight) * 100}%`,
  };
  if (!glowQueued) {
    glowQueued = true;
    requestAnimationFrame(() => {
      glowQueued = false;
      document.documentElement.style.setProperty("--mx", pointer.x);
      document.documentElement.style.setProperty("--my", pointer.y);
    });
  }
});

// HTTP

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function errorText(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail
      .map((item) => {
        const field = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .join("; ");
  }
  if (status === 429) return "Too many attempts. Please wait a minute and try again.";
  return `The request failed (HTTP ${status}).`;
}

async function api(path, { method = "GET", body, allowUnauthorized = false } = {}) {
  const init = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    throw new ApiError(0, "Could not reach the server. Is it running?");
  }
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }
  if (response.status === 401 && !allowUnauthorized) {
    showLogin("Your session has ended. Please sign in again.");
    throw new ApiError(401, "Not signed in.");
  }
  if (!response.ok) throw new ApiError(response.status, errorText(data, response.status));
  return data;
}

// Small DOM helpers

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function showMessage(node, text, kind) {
  node.textContent = text || "";
  node.classList.remove("form-message-error", "form-message-ok");
  if (kind === "error") node.classList.add("form-message-error");
  if (kind === "ok") node.classList.add("form-message-ok");
  node.classList.toggle("hidden", !text);
}

function formatTime(iso) {
  if (!iso) return "never";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function iconButton(iconName, label, onClick, extraClass) {
  const button = el("button", `icon-button small-icon-button${extraClass ? " " + extraClass : ""}`);
  button.type = "button";
  button.setAttribute("aria-label", label);
  button.title = label;
  button.appendChild(iconSpan(iconName));
  button.addEventListener("click", onClick);
  return button;
}

function emptyRow(tbody, columns, text) {
  const row = el("tr", "empty-row");
  const cell = el("td", "", text);
  cell.colSpan = columns;
  row.appendChild(cell);
  tbody.appendChild(row);
}

// Modals

let lastFocused = null;

function openModal(id) {
  lastFocused = document.activeElement;
  $(id).classList.remove("hidden");
  const focusTarget = $(id).querySelector("input:not([type=hidden]), textarea, button.choice-button");
  if (focusTarget) focusTarget.focus();
}

function closeModal(id) {
  $(id).classList.add("hidden");
  if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
}

document.querySelectorAll(".modal-overlay").forEach((overlay) => {
  overlay.addEventListener("click", (event) => {
    // The secret dialog only closes through its own button, so a stray
    // click outside it cannot lose the one chance to copy the secret.
    if (event.target === overlay && overlay.id !== "secret-modal") closeModal(overlay.id);
  });
  overlay.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", () => closeModal(overlay.id));
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  ["agent-modal", "confirm-modal"].forEach((id) => {
    if (!$(id).classList.contains("hidden")) closeModal(id);
  });
});

let confirmAction = null;

function askConfirm({ title, text, acceptLabel, onAccept }) {
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-accept").textContent = acceptLabel || "Delete";
  $("confirm-accept").disabled = false;
  confirmAction = onAccept;
  openModal("confirm-modal");
}

$("confirm-accept").addEventListener("click", async () => {
  if (!confirmAction) return;
  const action = confirmAction;
  confirmAction = null;
  $("confirm-accept").disabled = true;
  try {
    await action();
  } finally {
    closeModal("confirm-modal");
  }
});

// Screens and session

function showScreen(id) {
  document.querySelectorAll(".screen").forEach((screen) => screen.classList.toggle("active", screen.id === id));
}

function showLogin(message) {
  showScreen("screen-login");
  document.querySelectorAll(".modal-overlay").forEach((overlay) => overlay.classList.add("hidden"));
  showMessage($("login-error"), message || "", message ? "error" : undefined);
  $("login-password").value = "";
  $("login-username").focus();
}

function adminName(me) {
  if (!me) return "admin";
  return me.username || me.sub || me.name || "admin";
}

async function enterDashboard(me) {
  $("admin-name").textContent = adminName(me);
  showScreen("screen-dashboard");
  await Promise.allSettled([loadAgents(), loadMcpClients(), loadAudit()]);
}

async function checkSession() {
  try {
    const me = await api("/auth/admin/me", { allowUnauthorized: true });
    await enterDashboard(me);
  } catch (err) {
    showLogin(err.status && err.status !== 401 ? err.message : "");
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = $("login-username").value.trim();
  const password = $("login-password").value;
  if (!username || !password) {
    showMessage($("login-error"), "Enter your username and password.", "error");
    return;
  }
  $("login-submit").disabled = true;
  showMessage($("login-error"), "");
  try {
    await api("/auth/admin/login", { method: "POST", body: { username, password }, allowUnauthorized: true });
    $("login-password").value = "";
    const me = await api("/auth/admin/me", { allowUnauthorized: true }).catch(() => ({ username }));
    await enterDashboard(me);
  } catch (err) {
    const text = err.status === 401 ? "Incorrect username or password." : err.message;
    showMessage($("login-error"), text, "error");
  } finally {
    $("login-submit").disabled = false;
  }
});

$("logout-button").addEventListener("click", async () => {
  try {
    await api("/auth/admin/logout", { method: "POST", allowUnauthorized: true });
  } catch (err) {
    /* the cookie is cleared server side; show the sign in form either way */
  }
  showLogin("");
});

// External agents

let agents = [];
let activeAgentId = null;
let editingAgent = null;

function renderTestResult(node, name, result) {
  node.replaceChildren();
  node.classList.remove("hidden", "test-ok", "test-failed");
  node.classList.add(result.ok ? "test-ok" : "test-failed");
  const latency = result.latency_ms !== null && result.latency_ms !== undefined ? ` in ${result.latency_ms} ms` : "";
  const title = result.ok ? `${name} answered${latency}` : `${name} test failed${latency}`;
  node.appendChild(el("span", "test-result-title", title));
  node.appendChild(el("span", "test-result-reply", result.ok ? result.reply : result.error));
}

function renderTesting(node, name) {
  node.replaceChildren();
  node.classList.remove("hidden", "test-ok", "test-failed");
  node.appendChild(el("span", "test-result-title", `Testing ${name}...`));
}

function agentRadio(value, checked, label) {
  const wrapper = el("label", "radio-label");
  const radio = el("input");
  radio.type = "radio";
  radio.name = "active-agent";
  radio.value = value;
  radio.checked = checked;
  radio.setAttribute("aria-label", label);
  radio.addEventListener("change", () => setActiveAgent(value === "" ? null : Number(value)));
  wrapper.appendChild(radio);
  return wrapper;
}

function renderAgents() {
  const tbody = $("agents-body");
  tbody.replaceChildren();

  // The built in assistant is always the first choice.
  const builtIn = el("tr", activeAgentId === null ? "row-selected" : "");
  const radioCell = el("td");
  radioCell.appendChild(agentRadio("", activeAgentId === null, "Use the built in assistant"));
  builtIn.appendChild(radioCell);
  const nameCell = el("td");
  nameCell.appendChild(el("span", "cell-main", "Built in assistant"));
  nameCell.appendChild(el("span", "cell-sub", "Mobitel knowledge base, cloud or local model"));
  builtIn.appendChild(nameCell);
  const builtInEndpoint = el("td");
  builtInEndpoint.appendChild(el("span", "cell-sub", "This server"));
  builtIn.appendChild(builtInEndpoint);
  const builtInBadges = el("td");
  const badgeWrap = el("div", "badges-inline");
  badgeWrap.appendChild(el("span", "badge badge-local", "Default"));
  builtInBadges.appendChild(badgeWrap);
  builtIn.appendChild(builtInBadges);
  builtIn.appendChild(el("td"));
  tbody.appendChild(builtIn);

  if (!agents.length) {
    emptyRow(tbody, 5, "No external agents yet. Add one to let another chat agent answer through this assistant.");
    return;
  }

  agents.forEach((agent) => {
    const isActive = agent.id === activeAgentId;
    const row = el("tr", isActive ? "row-selected" : "");

    const radio = el("td");
    radio.appendChild(agentRadio(String(agent.id), isActive, `Use ${agent.name}`));
    row.appendChild(radio);

    const name = el("td");
    name.appendChild(el("span", "cell-main", agent.name));
    name.appendChild(el("span", "cell-sub", `Updated ${formatTime(agent.updated_at)}`));
    row.appendChild(name);

    const endpoint = el("td");
    const url = el("div", "cell-url", `${agent.method} ${agent.url}`);
    url.title = agent.url;
    endpoint.appendChild(url);
    endpoint.appendChild(el("span", "cell-sub", `Reply at ${agent.response_path || "(whole body)"}`));
    row.appendChild(endpoint);

    const options = el("td");
    const badges = el("div", "badges-inline");
    if (isActive) badges.appendChild(el("span", "badge badge-cloud", "Answering"));
    badges.appendChild(el("span", "badge badge-muted", agent.streaming ? "Streaming" : "JSON"));
    badges.appendChild(el("span", "badge badge-muted", agent.has_auth_header ? "Auth set" : "No auth"));
    badges.appendChild(el("span", "badge badge-muted", `${agent.timeout_seconds}s`));
    options.appendChild(badges);
    row.appendChild(options);

    const actions = el("td", "col-actions");
    const group = el("div", "row-actions");
    group.appendChild(iconButton("play", `Test ${agent.name}`, () => testSavedAgent(agent)));
    group.appendChild(iconButton("edit", `Edit ${agent.name}`, () => openAgentForm(agent)));
    group.appendChild(
      iconButton(
        "trash",
        `Delete ${agent.name}`,
        () =>
          askConfirm({
            title: `Delete ${agent.name}?`,
            text: isActive
              ? "This agent is answering right now. After deleting it, the built in assistant answers instead."
              : "Its settings, including the saved auth header, are removed.",
            acceptLabel: "Delete agent",
            onAccept: () => deleteAgent(agent),
          }),
        "cut-button"
      )
    );
    actions.appendChild(group);
    row.appendChild(actions);
    tbody.appendChild(row);
  });
}

async function loadAgents() {
  try {
    const data = await api("/admin/api/agents");
    agents = data.agents || [];
    activeAgentId = data.active_agent_id === undefined ? null : data.active_agent_id;
    renderAgents();
  } catch (err) {
    if (err.status !== 401) showMessage($("agents-message"), err.message, "error");
  }
}

async function setActiveAgent(agentId) {
  const previous = activeAgentId;
  showMessage($("agents-message"), "");
  try {
    if (agentId === null) {
      await api("/admin/api/agents/deactivate", { method: "POST" });
      showMessage($("agents-message"), "The built in assistant answers from the next turn.", "ok");
    } else {
      const agent = await api(`/admin/api/agents/${agentId}/activate`, { method: "POST" });
      showMessage($("agents-message"), `${agent.name} answers from the next turn.`, "ok");
    }
    await loadAgents();
  } catch (err) {
    activeAgentId = previous;
    renderAgents();
    if (err.status !== 401) showMessage($("agents-message"), err.message, "error");
  }
}

async function testSavedAgent(agent) {
  const target = $("agent-test-result");
  renderTesting(target, agent.name);
  try {
    const result = await api(`/admin/api/agents/${agent.id}/test`, { method: "POST", body: {} });
    renderTestResult(target, agent.name, result);
  } catch (err) {
    if (err.status !== 401) renderTestResult(target, agent.name, { ok: false, error: err.message });
  }
}

async function deleteAgent(agent) {
  try {
    await api(`/admin/api/agents/${agent.id}`, { method: "DELETE" });
    showMessage($("agents-message"), `${agent.name} was deleted.`, "ok");
    $("agent-test-result").classList.add("hidden");
    await loadAgents();
  } catch (err) {
    if (err.status !== 401) showMessage($("agents-message"), err.message, "error");
  }
}

function openAgentForm(agent) {
  editingAgent = agent || null;
  $("agent-modal-title").textContent = agent ? `Edit ${agent.name}` : "Add agent";
  $("agent-name").value = agent ? agent.name : "";
  $("agent-url").value = agent ? agent.url : "";
  $("agent-method").value = agent ? agent.method : "POST";
  $("agent-auth-name").value = agent ? agent.auth_header_name || "" : "";
  $("agent-auth-value").value = "";
  $("agent-auth-value").placeholder =
    agent && agent.has_auth_header ? "Saved. Leave blank to keep it" : "Optional";
  $("agent-auth-clear").checked = false;
  $("agent-auth-clear-row").classList.toggle("hidden", !(agent && agent.has_auth_header));
  $("agent-body").value = agent ? agent.body_template : '{"message": "{text}"}';
  $("agent-path").value = agent ? agent.response_path : "reply";
  $("agent-timeout").value = agent ? agent.timeout_seconds : 15;
  $("agent-streaming").checked = agent ? agent.streaming : false;
  showMessage($("agent-form-message"), "");
  $("agent-form-test").classList.add("hidden");
  openModal("agent-modal");
}

function agentFormValues() {
  const timeout = $("agent-timeout").value.trim();
  return {
    name: $("agent-name").value.trim(),
    url: $("agent-url").value.trim(),
    method: $("agent-method").value,
    auth_header_name: $("agent-auth-name").value.trim() || null,
    auth_header_value: $("agent-auth-value").value || null,
    clear_auth_header: $("agent-auth-clear").checked,
    body_template: $("agent-body").value,
    response_path: $("agent-path").value.trim(),
    timeout_seconds: timeout ? Number(timeout) : null,
    streaming: $("agent-streaming").checked,
  };
}

$("add-agent").addEventListener("click", () => openAgentForm(null));

$("agent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = agentFormValues();
  if (!values.name || !values.url) {
    showMessage($("agent-form-message"), "Name and URL are required.", "error");
    return;
  }
  $("agent-save").disabled = true;
  showMessage($("agent-form-message"), "");
  try {
    const saved = editingAgent
      ? await api(`/admin/api/agents/${editingAgent.id}`, { method: "PUT", body: values })
      : await api("/admin/api/agents", { method: "POST", body: values });
    closeModal("agent-modal");
    showMessage($("agents-message"), `${saved.name} was saved.`, "ok");
    await loadAgents();
  } catch (err) {
    if (err.status !== 401) showMessage($("agent-form-message"), err.message, "error");
  } finally {
    $("agent-save").disabled = false;
  }
});

$("agent-test-draft").addEventListener("click", async () => {
  const values = agentFormValues();
  const name = values.name || "Agent";
  const target = $("agent-form-test");
  showMessage($("agent-form-message"), "");
  renderTesting(target, name);
  $("agent-test-draft").disabled = true;
  try {
    const body = Object.assign({}, values, { id: editingAgent ? editingAgent.id : null });
    const result = await api("/admin/api/agents/test", { method: "POST", body });
    renderTestResult(target, name, result);
  } catch (err) {
    if (err.status !== 401) renderTestResult(target, name, { ok: false, error: err.message });
  } finally {
    $("agent-test-draft").disabled = false;
  }
});

// MCP clients

function renderMcpClients(clients) {
  const tbody = $("mcp-body");
  tbody.replaceChildren();
  if (!clients.length) {
    emptyRow(tbody, 7, "No MCP clients yet.");
    return;
  }
  clients.forEach((client) => {
    const row = el("tr");
    row.appendChild(el("td", "cell-main", client.name));
    row.appendChild(el("td", "cell-mono", client.client_id));

    const scopes = el("td");
    const scopeBadges = el("div", "badges-inline");
    (client.scopes || []).forEach((scope) => scopeBadges.appendChild(el("span", "badge badge-local", scope)));
    scopes.appendChild(scopeBadges);
    row.appendChild(scopes);

    row.appendChild(el("td", "cell-nowrap", formatTime(client.created_at)));
    row.appendChild(el("td", "cell-nowrap", formatTime(client.last_used_at)));

    const status = el("td");
    if (client.active) {
      status.appendChild(el("span", "badge badge-cloud", "Active"));
    } else {
      status.appendChild(el("span", "badge badge-muted", "Revoked"));
      status.appendChild(el("span", "cell-sub", formatTime(client.revoked_at)));
    }
    row.appendChild(status);

    const actions = el("td", "col-actions");
    if (client.active) {
      actions.appendChild(
        iconButton(
          "revoke",
          `Revoke ${client.name}`,
          () =>
            askConfirm({
              title: `Revoke ${client.name}?`,
              text: "Its access tokens stop working immediately and it can no longer get new ones. This cannot be undone.",
              acceptLabel: "Revoke client",
              onAccept: () => revokeClient(client),
            }),
          "cut-button"
        )
      );
    }
    row.appendChild(actions);
    tbody.appendChild(row);
  });
}

async function loadMcpClients() {
  try {
    renderMcpClients(await api("/admin/api/mcp-clients"));
  } catch (err) {
    if (err.status !== 401) {
      renderMcpClients([]);
      showMessage($("mcp-message"), err.message, "error");
    }
  }
}

async function revokeClient(client) {
  try {
    await api(`/admin/api/mcp-clients/${client.id}`, { method: "DELETE" });
    showMessage($("mcp-message"), `${client.name} was revoked.`, "ok");
    await Promise.allSettled([loadMcpClients(), loadAudit()]);
  } catch (err) {
    if (err.status !== 401) showMessage($("mcp-message"), err.message, "error");
  }
}

$("mcp-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("mcp-name").value.trim();
  const scopes = Array.from(document.querySelectorAll('input[name="mcp-scope"]:checked')).map((box) => box.value);
  if (!name) {
    showMessage($("mcp-message"), "Enter a name for the client.", "error");
    return;
  }
  if (!scopes.length) {
    showMessage($("mcp-message"), "Choose at least one scope.", "error");
    return;
  }
  $("mcp-create").disabled = true;
  showMessage($("mcp-message"), "");
  try {
    const created = await api("/admin/api/mcp-clients", { method: "POST", body: { name, scopes } });
    $("mcp-name").value = "";
    $("secret-client-id").textContent = created.client.client_id;
    $("secret-value").textContent = created.client_secret;
    showMessage($("secret-copy-status"), "");
    openModal("secret-modal");
    await Promise.allSettled([loadMcpClients(), loadAudit()]);
  } catch (err) {
    if (err.status !== 401) showMessage($("mcp-message"), err.message, "error");
  } finally {
    $("mcp-create").disabled = false;
  }
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.appendChild(iconSpan("copy"));
  button.addEventListener("click", async () => {
    const source = $(button.getAttribute("data-copy-target"));
    const text = source.textContent;
    let copied = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch (err) {
      // Clipboard API unavailable (plain http on another host): select the
      // text so Ctrl+C copies it.
      const range = document.createRange();
      range.selectNodeContents(source);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    showMessage(
      $("secret-copy-status"),
      copied ? "Copied to the clipboard." : "Selected. Press Ctrl+C to copy.",
      copied ? "ok" : undefined
    );
    if (copied) {
      button.replaceChildren(iconSpan("check"));
      setTimeout(() => button.replaceChildren(iconSpan("copy")), 1500);
    }
  });
});

$("secret-done").addEventListener("click", () => {
  // Drop the secret from the page as soon as it has been acknowledged.
  $("secret-value").textContent = "";
  $("secret-client-id").textContent = "";
  closeModal("secret-modal");
});

// Audit

function outcomeBadge(outcome) {
  const kind = outcome === "ok" ? "badge-cloud" : outcome === "denied" ? "badge-warn" : "badge-bad";
  return el("span", `badge ${kind}`, outcome);
}

async function loadAudit() {
  const tbody = $("audit-body");
  try {
    const entries = await api("/admin/api/audit?limit=50");
    showMessage($("audit-message"), "");
    tbody.replaceChildren();
    if (!entries.length) {
      emptyRow(tbody, 6, "No activity recorded yet.");
      return;
    }
    entries.forEach((entry) => {
      const row = el("tr");
      row.appendChild(el("td", "cell-nowrap", formatTime(entry.created_at)));
      const actor = el("td");
      actor.appendChild(el("span", "cell-main", entry.client_name || entry.actor_id));
      actor.appendChild(el("span", "cell-sub", entry.actor_type === "admin" ? "admin" : "MCP client"));
      row.appendChild(actor);
      row.appendChild(el("td", "cell-mono", entry.action));
      const outcome = el("td");
      outcome.appendChild(outcomeBadge(entry.outcome));
      row.appendChild(outcome);
      row.appendChild(
        el("td", "cell-nowrap", entry.duration_ms !== null && entry.duration_ms !== undefined ? `${entry.duration_ms} ms` : "")
      );
      row.appendChild(el("td", "cell-detail", entry.detail || ""));
      tbody.appendChild(row);
    });
  } catch (err) {
    if (err.status !== 401) {
      tbody.replaceChildren();
      emptyRow(tbody, 6, "Activity is not available.");
      showMessage($("audit-message"), err.message, "error");
    }
  }
}

$("audit-refresh").addEventListener("click", loadAudit);

checkSession();
