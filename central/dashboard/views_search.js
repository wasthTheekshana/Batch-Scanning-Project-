// Documents: find one by an identifier, open it, and the access log.
"use strict";

// ---- documents: find one by an identifier, open it
const searchState = { q: "", project: "", pc: "", status: "", date_from: "", date_to: "", offset: 0 };
const SEARCH_PAGE = 25;
const REVISION_SOURCE = { pc: "reported by the station", central: "corrected here" };

// What a station says about documents it has not yet been able to send here (null when nothing to say).
function queueText(q) {
  if (!q || (!q.waiting && !q.refused)) return null;
  const parts = [];
  if (q.waiting) parts.push(count(q.waiting) + " " + plural(q.waiting, "document", "documents") + " waiting to be sent"
    + (q.oldest_at ? " (the oldest " + ago(q.oldest_at) + ")" : ""));
  if (q.refused) parts.push(count(q.refused) + " refused by the server");
  return parts.join("; ");
}

function bytesText(n) {
  if (n < 1024) return n + " bytes";
  if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}

function fieldsPreview(fields) {
  if (fields === null) return h("span", { class: "muted" }, "cannot be read with the server's current data key");
  const entries = Object.entries(fields).filter(([, v]) => v);
  if (!entries.length) return h("span", { class: "muted" }, "no index fields");
  return h("ul", { class: "projects" }, entries.slice(0, 4).map(([k, v]) => h("li", {}, k + ": " + v)));
}

// The same filters as a CSV file. The session cookie authorises the request; the file is built in the browser.
async function downloadCsv(query) {
  const q = new URLSearchParams(query);
  q.delete("limit"); q.delete("offset");
  try {
    const response = await fetch("/admin/documents/export.csv?" + q, { credentials: "same-origin" });
    if (!response.ok) throw new Error("The export failed (" + response.status + ").");
    const blob = await response.blob();
    const link = h("a", { href: URL.createObjectURL(blob), download: "documents.csv" });
    document.body.append(link);
    link.click();
    link.remove();
    const rows = Number(response.headers.get("X-Rows")), matched = Number(response.headers.get("X-Matched"));
    toast(matched > rows ? "Downloaded the first " + count(rows) + " of " + count(matched) + ". Narrow the search for the rest."
      : "Downloaded " + count(rows) + " " + plural(rows, "document", "documents") + ".", matched > rows);
  } catch (e) { toast(e.message, true); }
}

async function search() {
  const f = await api("GET", "/admin/documents/filters");
  const s = searchState;
  const pick = (label, key, options, any) => {
    const el = h("select", {}, h("option", { value: "" }, any),
      options.map(([value, text]) => h("option", { value, selected: s[key] === value }, text)));
    el.addEventListener("change", () => { s[key] = el.value; s.offset = 0; });
    return h("label", {}, label, el);
  };
  const typed = (label, key, type, placeholder) => {
    const el = h("input", { type, value: s[key], placeholder, maxlength: 100 });
    el.addEventListener("input", () => { s[key] = el.value; s.offset = 0; });
    return h("label", {}, label, el);
  };
  const go = (event) => { if (event) event.preventDefault(); render(); };
  const form = h("form", { class: "searchform", onsubmit: go },
    typed("Identifier", "q", "search", "NIC, account number, customer ID..."),
    pick("Project", "project", f.projects.map((p) => [p.id, p.name]), "All projects"),
    pick("Station", "pc", f.stations.map((p) => [p.id, p.name]), "All stations"),
    pick("Status", "status", f.statuses.map((v) => [v, v]), "Any status"),
    typed("Scanned from", "date_from", "date"), typed("Scanned to", "date_to", "date"),
    h("div", { class: "buttons" },
      h("button", { type: "submit", class: "primary" }, "Search"),
      button("Clear", () => { Object.assign(s, { q: "", project: "", pc: "", status: "", date_from: "", date_to: "", offset: 0 }); render(); })));
  const query = new URLSearchParams({ limit: String(SEARCH_PAGE), offset: String(s.offset) });
  for (const key of ["q", "project", "pc", "status", "date_from", "date_to"]) if (s[key].trim()) query.set(key, s[key].trim());
  let found = null;
  let problem = null;
  try { found = await api("GET", "/admin/documents?" + query); }
  catch (e) { if (e.status === 401) throw e; problem = e.message; }
  const headers = ["Scanned", "Project", "Station", "Index", "Status", ""];
  const results = problem
    ? h("p", { class: "error" }, problem)
    : !found.total
      ? h("p", { class: "empty" }, s.q || s.project || s.pc || s.status || s.date_from || s.date_to
          ? "No document matches. An identifier must match exactly (spaces and dashes are ignored)."
          : "No documents have been reported yet. Stations report each document's index fields once they are enrolled.")
      : h("div", {},
          h("div", { class: "toolbar" },
            h("p", { class: "sub" }, count(found.total) + " " + plural(found.total, "document", "documents") + " found"
              + (found.total > SEARCH_PAGE ? ", showing " + (found.offset + 1) + " to " + (found.offset + found.items.length) : "") + "."),
            button("Download as a spreadsheet", () => downloadCsv(query))),
          h("div", { class: "scroll" }, stack(table(headers, found.items.map((d) => [
            when(d.created_at), d.project_name, d.pc_name, fieldsPreview(d.fields),
            d.validation_status ? badge(d.validation_status.replace(/_/g, " ")) : "",
            h("a", { href: "#/documents/" + encodeURIComponent(d.id) }, "Open")]), ""), headers)),
          found.total > SEARCH_PAGE
            ? h("div", { class: "toolbar" },
                button("Previous", () => { s.offset = Math.max(0, s.offset - SEARCH_PAGE); render(); }, ""),
                button("Next", () => { s.offset += SEARCH_PAGE; render(); }, ""))
            : null);
  if (found && found.total && s.offset >= found.total) { s.offset = 0; }
  return h("div", {},
    h("h1", {}, "Search documents"),
    h("p", { class: "sub" }, "Index data and PDF details reported by the stations. The PDF files themselves stay on the stations."),
    form, results,
    role === "searcher" ? null : h("p", { class: "sub" }, h("a", { href: "#/access" }, "Who searched for or opened documents")));
}

async function documentDetail(id) {
  const d = await api("GET", "/admin/documents/" + encodeURIComponent(id));
  const file = d.files.find((x) => x.is_current) || d.files[0];
  const entries = Object.entries(d.fields || {});
  return h("div", {},
    h("p", {}, h("a", { href: "#/search" }, "Back to the search")),
    h("h1", {}, d.project_name),
    h("p", { class: "lead" }, "Scanned on ", h("strong", {}, d.pc_name), " " + when(d.created_at) + ", "
      + d.page_count + " " + plural(d.page_count, "page", "pages") + ", batch " + d.batch_id + "."),
    d.validation_status ? h("p", {}, badge(d.validation_status.replace(/_/g, " "))) : null,
    h("h2", {}, "Index fields"),
    d.fields_unreadable
      ? h("p", { class: "error" }, "These fields cannot be read with the server's current data key.")
      : entries.length
        ? stack(table(["Field", "Value"], entries.map(([k, v]) => [k, v]), ""), ["Field", "Value"])
        : h("p", { class: "empty" }, "No index fields were reported."),
    h("h2", {}, "The PDF"),
    file
      ? h("div", {},
          h("p", {}, h("strong", {}, file.file_name || "(no name)"), " " + bytesText(file.size) + ". "
            + (file.delivery_status ? "Delivery: " + file.delivery_status.replace(/_/g, " ") + ". " : "")
            + (file.delivered_to.length ? "Where: " + file.delivered_to.join(", ") + "." : "")),
          h("p", { class: "sub" }, "The file stays on " + d.pc_name + "; this server holds its details only. SHA-256 "
            + file.sha256 + (d.files.length > 1 ? ". The PDF was re-exported " + (d.files.length - 1) + " "
              + plural(d.files.length - 1, "time", "times") + " after a correction." : ".")))
      : h("p", { class: "empty" }, "No PDF was reported for this document."),
    h("h2", {}, "History"),
    h("ul", { class: "history" }, d.history.map((r) => h("li", {},
      h("strong", {}, "Revision " + r.revision), " " + when(r.received_at) + ", " + (REVISION_SOURCE[r.source] || r.source)
        + (r.is_current ? " (current)" : ""),
      r.changes && r.changes.length
        ? h("ul", { class: "projects" }, r.changes.map((c) => h("li", {},
            c.field + ": " + (c.before === null || c.before === undefined ? "(empty)" : c.before)
            + " -> " + (c.after === null || c.after === undefined ? "(empty)" : c.after))))
        : null))),
    h("p", { class: "sub" }, "Opening this document was recorded in the access log."),
    wbutton("Delete this document...", async () => {
      const v = await ask("Delete this document from the server?", [
        { name: "reason", label: "Reason", hint: "Required, at least 5 characters. It is kept in the audit log.", max: 300 }], "Delete",
        h("p", {}, "Its index fields and PDF details are removed from the server for good, including every earlier revision. "
          + "The PDF itself is not kept here, and the station's own copy is not touched. The station will not send it again."));
      if (v) act(() => api("POST", "/admin/documents/" + encodeURIComponent(id) + "/delete", { reason: v.reason.trim() }),
        () => { location.hash = "#/search"; }, "Document deleted.");
    }, "danger"));
}

async function accessLog() {
  const r = await api("GET", "/admin/documents/access-log?limit=100");
  return h("div", {},
    h("h1", {}, "Document access"),
    h("p", { class: "sub" }, "Who searched for or opened documents. The log records the kind of search, never what was typed."),
    stack(table(["Time", "Who", "Action", "Details"], r.items.map((e) => [when(e.at), e.user, e.action,
      h("span", { class: "muted" }, e.document_id ? short(e.document_id) : detailText(e.detail))]), "Nobody has searched yet."),
      ["Time", "Who", "Action", "Details"]));
}
