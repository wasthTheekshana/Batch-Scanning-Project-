// The activity charts.
"use strict";

// ---- activity
let activityDays = 14;
let activityMetric = "documents";
const METRICS = [["documents", "Documents"], ["pages", "Pages"], ["batches", "Batches"], ["errors", "Errors"]];

function niceMax(v) {
  const magnitude = Math.pow(10, Math.floor(Math.log10(v)));
  for (const step of [1, 2, 5, 10]) if (step * magnitude >= v) return step * magnitude;
  return 10 * magnitude;
}

function barChart(days, metric, label) {
  const W = 720, H = 250, L = 46, R = 12, T = 14, B = 30;
  const max = niceMax(Math.max(1, ...days.map((d) => d[metric])));
  const iw = W - L - R, ih = H - T - B, slot = iw / days.length, bw = Math.max(2, slot * 0.68);
  const chart = svg("svg", { class: "chart", viewBox: "0 0 " + W + " " + H, role: "img",
    "aria-label": label + " per day, last " + days.length + " days" });
  for (const fraction of [0, 0.5, 1]) {
    const y = T + ih - ih * fraction;
    chart.append(svg("line", { class: "grid", x1: L, x2: W - R, y1: y, y2: y }),
      svg("text", { class: "end", x: L - 6, y: y + 4 }, count(max * fraction)));
  }
  days.forEach((d, i) => {
    const v = d[metric];
    const height = v ? Math.max(1.5, (ih * v) / max) : 1.5;
    const cls = "bar" + (metric === "errors" ? " errors" : "") + (v ? "" : " zero");
    chart.append(svg("rect", { class: cls, x: L + i * slot + (slot - bw) / 2, y: T + ih - height,
      width: bw, height, rx: 1.5 }, svg("title", {}, d.day + ": " + count(v) + " " + label.toLowerCase())));
  });
  for (const i of new Set([0, Math.floor((days.length - 1) / 2), days.length - 1])) {
    chart.append(svg("text", { class: "mid", x: L + i * slot + slot / 2, y: H - 8 }, days[i].day.slice(5)));
  }
  return chart;
}

function alignRight(tbl, columns) {
  if (!(tbl instanceof Element) || tbl.tagName !== "TABLE") return tbl;
  for (const tr of tbl.querySelectorAll("tr")) {
    for (const i of columns) if (tr.children[i]) tr.children[i].classList.add("num");
  }
  return tbl;
}

async function activity() {
  const a = await api("GET", "/admin/stats?days=" + activityDays);
  const t = a.totals;
  const label = METRICS.find(([k]) => k === activityMetric)[1];
  const range = h("div", { class: "seg", role: "group", "aria-label": "Period" }, [7, 14, 30, 90].map((d) =>
    h("button", { type: "button", class: d === activityDays ? "on" : "", onclick: () => { activityDays = d; render(); } }, d + " days")));
  const metric = h("div", { class: "seg", role: "group", "aria-label": "Measure" }, METRICS.map(([k, text]) =>
    h("button", { type: "button", class: k === activityMetric ? "on" : "", onclick: () => { activityMetric = k; render(); } }, text)));
  const busiest = Math.max(1, ...a.per_pc.map((p) => p.documents));
  const empty = !t.documents && !t.pages && !t.batches && !t.errors;
  return h("div", {},
    h("h1", {}, "Activity"),
    h("p", { class: "lead" }, empty
      ? "No activity reported in the last " + activityDays + " days."
      : ["In the last " + activityDays + " days the stations processed ", h("strong", {}, count(t.documents) + " " + plural(t.documents, "document", "documents")),
          " (" + count(t.pages) + " pages in " + count(t.batches) + " batches)" + (t.errors ? "; " + count(t.errors) + " had errors." : ".")]),
    empty ? h("p", { class: "sub" }, "Stations report their totals with each sync, from program version 1.0.0. Nothing appears until a station has synced and scanned something.") : null,
    h("div", { class: "toolbar" }, range, metric),
    barChart(a.days, activityMetric, label),
    h("h2", {}, "By station"),
    stack(alignRight(table(["Station", "Documents", "Pages", "Batches", "Errors", ""], a.per_pc.map((p) => [
      p.display_name, count(p.documents), count(p.pages), count(p.batches),
      p.errors ? h("span", { class: "err" }, count(p.errors)) : "0",
      svg("svg", { class: "rank", viewBox: "0 0 100 10", preserveAspectRatio: "none", role: "img",
        "aria-label": count(p.documents) + " documents" },
        svg("rect", { class: "track", x: 0, y: 0, width: 100, height: 10, rx: 3 }),
        svg("rect", { class: "fill", x: 0, y: 0, width: Math.max(p.documents ? 2 : 0, (p.documents / busiest) * 100), height: 10, rx: 3 }))]),
      "No station has reported activity in this period."), [1, 2, 3, 4]), ["Station", "Documents", "Pages", "Batches", "Errors", ""]));
}
