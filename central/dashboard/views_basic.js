// Overview and PCs.
"use strict";

// ------------------------------------------------------------------ views
async function overview() {
  const [o, fl, al] = await Promise.all([api("GET", "/admin/overview"), api("GET", "/admin/fleet"), api("GET", "/admin/alerts")]);
  const minutes = Math.round(o.poll_interval_seconds / 60);
  return h("div", {},
    h("h1", {}, "Overview"),
    o.server && !o.server.data_key && fl.summary.total
      ? h("p", { class: "banner", role: "alert" }, "This server has no data key, so it refuses the documents stations report "
          + "(they stay queued on the PCs). Set CENTRAL_DATA_KEY and restart the server; see the deployment guide.")
      : null,
    fleetHeader(fl.summary),
    h("p", { class: "sub" },
      "The server holds " + o.projects + " " + plural(o.projects, "project", "projects") + " and "
      + o.templates + " " + plural(o.templates, "template", "templates") + ", has "
      + o.admins.active + " active " + plural(o.admins.active, "administrator", "administrators")
      + ", and asks stations to check in every "
      + (minutes >= 1 ? minutes + " " + plural(minutes, "minute", "minutes") : o.poll_interval_seconds + " seconds") + "."
      + (o.server && o.server.documents ? " It holds the index data of " + count(o.server.documents) + " "
        + plural(o.server.documents, "document", "documents") + "." : "")),
    h("h2", {}, "Needs a look"),
    al.alerts.length
      ? stack(table(["Level", "Station", "What", "Since"], al.alerts.map((x) => [
          badge(SEVERITY_TEXT[x.severity], SEVERITY_KIND[x.severity]),
          h("a", { href: "#/fleet/" + encodeURIComponent(x.pc_id) }, x.pc_name), x.message, x.since ? ago(x.since) : "today"]), ""),
        ["Level", "Station", "What", "Since"])
      : h("p", { class: "empty" }, fl.summary.total ? "Nothing right now: every station is online and up to date." : "Add a PC to start monitoring."),
    h("h2", {}, "Recent activity"),
    stack(auditTable(o.recent_audit), ["Time", "Who", "Action", "Details"]),
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
      wbutton("Add PC", () => {
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
        wbutton("Rename", async () => {
          const v = await ask("Rename PC", [{ name: "name", label: "New name", value: p.display_name }], "Rename");
          if (v) act(() => api("PATCH", "/admin/pcs/" + p.id, { display_name: v.name.trim() }), reload, "Renamed.");
        }),
        wbutton("New enrollment code", () => act(
          () => api("POST", "/admin/pcs/" + p.id + "/enrollment-code"),
          async (r) => { await secret(r, "New code for " + p.display_name); reload(); })),
        wbutton("Revoke key", () => {
          if (confirm("Revoke the access key of " + p.display_name + "? It stops receiving configuration until it is enrolled again."))
            act(() => api("DELETE", "/admin/pcs/" + p.id + "/key"), reload, "Key revoked.");
        }, "danger"),
        wbutton("Delete", () => {
          if (confirm("Delete " + p.display_name + " and its assignments? This cannot be undone."))
            act(() => api("DELETE", "/admin/pcs/" + p.id), reload, "PC deleted.");
        }, "danger"))]),
      "No PCs registered yet."));
}
