// DOK Scanner central admin dashboard.
// Every value from the API is placed into the page with text nodes only
// (see h()), never as markup, and the session token lives in memory only.
(() => {
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
    return role === "auditor" ? null : button(label, onclick, kind);
  }

  const badge = (text, kind) => h("span", { class: "badge " + (kind || "") }, text);
  const short = (id) => String(id).slice(0, 8);

  // ------------------------------------------------------------------ views
  async function overview() {
    const [o, fl] = await Promise.all([api("GET", "/admin/overview"), api("GET", "/admin/fleet")]);
    const minutes = Math.round(o.poll_interval_seconds / 60);
    const looks = fl.pcs.filter(needsAttention);
    return h("div", {},
      h("h1", {}, "Overview"),
      fleetHeader(fl.summary),
      h("p", { class: "sub" },
        "The server holds " + o.projects + " " + plural(o.projects, "project", "projects") + " and "
        + o.templates + " " + plural(o.templates, "template", "templates") + ", has "
        + o.admins.active + " active " + plural(o.admins.active, "administrator", "administrators")
        + ", and asks stations to check in every "
        + (minutes >= 1 ? minutes + " " + plural(minutes, "minute", "minutes") : o.poll_interval_seconds + " seconds") + "."),
      h("h2", {}, "Needs a look"),
      looks.length
        ? stack(table(["Station", "Why"], looks.map((p) => [
            h("a", { href: "#/fleet" }, p.display_name),
            h("ul", { class: "projects why" }, reasons(p).map((r) => h("li", {}, r)))]),
          ""), ["Station", "Why"])
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

  // Projects and templates share one list + detail layout.
  function configKind(kind) {
    const plural = kind + "s";
    const base = "/admin/" + plural;

    async function list() {
      const rows = await api("GET", base);
      return h("div", {},
        h("h1", {}, kind === "project" ? "Projects" : "Templates"),
        h("div", { class: "row" }, wbutton("New " + kind + "...", () => editor(null), "primary")),
        table(["Name", kind === "template" ? "Category" : "ID", "Version", ""].filter(Boolean),
          rows.map((r) => [r.name, kind === "template" ? (r.category || "") + (r.built_in ? " (built in)" : "") : short(r.id),
            "v" + r.version, actions(button("Open", () => { location.hash = "#/" + plural + "/" + r.id; }))])));
    }

    // A project is edited as a form (processing settings only), with the raw
    // JSON one tab away for anything the form does not cover.
    function projectDialog(name, config) {
      return new Promise((resolve) => {
        const F = window.DokForm;
        let current = config;
        let tab = "form";
        const nameInput = h("input", { type: "text", value: name, maxlength: 200, autocomplete: "off" });
        const error = h("p", { class: "error", role: "alert" });
        const formBox = h("div", { class: "settings-form" });
        const jsonBox = h("textarea", { rows: 16, maxlength: 300000 });
        jsonBox.hidden = true;
        const tabForm = h("button", { type: "button", class: "primary" }, "Form");
        const tabJson = h("button", { type: "button" }, "JSON (advanced)");
        let read = () => ({});

        const group = (title, ...items) => h("fieldset", {}, h("legend", {}, title), items);
        const labelled = (text, input, hint) =>
          h("label", {}, text, input, hint ? h("span", { class: "muted" }, hint) : null);
        const check = (text, on) => {
          const box = h("input", { type: "checkbox" });
          box.checked = !!on;
          return { box, el: h("label", { class: "inline" }, box, " ", text) };
        };
        const select = (options, value) => {
          const el = h("select", {}, options.map(([k, t]) => h("option", { value: k }, t)));
          el.value = value;
          return el;
        };

        function buildForm() {
          clear(formBox);
          const f = F.toForm(current);
          const lang = h("input", { type: "text", value: f.ocr_lang, maxlength: 40 });
          const mode = select(F.OCR_MODES, f.ocr_mode);
          const conf = h("input", { type: "number", value: f.ocr_confidence, step: "0.05", min: "0", max: "1" });
          const qcOn = check("Quality checks on", f.qc_enabled);
          const num = (v, step) => h("input", { type: "number", value: v, step: step || "1", min: "0" });
          const pass = num(f.qc_pass_score), bw = num(f.qc_blur_warn), bf = num(f.qc_blur_fail);
          const dw = num(f.qc_dpi_warn), df = num(f.qc_dpi_fail);
          const strat = F.STRATEGIES.map(([k, t]) => [k, check(t, f.strategies.includes(k))]);
          const pages = num(f.fixed_pages);
          const brole = select(F.BARCODE_ROLES, f.barcode_role);
          const bpat = h("input", { type: "text", value: f.barcode_pattern, maxlength: 200 });
          const dropBlank = check("Discard empty pages (the blank back of a one-sided sheet)", f.drop_blank_pages);
          const autoIdx = check("Read the index fields automatically", f.auto_index);
          const dup = check("Check for duplicate documents", f.duplicate_check);
          const rows = [];
          const fieldsBox = h("div", {});
          function addRow(row) {
            const r = { i: row.i, name: h("input", { type: "text", value: row.name, maxlength: 100, placeholder: "field name" }),
                        regex: h("input", { type: "text", value: row.regex, maxlength: 500, placeholder: "pattern (optional)" }) };
            const line = h("div", { class: "field-row" }, r.name, r.regex,
              button("Remove", () => { rows.splice(rows.indexOf(r), 1); line.remove(); }, "danger"));
            rows.push(r);
            fieldsBox.append(line);
          }
          f.fields.forEach(addRow);
          formBox.append(
            group("Reading the page",
              labelled("OCR language", lang, "e.g. eng"), labelled("Index fields are read by", mode),
              labelled("Accept a read value from this confidence (0 to 1)", conf)),
            group("Quality checks", qcOn.el,
              labelled("Pass score (0 to 100)", pass), labelled("Blur warning below", bw),
              labelled("Blur failure below", bf), labelled("DPI warning below", dw), labelled("DPI failure below", df)),
            group("Splitting a stack into documents",
              strat.map(([, c]) => c.el), labelled("Pages per document (for a fixed number)", pages),
              labelled("What a barcode means", brole),
              labelled("Only barcodes matching this pattern", bpat, "optional regular expression"),
              dropBlank.el),
            group("Features", autoIdx.el, dup.el),
            group("Index fields",
              h("p", { class: "muted" }, f.document_types
                ? f.document_types + " document type(s) are kept as they are (edit them in the JSON tab)."
                : "No document types."),
              fieldsBox, button("Add a field", () => addRow({ i: -1, name: "", regex: "" }))));
          read = () => ({
            ocr_lang: lang.value, ocr_mode: mode.value, ocr_confidence: conf.value,
            qc_enabled: qcOn.box.checked, qc_pass_score: pass.value, qc_blur_warn: bw.value,
            qc_blur_fail: bf.value, qc_dpi_warn: dw.value, qc_dpi_fail: df.value,
            strategies: strat.filter(([, c]) => c.box.checked).map(([k]) => k),
            fixed_pages: pages.value, barcode_role: brole.value, barcode_pattern: bpat.value,
            drop_blank_pages: dropBlank.box.checked, auto_index: autoIdx.box.checked,
            duplicate_check: dup.box.checked,
            fields: rows.map((r) => ({ i: r.i, name: r.name.value, regex: r.regex.value }))});
        }

        // the config the form currently describes, or null (with the reason shown)
        function fromForm() {
          const values = read();
          const problems = F.validate(values);
          if (problems.length) { error.textContent = problems.join(" "); return null; }
          return F.applyForm(current, values);
        }
        function fromJson() {
          try {
            const parsed = JSON.parse(jsonBox.value);
            if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
            return parsed;
          } catch (e) { error.textContent = "The configuration is not valid JSON."; return null; }
        }
        function show(next) {
          error.textContent = "";
          if (next === tab) return;
          const config = tab === "form" ? fromForm() : fromJson();
          if (config === null) return;
          current = config;
          tab = next;
          formBox.hidden = tab !== "form";
          jsonBox.hidden = tab !== "json";
          tabForm.className = tab === "form" ? "primary" : "";
          tabJson.className = tab === "json" ? "primary" : "";
          if (tab === "form") buildForm(); else jsonBox.value = JSON.stringify(current, null, 2);
        }
        tabForm.addEventListener("click", () => show("form"));
        tabJson.addEventListener("click", () => show("json"));

        const importer = h("input", { type: "file", accept: ".json,application/json" });
        importer.addEventListener("change", () => {
          const file = importer.files[0];
          if (!file) return;
          const reader = new FileReader();
          reader.onload = () => {
            jsonBox.value = String(reader.result);
            tab = "form"; show("json");      // look at what was imported before saving
          };
          reader.readAsText(file);
        });

        buildForm();
        let result = null;
        const form = h("form", { method: "dialog" },
          h("h1", {}, name ? "Edit project" : "New project"),
          labelled("Name", nameInput),
          h("div", { class: "row" }, tabForm, tabJson),
          formBox, jsonBox,
          h("label", {}, "Import from a .json file", importer),
          error,
          h("div", { class: "buttons" },
            h("button", { type: "button", onclick: () => dialogEl.close() }, "Cancel"),
            h("button", { type: "submit", class: "primary" }, "Save")));
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          error.textContent = "";
          const config = tab === "form" ? fromForm() : fromJson();
          if (config === null) return;
          result = { name: nameInput.value, config };
          dialogEl.close();
        });
        dialogEl.addEventListener("close", () => { clear(dialogEl); resolve(result); }, { once: true });
        clear(dialogEl);
        dialogEl.append(form);
        dialogEl.showModal();
        nameInput.focus();
      });
    }

    async function projectEditor(existing) {
      let config = existing ? existing.config : null;
      if (!existing) {
        try { config = await api("GET", "/admin/project-defaults"); }
        catch (e) { if (e.status === 401) return; config = {}; }
      }
      const v = await projectDialog(existing ? existing.name : "", config);
      if (!v) return;
      const id = existing ? existing.id : crypto.randomUUID();
      act(() => api("PUT", base + "/" + id, { name: v.name.trim(), config: v.config }),
        () => { location.hash = "#/" + plural + "/" + id; if (existing) render(); },
        existing ? "Saved." : "Created.");
    }

    async function editor(existing) {
      if (kind === "project") return projectEditor(existing);
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
          wbutton("Edit / new version", () => editor(item), "primary"))];
      if (kind === "project") {
        const have = new Set(pcList.filter((p) => p.assigned_project_ids.includes(id)).map((p) => p.id));
        parts.push(h("h2", {}, "Assigned PCs"),
          h("div", { class: "row" },
            wbutton("Assign to all PCs", () => act(() => api("POST", base + "/" + id + "/push", { all_pcs: true }), render, "Assigned to all PCs.")),
            h("span", { class: "muted" }, "PCs pick up a new version at their next poll.")),
          table(["PC", "Assigned", ""], pcList.map((p) => [p.display_name,
            have.has(p.id) ? badge("assigned", "ok") : badge("not assigned"),
            actions(have.has(p.id)
              ? wbutton("Unassign", () => act(() => api("DELETE", base + "/" + id + "/assignments/" + p.id), render, "Unassigned."), "danger")
              : wbutton("Assign", () => act(() => api("POST", base + "/" + id + "/push", { pc_ids: [p.id] }), render, "Assigned.")))]),
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
        wbutton("Add admin...", async () => {
          const v = await ask("Add admin", [
            { name: "username", label: "Username", hint: "3-100 letters, digits . _ @ -" },
            { name: "password", label: "Password", type: "password", hint: "At least 12 characters." },
            { name: "role", label: "Role", value: "admin", hint: "admin (can change settings) or auditor (read-only)." }], "Create");
          if (v) act(() => api("POST", "/admin/users", { username: v.username.trim(), password: v.password,
            role: (v.role || "admin").trim().toLowerCase() }), reload, "Account created.");
        }, "primary"),
        wbutton("Sign everyone out", () => {
          if (confirm("Sign out every admin, including you?"))
            act(() => api("POST", "/admin/users/revoke-all-sessions"), () => showLogin(), "All sessions revoked.");
        }, "danger")),
      table(["Username", "Role", "Status", "Failed logins", ""], list.map((u) => [
        u.username, badge(u.role === "auditor" ? "auditor (read-only)" : "admin"), badge(u.status + (u.locked_until ? " until " + when(u.locked_until) : ""), tone[u.status]), u.failed_count,
        actions(
          u.status === "disabled"
            ? wbutton("Enable", () => act(() => api("POST", "/admin/users/" + u.id + "/enable"), reload, "Enabled."))
            : wbutton("Disable", () => { if (confirm("Disable " + u.username + "? They are signed out and cannot sign in."))
                act(() => api("POST", "/admin/users/" + u.id + "/disable"), reload, "Disabled."); }, "danger"),
          u.status === "locked" ? wbutton("Unlock", () => act(() => api("POST", "/admin/users/" + u.id + "/unlock"), reload, "Unlocked.")) : null,
          wbutton("Change role", async () => {
            const v = await ask("Role of " + u.username, [
              { name: "role", label: "Role", value: u.role, hint: "admin or auditor (read-only). They are signed out." }], "Change");
            if (v) act(() => api("POST", "/admin/users/" + u.id + "/role", { role: v.role.trim().toLowerCase() }), reload, "Role changed.");
          }),
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
        }, "primary"))));
  }

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

  // Why a station is on the "needs a look" list, in words.
  function reasons(p) {
    const out = [];
    if (p.status === "offline") out.push("No contact since " + ago(p.last_seen_at));
    if (p.status === "key_revoked") out.push("Its access was revoked");
    if (p.rollout === "behind" && p.status !== "key_revoked") {
      const n = p.projects.filter((q) => q.state === "behind" || q.state === "not_applied").length;
      out.push(n + " " + plural(n, "project is", "projects are") + " behind");
    }
    if (p.today && p.today.errors > 0) out.push(count(p.today.errors) + " " + plural(p.today.errors, "error", "errors") + " today");
    return out;
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

  let fleetFilter = "all";
  let fleetSearch = "";

  async function fleet() {
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
        h("div", {}, h("div", { class: "station" }, p.display_name),
          h("div", { class: "sub" }, p.last_seen_at ? "Last seen " + ago(p.last_seen_at) : "Never seen")),
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

  // ----------------------------------------------------------------- router
  const routes = {
    overview, fleet, activity, pcs, admins, audit, settings,
    projects: (id) => (id ? projectViews.detail(id) : projectViews.list()),
    templates: (id) => (id ? templateViews.detail(id) : templateViews.list()),
  };

  async function render() {
    const myRender = ++renderId;
    clearInterval(refreshTimer);
    refreshTimer = null;
    const [name, id] = (location.hash.replace(/^#\//, "") || "overview").split("/");
    const route = routes[name] || overview;
    for (const a of document.querySelectorAll("#nav a")) {
      a.classList.toggle("active", a.dataset.view === (routes[name] ? name : "overview"));
    }
    try {
      const content = await route(id);
      if (myRender !== renderId) return;       // the person moved on while this was loading
      clear(view);
      view.append(content);
      if (route.refresh) {
        refreshTimer = setInterval(() => { if (!document.hidden && !dialogEl.open) render(); }, route.refresh);
      }
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
      me.username + (role === "auditor" ? " (read-only)" : "");
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
