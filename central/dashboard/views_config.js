// Projects and templates: the list + detail pages and the form editor.
"use strict";

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
