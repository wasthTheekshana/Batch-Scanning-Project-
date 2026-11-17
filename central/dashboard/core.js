// DOK Scanner central admin dashboard.
// Every value from the API is placed into the page with text nodes only
// (see h()), never as markup, and the session token lives in memory only.
// Shared by every file below: classic scripts, so these names are one page-wide scope.
// Load order is in index.html; app.js (router and sign-in) comes last.
"use strict";

const view = document.getElementById("view");
let csrf = "";
let role = "admin";
let renderId = 0;          // a newer render makes an older, slower one discard its result
let refreshTimer = null;   // views that keep themselves current (the fleet) set it

// ---------------------------------------------------------------- helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else if (key === "checked" || key === "disabled") el[key] = true;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

function when(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "?" : d.toLocaleString();
}

function ago(iso) {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function describe(data) {
  if (!data || data.detail === undefined) return "";
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((e) => (e.loc ? e.loc.slice(1).join(".") + ": " : "") + (e.msg || "invalid")).join("; ");
  }
  return "";
}

async function api(method, path, body) {
  const options = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") options.headers["X-CSRF-Token"] = csrf;
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (response.status === 401 && path !== "/admin/login") {
    showLogin();
    throw new ApiError(401, "Please sign in again.");
  }
  let data = null;
  if (response.status !== 204) {
    try { data = await response.json(); } catch (e) { data = null; }
  }
  if (!response.ok) {
    throw new ApiError(response.status, describe(data) || "Request failed (" + response.status + ").");
  }
  return data;
}

const toastBox = document.getElementById("toast");
function toast(message, bad) {
  const note = h("p", { class: bad ? "bad" : "" }, message);
  toastBox.append(note);
  setTimeout(() => note.remove(), bad ? 8000 : 4000);
}

// Run an action; report success or the error in a toast; then refresh.
async function act(work, done, success) {
  try {
    const result = await work();
    if (success) toast(success);
    if (done) await done(result);
  } catch (e) {
    if (e.status !== 401) toast(e.message, true);
  }
}

// A modal form. fields: [{name,label,type,value,hint,rows}]. Resolves to
// {name: value} on submit, or null when cancelled.
const dialogEl = document.getElementById("dialog");
function ask(title, fields, submit, extra) {
  return new Promise((resolve) => {
    const inputs = {};
    const error = h("p", { class: "error", role: "alert" });
    const form = h("form", { method: "dialog" },
      h("h1", {}, title),
      extra || null,
      fields.map((f) => {
        const input = f.rows
          ? h("textarea", { rows: f.rows, value: f.value || "" })
          : h("input", { type: f.type || "text", value: f.value || "",
                         autocomplete: f.type === "password" ? "new-password" : "off",
                         maxlength: f.max || 200 });
        inputs[f.name] = input;
        return h("label", {}, f.label, input, f.hint ? h("span", { class: "muted" }, f.hint) : null);
      }),
      error,
      h("div", { class: "buttons" },
        h("button", { type: "button", onclick: () => dialogEl.close() }, "Cancel"),
        h("button", { type: "submit", class: "primary" }, submit || "OK")));
    let result = null;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      result = {};
      for (const name of Object.keys(inputs)) result[name] = inputs[name].value;
      dialogEl.close();
    });
    dialogEl.addEventListener("close", () => { clear(dialogEl); resolve(result); }, { once: true });
    clear(dialogEl);
    dialogEl.append(form);
    dialogEl.showModal();
    const first = Object.values(inputs)[0];
    if (first) first.focus();
  });
}

// Show something once (an enrollment code): modal with the secret.
function showSecret(title, note, secret, expires) {
  return new Promise((resolve) => {
    clear(dialogEl);
    const copy = h("button", { type: "button", onclick: async () => {
      try { await navigator.clipboard.writeText(secret); toast("Copied."); }
      catch (e) { toast("Copy failed: select the text and copy it.", true); }
    } }, "Copy");
    dialogEl.append(h("form", { method: "dialog" },
      h("h1", {}, title), h("p", {}, note),
      h("div", { class: "secret mono" }, secret),
      expires ? h("p", { class: "muted" }, "Expires " + when(expires)) : null,
      h("div", { class: "buttons" }, copy,
        h("button", { type: "submit", class: "primary" }, "Done"))));
    dialogEl.addEventListener("close", () => { clear(dialogEl); resolve(); }, { once: true });
    dialogEl.showModal();
  });
}

function table(headers, rows, empty) {
  if (!rows.length) return h("p", { class: "muted" }, empty || "Nothing here yet.");
  return h("table", {},
    h("thead", {}, h("tr", {}, headers.map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((c) =>
      (c instanceof Node && c.dataset && c.dataset.actions) ? c : h("td", {}, c))))));
}

function actions(...buttons) {
  const td = h("td", { class: "actions" }, buttons);
  td.dataset.actions = "1";
  return td;
}

function button(label, onclick, kind) {
  return h("button", { type: "button", class: kind || "", onclick }, label);
}

// A button that changes something: an auditor's account is read-only, so
// they are not shown it (the server refuses the change anyway).
function wbutton(label, onclick, kind) {
  return role !== "admin" ? null : button(label, onclick, kind);
}

const badge = (text, kind) => h("span", { class: "badge " + (kind || "") }, text);
const short = (id) => String(id).slice(0, 8);
