// The router and sign in/out; loaded last.
"use strict";

// ----------------------------------------------------------------- router
const routes = {
  overview, fleet, activity, pcs, admins, audit, settings, search, access: accessLog,
  documents: (id) => (id ? documentDetail(id) : search()),
  projects: (id) => (id ? projectViews.detail(id) : projectViews.list()),
  templates: (id) => (id ? templateViews.detail(id) : templateViews.list()),
};

async function render() {
  const myRender = ++renderId;
  clearInterval(refreshTimer);
  refreshTimer = null;
  const [name, id] = (location.hash.replace(/^#\//, "") || (role === "searcher" ? "search" : "overview")).split("/");
  const allowed = role !== "searcher" || ["search", "documents"].includes(name);
  const route = (allowed && routes[name]) || (role === "searcher" ? search : overview);
  for (const a of document.querySelectorAll("#nav a")) {
    a.classList.toggle("active", a.dataset.view === (name === "documents" ? "search" : name === "access" ? "audit" : (routes[name] ? name : "overview")));
  }
  try {
    const content = await route(id);
    if (myRender !== renderId) return;       // the person moved on while this was loading
    clear(view);
    view.append(content);
    if (route.refresh) {
      refreshTimer = setInterval(() => { if (!document.hidden && !dialogEl.open) render(); }, route.refresh);
    }
    updateAlertBadge();
  } catch (e) {
    if (e.status === 401) return;
    clear(view);
    view.append(h("p", { class: "error" }, e.message));
  }
}

// ------------------------------------------------------------ sign in/out
function showLogin() {
  csrf = "";
  document.getElementById("top").hidden = true;
  view.hidden = true;
  clear(view);
  document.getElementById("login").hidden = false;
  document.getElementById("login-pass").value = "";
}

function showApp(me) {
  csrf = me.csrf_token || "";
  role = me.role || "admin";
  document.getElementById("who-name").textContent =
    me.username + (role === "auditor" ? " (read-only)" : role === "searcher" ? " (search only)" : "");
  for (const a of document.querySelectorAll("#nav a")) a.hidden = role === "searcher" && a.dataset.view !== "search";
  document.getElementById("login").hidden = true;
  document.getElementById("top").hidden = false;
  view.hidden = false;
  render();
}

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("login-error");
  error.textContent = "";
  try {
    const me = await api("POST", "/admin/login", {
      username: document.getElementById("login-user").value,
      password: document.getElementById("login-pass").value });
    showApp(me);
  } catch (e) {
    error.textContent = e.status === 401 ? "Wrong username or password." : e.message;
  }
});

document.getElementById("sign-out").addEventListener("click", () => act(
  () => api("POST", "/admin/logout"), () => showLogin()));

document.getElementById("change-password").addEventListener("click", async () => {
  const v = await ask("Change your password", [
    { name: "current", label: "Current password", type: "password" },
    { name: "next", label: "New password", type: "password", hint: "At least 12 characters. You will sign in again." }], "Change");
  if (v) act(() => api("POST", "/admin/me/password", { current_password: v.current, new_password: v.next }),
    () => showLogin(), "Password changed. Please sign in again.");
});

window.addEventListener("hashchange", () => { if (!view.hidden) render(); });

(async () => {
  try {
    const response = await fetch("/admin/me", { credentials: "same-origin" });
    if (response.ok) { showApp(await response.json()); return; }
  } catch (e) { /* fall through to the sign-in form */ }
  showLogin();
})();
