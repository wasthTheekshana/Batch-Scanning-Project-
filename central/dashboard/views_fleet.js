// The fleet and one station's page.
"use strict";

let fleetFilter = "all";
let fleetSearch = "";

// ---- one station
let stationMetric = "documents";
const EVENT_TEXT = {
  pc_created: "Added", pc_registered: "Enrolled", enrollment_code_issued: "New enrolment code issued",
  pc_key_revoked: "Access revoked", pc_renamed: "Renamed", project_pushed: "Project assigned",
  project_unassigned: "Project unassigned", pc_deleted: "Deleted",
};

function stationSentence(pc) {
  const seen = pc.last_seen_at ? "It last checked in " + ago(pc.last_seen_at) + "." : "It has never checked in.";
  const program = pc.app_version ? " It runs program " + pc.app_version + "." : " It has not reported its program version.";
  const lead = {
    online: " is online.", offline: " has stopped checking in.",
    key_revoked: ": its access was revoked, so it no longer receives settings.",
    not_registered: " has not been enrolled yet: type its code into DOK Scanner (Admin, Central).",
  }[pc.status];
  return [h("strong", {}, pc.display_name), lead + " " + (pc.status === "not_registered" ? "" : seen + program)];
}

async function stationDetail(id) {
  const d = await api("GET", "/admin/fleet/" + encodeURIComponent(id));
  const metricLabel = METRICS.find(([k]) => k === stationMetric)[1];
  const metric = h("div", { class: "seg", role: "group", "aria-label": "Measure" }, METRICS.map(([k, text]) =>
    h("button", { type: "button", class: k === stationMetric ? "on" : "", onclick: () => { stationMetric = k; render(); } }, text)));
  const projects = d.projects.length
    ? (() => {
        const headers = ["Project", "Assigned", "The station runs", "Where it stands"];
        return h("div", { class: "scroll" }, stack(table(headers, d.projects.map((p) => [
          h("div", {}, h("div", { class: "station" }, p.name),
            h("ul", { class: "projects" }, p.history.map((v) => h("li", {},
              "v" + v.version + ", published " + ago(v.published_at) + (v.published_by ? " by " + v.published_by : ""))))),
          "v" + p.assigned_version + (p.published_at ? ", published " + ago(p.published_at) : ""),
          p.applied_version === null ? "nothing yet" : "v" + p.applied_version,
          h("span", { class: "pill " + ({ current: "current", behind: "behind", not_applied: "behind", unknown: "unknown" }[p.state]) },
            { current: "Up to date", behind: "Behind", not_applied: "Not applied yet", unknown: "Not reported" }[p.state])])), headers));
      })()
    : h("p", { class: "empty" }, "No project is assigned to this station.");
  const alerts = d.alerts.length
    ? stack(table(["Level", "What", "Since"], d.alerts.map((x) => [
        badge(SEVERITY_TEXT[x.severity], SEVERITY_KIND[x.severity]), x.message, x.since ? ago(x.since) : "today"]), ""),
      ["Level", "What", "Since"])
    : h("p", { class: "empty" }, "Nothing needs attention for this station.");
  const t = d.totals;
  return h("div", {},
    h("p", {}, h("a", { href: "#/fleet" }, "Back to the fleet")),
    h("h1", {}, d.pc.display_name),
    h("p", { class: "lead" }, stationSentence(d.pc)),
    d.today ? h("p", { class: "sub" }, todayLine(d.today)) : h("p", { class: "sub" }, "Nothing reported yet today."),
    queueText(d.queue) ? h("p", { class: "err" }, "Documents: " + queueText(d.queue) + ".") : null,
    h("h2", {}, "Needs a look"), alerts,
    h("h2", {}, "Settings"), projects,
    h("h2", {}, "Activity in the last 30 days"),
    h("p", { class: "sub" }, count(t.documents) + " " + plural(t.documents, "document", "documents") + ", " + count(t.pages)
      + " pages in " + count(t.batches) + " batches" + (t.errors ? "; " + count(t.errors) + " had errors." : ".")),
    h("div", { class: "toolbar" }, metric),
    barChart(d.days, stationMetric, metricLabel),
    h("h2", {}, "Documents sent to the server"),
    d.documents.held
      ? h("div", {},
          h("p", { class: "sub" }, count(d.documents.held) + " " + plural(d.documents.held, "document", "documents")
            + " reported by this station; the newest:"),
          stack(table(["Scanned", "Batch", "Pages", "Status", ""], d.documents.recent.map((x) => [
            when(x.created_at), x.batch_id, x.page_count, x.validation_status ? x.validation_status.replace(/_/g, " ") : "",
            h("a", { href: "#/documents/" + encodeURIComponent(x.id) }, "Open")]), ""),
            ["Scanned", "Batch", "Pages", "Status", ""]))
      : h("p", { class: "empty" }, "This station has not reported any documents yet."),
    h("h2", {}, "Recent events"),
    d.events.length
      ? stack(table(["Time", "Event", "Details"], d.events.map((e) => [when(e.at), EVENT_TEXT[e.action] || e.action,
          h("span", { class: "muted" }, detailText(e.detail))]), ""), ["Time", "Event", "Details"])
      : h("p", { class: "empty" }, "No events yet."),
    h("p", { class: "sub" }, "To rename this station, issue a new enrolment code, revoke its access or delete it, use ",
      h("a", { href: "#/pcs" }, "PCs"), "."));
}

async function fleet(id) {
  if (id) return stationDetail(id);
  const f = await api("GET", "/admin/fleet");
  const s = f.summary;
  const latest = f.pcs.map((p) => p.app_version).filter(Boolean).reduce((a, b) => (a && !isNewer(b, a) ? a : b), null);
  const holder = h("div", { class: "scroll" });

  function projectsList(p) {
    if (!p.projects.length) return null;
    return h("ul", { class: "projects" }, p.projects.map((q) => h("li", { class: q.state },
      q.name + " v" + q.assigned_version +
      (q.state === "behind" ? " (the PC has v" + q.applied_version + ")" : "") +
      (q.state === "not_applied" ? " (not applied yet)" : ""))));
  }

  const offline = (p) => p.status === "key_revoked" || p.status === "not_registered";

  function row(p) {
    const older = p.app_version && latest && isNewer(latest, p.app_version);
    return [
      h("div", {}, h("div", { class: "station" }, h("a", { href: "#/fleet/" + encodeURIComponent(p.id) }, p.display_name)),
        h("div", { class: "sub" }, p.last_seen_at ? "Last seen " + ago(p.last_seen_at) : "Never seen"),
        queueText(p.documents) ? h("div", { class: "err" }, queueText(p.documents)) : null),
      badge(STATUS_TEXT[p.status], STATUS_KIND[p.status]),
      p.app_version
        ? h("span", {}, h("span", { class: "mono" }, p.app_version), older ? " " : null, older ? badge("older", "warn") : null)
        : h("span", { class: "muted" }, "Not reported"),
      h("div", {}, offline(p) ? h("span", { class: "pill none" }, "Not receiving settings")
        : h("span", { class: "pill " + p.rollout }, ROLLOUT_TEXT[p.rollout]), projectsList(p)),
      p.today
        ? h("div", {}, count(p.today.documents) + " docs, " + count(p.today.pages) + " pages",
            p.today.errors ? h("div", { class: "err" }, count(p.today.errors) + " " + plural(p.today.errors, "error", "errors")) : null)
        : h("span", { class: "muted" }, "-"),
    ];
  }

  function show() {
    const term = fleetSearch.trim().toLowerCase();
    const rows = f.pcs.filter((p) => (fleetFilter === "all" || needsAttention(p))
      && (!term || p.display_name.toLowerCase().includes(term)))
      .sort((a, b) => Number(needsAttention(b)) - Number(needsAttention(a)));   // stable: names stay in order
    clear(holder);
    if (!rows.length) {
      holder.append(h("p", { class: "empty" }, !f.pcs.length ? "No stations yet."
        : fleetFilter === "attention" && !term ? "Nothing needs attention: every station is online and up to date."
        : "No station matches."));
      return;
    }
    const headers = ["Station", "Status", "Program", "Settings", "Today"];
    holder.append(stack(table(headers, rows.map(row)), headers));
  }

  const attention = f.pcs.filter(needsAttention).length;
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Show" });
  const choice = (key, label) => {
    const b = h("button", { type: "button", class: fleetFilter === key ? "on" : "", onclick: () => {
      fleetFilter = key;
      for (const other of seg.children) other.classList.toggle("on", other === b);
      show();
    } }, label);
    return b;
  };
  seg.append(choice("all", "All " + f.pcs.length), choice("attention", "Needs attention " + attention));
  const search = h("input", { type: "search", placeholder: "Find a station", value: fleetSearch, maxlength: 100,
    "aria-label": "Find a station" });
  search.addEventListener("input", () => { fleetSearch = search.value; show(); });
  show();
  return h("div", {},
    h("h1", {}, "Fleet"),
    fleetHeader(s),
    h("div", { class: "toolbar" }, seg, search),
    holder);
}
fleet.refresh = 30000;
overview.refresh = 60000;
