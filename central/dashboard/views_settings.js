// Admin users, audit log, webhook and settings.
"use strict";

async function admins() {
  const list = await api("GET", "/admin/users");
  const reload = () => render();
  const tone = { active: "ok", locked: "warn", disabled: "bad" };
  return h("div", {},
    h("h1", {}, "Admins"),
    h("div", { class: "row" },
      wbutton("Add admin...", async () => {
        const v = await ask("Add admin", [
          { name: "username", label: "Username", hint: "3-100 letters, digits . _ @ -" },
          { name: "password", label: "Password", type: "password", hint: "At least 12 characters." },
          { name: "role", label: "Role", value: "admin", hint: "admin (changes settings), auditor (read-only) or searcher (finds documents of chosen projects only)." }], "Create");
        if (v) act(() => api("POST", "/admin/users", { username: v.username.trim(), password: v.password,
          role: (v.role || "admin").trim().toLowerCase() }), reload, "Account created.");
      }, "primary"),
      wbutton("Sign everyone out", () => {
        if (confirm("Sign out every admin, including you?"))
          act(() => api("POST", "/admin/users/revoke-all-sessions"), () => showLogin(), "All sessions revoked.");
      }, "danger")),
    table(["Username", "Role", "Status", "Failed logins", ""], list.map((u) => [
      u.username, badge(u.role === "auditor" ? "auditor (read-only)" : u.role === "searcher" ? "searcher: " + (u.projects.length ? u.projects.length + " " + plural(u.projects.length, "project", "projects") : "no projects") : "admin"), badge(u.status + (u.locked_until ? " until " + when(u.locked_until) : ""), tone[u.status]), u.failed_count,
      actions(
        u.status === "disabled"
          ? wbutton("Enable", () => act(() => api("POST", "/admin/users/" + u.id + "/enable"), reload, "Enabled."))
          : wbutton("Disable", () => { if (confirm("Disable " + u.username + "? They are signed out and cannot sign in."))
              act(() => api("POST", "/admin/users/" + u.id + "/disable"), reload, "Disabled."); }, "danger"),
        u.status === "locked" ? wbutton("Unlock", () => act(() => api("POST", "/admin/users/" + u.id + "/unlock"), reload, "Unlocked.")) : null,
        wbutton("Change role", async () => {
          const v = await ask("Role of " + u.username, [
            { name: "role", label: "Role", value: u.role, hint: "admin, auditor (read-only) or searcher. They are signed out." }], "Change");
          if (v) act(() => api("POST", "/admin/users/" + u.id + "/role", { role: v.role.trim().toLowerCase() }), reload, "Role changed.");
        }),
        u.role === "searcher" ? wbutton("Projects...", async () => {
          const all = await api("GET", "/admin/projects");
          const boxes = all.map((p) => h("input", { type: "checkbox", checked: u.projects.includes(p.id) }));
          const extra = h("div", { class: "checks" }, all.length ? all.map((p, i) => h("label", { class: "check" }, boxes[i], p.name))
            : h("p", { class: "muted" }, "There are no projects yet."));
          const v = await ask("Projects " + u.username + " may search", [], "Save", extra);
          if (v) act(() => api("PUT", "/admin/users/" + u.id + "/projects", { project_ids: all.filter((p, i) => boxes[i].checked).map((p) => p.id) }), reload, "Saved.");
        }) : null,
        wbutton("Reset password", async () => {
          const v = await ask("Reset password for " + u.username, [
            { name: "password", label: "New password", type: "password", hint: "At least 12 characters. They are signed out." }], "Reset");
          if (v) act(() => api("POST", "/admin/users/" + u.id + "/password", { password: v.password }), reload, "Password reset.");
        }),
        wbutton("Sign out", () => act(() => api("POST", "/admin/users/" + u.id + "/revoke-sessions"), reload, "Signed out.")))])));
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

// Alert notifications: where to send a message when something needs attention. The address is a secret (chat
// tools put a key in it): it is sent to the server once and only its host name is ever shown again.
async function webhookCard() {
  const w = await api("GET", "/admin/webhook");
  const url = h("input", { type: "text", maxlength: 2000, autocomplete: "off",
    placeholder: w.configured ? "Leave empty to keep the address already saved" : "Paste the address (it starts with https)" });
  const secret = h("input", { type: "password", maxlength: 200, autocomplete: "new-password",
    placeholder: w.has_secret ? "Leave empty to keep the secret already saved" : "Optional: used to sign every message" });
  const enabled = h("input", { type: "checkbox" });
  enabled.checked = w.enabled;
  const level = h("select", {}, [["info", "Notes, warnings and critical"], ["warn", "Warnings and critical"], ["critical", "Critical only"]]
    .map(([k, t]) => h("option", { value: k }, t)));
  level.value = w.min_level;
  const last = w.last_result;
  const status = !w.key_available
    ? h("p", { class: "error" }, "This server has no data key, so a webhook address cannot be stored. Ask whoever runs the server to set CENTRAL_DATA_KEY (see the deployment guide).")
    : w.configured
      ? h("p", {}, (w.enabled ? "Sending to " : "Switched off. Address on file: ") + w.host + ". ",
          last ? "The last message, " + ago(last.at) + ", " + (last.ok ? "was received." : "failed: " + last.error + ".") : "No message has been sent yet.")
      : h("p", { class: "muted" }, "No webhook yet. Paste the address your chat tool or ticket system gives you.");
  const reload = () => render();
  return h("div", { class: "card" },
    h("h2", {}, "Alert notifications"),
    h("p", { class: "muted" }, "When an alert opens, becomes critical, stays critical for 12 hours, or is resolved, the server sends a message to this address."
      + " Chat tools such as Slack and Teams show the text; other systems can read the JSON and check its signature."),
    status,
    h("div", { class: "settings-form" },
      h("label", {}, "Webhook address", url),
      h("label", {}, "Signing secret", secret),
      h("label", {}, "Send", level),
      h("label", { class: "inline" }, enabled, " Send alert messages")),
    h("div", { class: "row" },
      wbutton("Save", () => act(() => api("PUT", "/admin/webhook", {
        url: url.value.trim() || undefined, secret: secret.value.trim() || undefined,
        enabled: enabled.checked, min_level: level.value }), reload, "Saved."), "primary"),
      w.configured ? wbutton("Send a test message", async () => {
        try {
          const r = await api("POST", "/admin/webhook/test");
          toast(r.ok ? "The test message was received." : "The test message failed: " + r.error + ".", !r.ok);
          reload();
        } catch (e) { if (e.status !== 401) toast(e.message, true); }
      }) : null,
      w.has_secret ? wbutton("Remove the secret", () => act(() => api("PUT", "/admin/webhook",
        { secret: "", enabled: enabled.checked, min_level: level.value }), reload, "Secret removed.")) : null,
      w.configured ? wbutton("Remove the webhook", () => {
        if (confirm("Remove the webhook? Alert messages stop until you set one up again."))
          act(() => api("PUT", "/admin/webhook", { url: "", enabled: false, min_level: level.value }), reload, "Webhook removed.");
      }, "danger") : null));
}

async function settings() {
  const s = await api("GET", "/admin/settings");
  const input = h("input", { type: "number", min: 30, max: 86400, value: String(s.poll_interval_seconds) });
  return h("div", {},
    h("h1", {}, "Settings"),
    h("div", { class: "card" },
      h("label", {}, "How often PCs check for new configuration (seconds, 30 to 86400)", input),
      h("div", { class: "row" }, wbutton("Save", () => {
        const n = Number(input.value);
        if (!Number.isInteger(n)) { toast("Enter a whole number of seconds.", true); return; }
        act(() => api("PUT", "/admin/settings", { poll_interval_seconds: n }), null, "Saved.");
      }, "primary"))),
    await webhookCard());
}
