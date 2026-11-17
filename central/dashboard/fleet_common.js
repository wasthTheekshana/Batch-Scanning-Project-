// What the fleet and activity pages share: labels, status bar, small chart helpers.
"use strict";

// ------------------------------------------------------- fleet and activity
// Read-only views of what the PCs report: are they up, running the current settings, and
// how much they process. The charts are SVG built from attributes (the page's content
// policy allows no inline styles).
const count = (v) => Number(v || 0).toLocaleString();
const plural = (v, one, many) => (v === 1 ? one : many);
const SVGNS = "http://www.w3.org/2000/svg";

function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value !== null && value !== undefined) el.setAttribute(key, String(value));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const STATUS_TEXT = { online: "Online", offline: "Offline", key_revoked: "Access revoked", not_registered: "Not enrolled yet" };
const STATUS_KIND = { online: "ok", offline: "bad", key_revoked: "warn", not_registered: "warn" };
const STATUS_LEGEND = { online: "online", offline: "offline", key_revoked: "access revoked", not_registered: "not enrolled" };
const STATUS_ORDER = ["online", "offline", "key_revoked", "not_registered"];
const ROLLOUT_TEXT = { current: "Up to date", behind: "Behind", unknown: "Not reported", none: "No projects" };

function isNewer(a, b) {
  const x = String(a).split(/[.+-]/).map((p) => parseInt(p, 10) || 0);
  const y = String(b).split(/[.+-]/).map((p) => parseInt(p, 10) || 0);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] || 0) - (y[i] || 0);
    if (d) return d > 0;
  }
  return false;
}

function needsAttention(p) {
  return p.status === "offline" || p.status === "key_revoked" || p.rollout === "behind"
    || !!(p.today && p.today.errors > 0);
}

function fleetSentence(s) {
  const enrolled = s.online + s.offline + s.key_revoked;
  if (!s.total) return ["No stations yet. Add a PC under ", h("a", { href: "#/pcs" }, "PCs"),
    ", then type its code into DOK Scanner (Admin, Central)."];
  if (!enrolled) return ["No station has connected yet. They appear here after their first sync."];
  const out = [h("strong", {}, s.online + " of " + enrolled), " " + plural(enrolled, "station is", "stations are") + " online."];
  if (s.offline) out.push(" ", h("strong", {}, String(s.offline)), " " + plural(s.offline, "has", "have") + " stopped checking in.");
  if (s.behind) out.push(" ", h("strong", {}, String(s.behind)), " " + plural(s.behind, "is", "are") + " behind on settings.");
  if (!s.offline && !s.behind) out.push(" Every project is up to date.");
  return out;
}

function todayLine(t) {
  if (!t.documents && !t.pages && !t.batches) return "Nothing processed yet today.";
  return "Today: " + count(t.documents) + " " + plural(t.documents, "document", "documents") + ", "
    + count(t.pages) + " " + plural(t.pages, "page", "pages") + " in "
    + count(t.batches) + " " + plural(t.batches, "batch", "batches")
    + (t.errors ? ", " + count(t.errors) + " " + plural(t.errors, "error", "errors") : "") + ".";
}

function statusBar(s) {
  let x = 0;
  const parts = STATUS_ORDER.filter((k) => s[k] > 0).map((k) => {
    const width = (s[k] / s.total) * 100;
    const rect = svg("rect", { class: k, x, y: 0, width, height: 1 });
    x += width;
    return rect;
  });
  return svg("svg", { class: "statusbar", viewBox: "0 0 100 1", preserveAspectRatio: "none", role: "img",
    "aria-label": STATUS_ORDER.map((k) => s[k] + " " + STATUS_LEGEND[k]).join(", ") }, parts);
}

// The sentence, the status bar and its legend: the top of the Overview and of the Fleet.
function fleetHeader(s) {
  return [
    h("p", { class: "lead" }, fleetSentence(s)),
    s.total ? statusBar(s) : null,
    s.total ? h("div", { class: "legend" }, STATUS_ORDER.filter((k) => s[k] > 0).map((k) =>
      h("span", {}, h("i", { class: k }), s[k] + " " + STATUS_LEGEND[k]))) : null,
    h("p", { class: "sub" }, todayLine(s.today)),
  ];
}

const SEVERITY_TEXT = { critical: "Critical", warn: "Warning", info: "Note" };
const SEVERITY_KIND = { critical: "bad", warn: "warn", info: "" };

// The nav badge: how many things need attention, without opening the Overview.
async function updateAlertBadge() {
  if (role === "searcher") return;                  // alerts are not theirs to see
  try {
    const a = await api("GET", "/admin/alerts");
    const urgent = a.counts.critical + a.counts.warn;
    const el = document.getElementById("alert-count");
    el.textContent = String(urgent);
    el.className = "count" + (a.counts.critical ? " critical" : "");
    el.title = urgent + " " + plural(urgent, "thing needs", "things need") + " attention";
    el.hidden = urgent === 0;
  } catch (e) { /* the badge is a convenience: the Overview shows the real list */ }
}

// On a narrow screen a table row becomes a card: each cell is labelled with its column.
function stack(tbl, headers) {
  if (!(tbl instanceof Element) || tbl.tagName !== "TABLE") return tbl;
  tbl.classList.add("stack");
  for (const tr of tbl.querySelectorAll("tbody tr")) {
    [...tr.children].forEach((td, i) => { if (headers[i]) td.dataset.label = headers[i]; });
  }
  return tbl;
}
