// DOK Scanner central admin dashboard.
// Every value from the API is placed into the page with text nodes only
// (see h()), never as markup, and the session token lives in memory only.
(() => {
  "use strict";

  const view = document.getElementById("view");
  let csrf = "";

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

  const badge = (text, kind) => h("span", { class: "badge " + (kind || "") }, text);
  const short = (id) => String(id).slice(0, 8);

  // ------------------------------------------------------------------ views
  async function overview() {
    const o = await api("GET", "/admin/overview");
    const stat = (n, label) => h("div", { class: "card stat" }, h("div", { class: "n" }, n), h("div", { class: "l" }, label));
    return h("div", {},
      h("h1", {}, "Overview"),
      h("div", { class: "cards" },
        stat(o.pcs.online + " / " + o.pcs.registered, "PCs online / registered"),
        stat(o.pcs.total, "PCs in total"),
        stat(o.projects, "Projects"),
        stat(o.templates, "Templates"),
        stat(o.admins.active + " / " + o.admins.total, "Admins active / total"),
        stat(o.poll_interval_seconds + " s", "PC poll interval")),
      o.pcs.offline.length
        ? h("div", {}, h("h2", {}, "PCs that have stopped checking in"),
            h("p", {}, o.pcs.offline.map((n) => badge(n, "bad")),
              h("span", { class: "muted" }, " (silent for over " + Math.round(o.pcs.online_window_seconds / 60) + " minutes)")))
        : h("p", { class: "muted" }, "Every registered PC is checking in."),
      h("h2", {}, "Recent activity"),
      auditTable(o.recent_audit),
      h("p", {}, h("a", { href: "#/audit" }, "Open the full audit log")));
  }

  function detailText(detail) {
    const parts = Object.entries(detail || {}).map(([k, v]) =>
      k + "=" + (typeof v === "object" ? JSON.stringify(v) : v));
    return parts.join("  ");
  }

  function auditTable(events) {
    return table(["Time", "Who", "Action", "Details"],
      events.map((e) => [when(e.at), e.actor, h("code", {}, e.action), h("span", { class: "muted" }, detailText(e.detail))]),
      "No events yet.");
  }

  async function pcs() {
    const [list, projects] = await Promise.all([api("GET", "/admin/pcs"), api("GET", "/admin/projects")]);
    const names = Object.fromEntries(projects.map((p) => [p.id, p.name]));
    const reload = () => render();
    const secret = (r, title) => showSecret(title,
      "Type this one-time code into DOK Scanner (Admin > Central) on the PC. It is shown only now.",
      r.enrollment_code, r.expires_at);
    return h("div", {},
      h("h1", {}, "PCs"),
      h("div", { class: "row" },
        h("label", {}, "Register a new PC", h("input", { id: "new-pc", placeholder: "e.g. Scan-Desk-1", maxlength: 200 })),
        button("Add PC", () => {
          const name = document.getElementById("new-pc").value.trim();
          if (!name) { toast("Give the PC a name.", true); return; }
          act(() => api("POST", "/admin/pcs", { display_name: name }),
            async (r) => { await secret(r, "Enrollment code for " + name); reload(); }, "PC added.");
        }, "primary")),
      table(["Name", "Status", "Last seen", "Projects", ""], list.map((p) => [
        p.display_name,
        !p.registered ? badge("not enrolled yet", "warn") : p.online ? badge("online", "ok") : badge("offline", "bad"),
        p.last_seen_at ? when(p.last_seen_at) + " (" + ago(p.last_seen_at) + ")" : "never",
        p.assigned_project_ids.map((id) => names[id] || short(id)).join(", ") || "none",
        actions(
          button("Rename", async () => {
            const v = await ask("Rename PC", [{ name: "name", label: "New name", value: p.display_name }], "Rename");
            if (v) act(() => api("PATCH", "/admin/pcs/" + p.id, { display_name: v.name.trim() }), reload, "Renamed.");
          }),
          button("New enrollment code", () => act(
            () => api("POST", "/admin/pcs/" + p.id + "/enrollment-code"),
            async (r) => { await secret(r, "New code for " + p.display_name); reload(); })),
          button("Revoke key", () => {
            if (confirm("Revoke the access key of " + p.display_name + "? It stops receiving configuration until it is enrolled again."))
              act(() => api("DELETE", "/admin/pcs/" + p.id + "/key"), reload, "Key revoked.");
          }, "danger"),
          button("Delete", () => {
            if (confirm("Delete " + p.display_name + " and its assignments? This cannot be undone."))
              act(() => api("DELETE", "/admin/pcs/" + p.id), reload, "PC deleted.");
          }, "danger"))]),
        "No PCs registered yet."));
  }

  // Projects and templates share one list + detail layout.
  function configKind(kind) {
    const plural = kind + "s";
    const base = "/admin/" + plural;

    async function list() {
      const rows = await api("GET", base);
      return h("div", {},
        h("h1", {}, kind === "project" ? "Projects" : "Templates"),
        h("div", { class: "row" }, button("New " + kind + "...", () => editor(null), "primary")),
        table(["Name", kind === "template" ? "Category" : "ID", "Version", ""].filter(Boolean),
          rows.map((r) => [r.name, kind === "template" ? (r.category || "") + (r.built_in ? " (built in)" : "") : short(r.id),
            "v" + r.version, actions(button("Open", () => { location.hash = "#/" + plural + "/" + r.id; }))])));
    }

    async function editor(existing) {
      let starter = existing ? JSON.stringify(existing.config, null, 2) : "";
      if (!existing && kind === "project") {
        try { starter = JSON.stringify(await api("GET", "/admin/project-defaults"), null, 2); }
        catch (e) { if (e.status === 401) return; }
      }
      const fields = [{ name: "name", label: "Name", value: existing ? existing.name : "" }];
      if (kind === "template") fields.push({ name: "category", label: "Category", value: existing ? existing.category : "" });
      fields.push({ name: "config", label: "Configuration (JSON)", rows: 16, max: 300000,
        value: starter,
        hint: "Edit, paste or import the configuration. It is checked on save; credentials are not accepted." });
      const importer = h("input", { type: "file", accept: ".json,application/json" });
      importer.addEventListener("change", () => {
        const file = importer.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => { dialogEl.querySelector("textarea").value = String(reader.result); };
        reader.readAsText(file);
      });
      const v = await ask((existing ? "Edit " : "New ") + kind, fields, "Save",
        h("label", {}, "Import from a .json file", importer));
      if (!v) return;
      let config;
      try { config = JSON.parse(v.config); } catch (e) { toast("The configuration is not valid JSON.", true); return; }
      if (config === null || typeof config !== "object" || Array.isArray(config)) { toast("The configuration must be a JSON object.", true); return; }
      const id = existing ? existing.id : crypto.randomUUID();
      const body = { name: v.name.trim(), config };
      if (kind === "template") body.category = (v.category || "").trim();
      act(() => api("PUT", base + "/" + id, body),
        (r) => { location.hash = "#/" + plural + "/" + id; if (existing) render(); },
        existing ? "Saved." : "Created.");
    }

    async function detail(id) {
      const [item, versions, pcList] = await Promise.all([
        api("GET", base + "/" + id), api("GET", base + "/" + id + "/versions"),
        kind === "project" ? api("GET", "/admin/pcs") : Promise.resolve([])]);
      const viewer = h("pre", { id: "config-view" }, JSON.stringify(item.config, null, 2));
      const parts = [
        h("p", {}, h("a", { href: "#/" + plural }, "< All " + plural)),
        h("h1", {}, item.name, " ", badge("v" + item.version)),
        h("div", { class: "row" },
          button("Edit / new version", () => editor(item), "primary"))];
      if (kind === "project") {
        const have = new Set(pcList.filter((p) => p.assigned_project_ids.includes(id)).map((p) => p.id));
        parts.push(h("h2", {}, "Assigned PCs"),
          h("div", { class: "row" },
            button("Assign to all PCs", () => act(() => api("POST", base + "/" + id + "/push", { all_pcs: true }), render, "Assigned to all PCs.")),
            h("span", { class: "muted" }, "PCs pick up a new version at their next poll.")),
          table(["PC", "Assigned", ""], pcList.map((p) => [p.display_name,
            have.has(p.id) ? badge("assigned", "ok") : badge("not assigned"),
            actions(have.has(p.id)
              ? button("Unassign", () => act(() => api("DELETE", base + "/" + id + "/assignments/" + p.id), render, "Unassigned."), "danger")
              : button("Assign", () => act(() => api("POST", base + "/" + id + "/push", { pc_ids: [p.id] }), render, "Assigned.")))]),
            "Register a PC first."));
      }
      parts.push(h("h2", {}, "Versions"),
        table(["Version", "Saved by", "When", ""], versions.map((v) => [
          "v" + v.version, v.created_by, when(v.created_at),
          actions(button("View", () => act(() => api("GET", base + "/" + id + "/versions/" + v.version),
            (r) => { viewer.textContent = JSON.stringify(r.config, null, 2); })))]), "No versions."),
        h("h2", {}, "Configuration"), viewer);
      return h("div", {}, parts);
    }
    return { list, detail };
  }
  const projectViews = configKind("project");
  const templateViews = configKind("template");

  async function admins() {
    const list = await api("GET", "/admin/users");
    const reload = () => render();
    const tone = { active: "ok", locked: "warn", disabled: "bad" };
    return h("div", {},
      h("h1", {}, "Admins"),
      h("div", { class: "row" },
        button("Add admin...", async () => {
          const v = await ask("Add admin", [
            { name: "username", label: "Username", hint: "3-100 letters, digits . _ @ -" },
            { name: "password", label: "Password", type: "password", hint: "At least 12 characters." }], "Create");
          if (v) act(() => api("POST", "/admin/users", { username: v.username.trim(), password: v.password }), reload, "Admin created.");
        }, "primary"),
        button("Sign everyone out", () => {
          if (confirm("Sign out every admin, including you?"))
            act(() => api("POST", "/admin/users/revoke-all-sessions"), () => showLogin(), "All sessions revoked.");
        }, "danger")),
      table(["Username", "Status", "Failed logins", ""], list.map((u) => [
        u.username, badge(u.status + (u.locked_until ? " until " + when(u.locked_until) : ""), tone[u.status]), u.failed_count,
        actions(
          u.status === "disabled"
            ? button("Enable", () => act(() => api("POST", "/admin/users/" + u.id + "/enable"), reload, "Enabled."))
            : button("Disable", () => { if (confirm("Disable " + u.username + "? They are signed out and cannot sign in."))
                act(() => api("POST", "/admin/users/" + u.id + "/disable"), reload, "Disabled."); }, "danger"),
          u.status === "locked" ? button("Unlock", () => act(() => api("POST", "/admin/users/" + u.id + "/unlock"), reload, "Unlocked.")) : null,
          button("Reset password", async () => {
            const v = await ask("Reset password for " + u.username, [
              { name: "password", label: "New password", type: "password", hint: "At least 12 characters. They are signed out." }], "Reset");
            if (v) act(() => api("POST", "/admin/users/" + u.id + "/password", { password: v.password }), reload, "Password reset.");
          }),
          button("Sign out", () => act(() => api("POST", "/admin/users/" + u.id + "/revoke-sessions"), reload, "Signed out.")))])));
  }

  async function audit() {
    const actionList = await api("GET", "/admin/audit/actions");
    const filters = { action: "", actor: "" };
    const body = h("div", {});
    let next = null;
    const holder = h("div", {});
    const more = button("Load more", () => load(false));
    async function load(reset) {
      if (reset) { clear(holder); next = null; }
      const q = new URLSearchParams({ limit: "50" });
      if (filters.action) q.set("action", filters.action);
      if (filters.actor) q.set("actor", filters.actor.trim());
      if (next) q.set("before_id", next);
      try {
        const page = await api("GET", "/admin/audit?" + q.toString());
        holder.append(auditTable(page.events));
        next = page.next_before_id;
        more.hidden = !next;
      } catch (e) { if (e.status !== 401) toast(e.message, true); }
    }
    const select = h("select", {}, h("option", { value: "" }, "All actions"),
      actionList.map((a) => h("option", { value: a }, a)));
    select.addEventListener("change", () => { filters.action = select.value; load(true); });
    const actor = h("input", { placeholder: "e.g. admin:root", maxlength: 150 });
    actor.addEventListener("change", () => { filters.actor = actor.value; load(true); });
    body.append(h("h1", {}, "Audit log"),
      h("div", { class: "row" }, h("label", {}, "Action", select), h("label", {}, "Who", actor)),
      holder, h("div", { class: "row" }, more));
    await load(true);
    return body;
  }

  async function settings() {
    const s = await api("GET", "/admin/settings");
    const input = h("input", { type: "number", min: 30, max: 86400, value: String(s.poll_interval_seconds) });
    return h("div", {},
      h("h1", {}, "Settings"),
      h("div", { class: "card" },
        h("label", {}, "How often PCs check for new configuration (seconds, 30 to 86400)", input),
        h("div", { class: "row" }, button("Save", () => {
          const n = Number(input.value);
          if (!Number.isInteger(n)) { toast("Enter a whole number of seconds.", true); return; }
          act(() => api("PUT", "/admin/settings", { poll_interval_seconds: n }), null, "Saved.");
        }, "primary"))));
  }

  // ----------------------------------------------------------------- router
  const routes = {
    overview, pcs, admins, audit, settings,
    projects: (id) => (id ? projectViews.detail(id) : projectViews.list()),
    templates: (id) => (id ? templateViews.detail(id) : templateViews.list()),
  };

  async function render() {
    const [name, id] = (location.hash.replace(/^#\//, "") || "overview").split("/");
    const route = routes[name] || overview;
    for (const a of document.querySelectorAll("#nav a")) {
      a.classList.toggle("active", a.dataset.view === (routes[name] ? name : "overview"));
    }
    try {
      const content = await route(id);
      clear(view);
      view.append(content);
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
    document.getElementById("who-name").textContent = me.username;
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
})();
