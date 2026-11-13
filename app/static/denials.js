/* De-identified denial analysis. No claim values are written to the console. */
(function () {
  const COMPLIANCE =
    "Suggestions never recommend changing a diagnosis or CPT to get paid. Documentation gaps are flagged for clinician review only.";

  function $(id) {
    return document.getElementById(id);
  }

  function bridge() {
    return window.clynotion;
  }

  function money(value) {
    return value == null || value === "" ? "—" : value;
  }

  function table(columns, rows) {
    const node = document.createElement("table");
    node.className = "denials-table";
    const head = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("th");
      cell.textContent = column.label;
      head.appendChild(cell);
    });
    node.appendChild(head);
    rows.forEach((row) => {
      const line = document.createElement("tr");
      columns.forEach((column) => {
        const cell = document.createElement("td");
        cell.textContent = row[column.key] == null ? "" : String(row[column.key]);
        line.appendChild(cell);
      });
      node.appendChild(line);
    });
    return node;
  }

  function section(title, node) {
    const wrap = document.createElement("div");
    const heading = document.createElement("h3");
    heading.textContent = title;
    wrap.appendChild(heading);
    wrap.appendChild(node);
    return wrap;
  }

  async function openDenials() {
    if ((window.location.hash || "") !== "#denials") {
      window.location.hash = "#denials";
      return;
    }
    const ui = bridge();
    ui.show("denials-panel");
    const status = $("denials-status");
    ui.setStatus(status, "Loading…");
    $("denials-compliance").textContent = COMPLIANCE;
    try {
      const home = await ui.api("/denials");
      const dashboard = await ui.api("/denials/dashboard");
      renderSummary(dashboard, home.access);
      if (home.access === "full") {
        renderUpload(home.configured);
        await renderWork(home.configured);
        await renderFiles(home.configured);
      } else {
        $("denials-upload").innerHTML = "";
        $("denials-work").innerHTML = "";
        $("denials-files").innerHTML = "";
      }
      ui.setStatus(
        status,
        home.configured ? "" : "Denial analysis is not configured.",
        home.configured ? "ok" : "error"
      );
    } catch (error) {
      ui.setStatus(status, error.message, "error");
    }
  }

  function renderSummary(data, access) {
    const root = $("denials-summary");
    root.innerHTML = "";
    const totals = data.totals || {};
    const line = document.createElement("p");
    line.textContent =
      "Claims " +
      (totals.claims || 0) +
      " · Denied " +
      (totals.denied || 0) +
      " · Rejected " +
      (totals.rejected || 0) +
      " · Denial rate " +
      (totals.denial_rate || "0.0000") +
      " · Billed " +
      money(totals.billed) +
      " · Paid " +
      money(totals.paid);
    root.appendChild(line);
    if (access === "summary") {
      const note = document.createElement("p");
      note.className = "meta";
      note.textContent = "Totals and trends only.";
      root.appendChild(note);
    }
    const blocks = [
      ["By payer", data.by_payer],
      ["By CPT", data.by_cpt],
      ["By modifier and place of service", data.by_modifier_pos],
      ["By denial category", data.by_category],
      ["By CARC", data.by_carc],
    ];
    blocks.forEach(([title, rows]) => {
      root.appendChild(
        section(
          title,
          table(
            [
              { key: "key", label: "Group" },
              { key: "claims", label: "Claims" },
              { key: "denied", label: "Denied or rejected" },
              { key: "rate", label: "Rate" },
            ],
            rows || []
          )
        )
      );
    });
    root.appendChild(
      section(
        "Trend by month",
        table(
          [
            { key: "month", label: "Month" },
            { key: "claims", label: "Claims" },
            { key: "denied", label: "Denied or rejected" },
            { key: "rate", label: "Rate" },
          ],
          data.trend || []
        )
      )
    );
  }

  function renderUpload(configured) {
    const root = $("denials-upload");
    root.innerHTML = "";
    if (!configured) return;
    const type = document.createElement("select");
    type.id = "denials-file-type";
    [
      ["filed_claims", "Filed claims"],
      ["payment_reports", "Payment report"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      type.appendChild(option);
    });
    const file = document.createElement("input");
    file.type = "file";
    file.accept = ".csv,.xlsx,text/csv";
    const check = document.createElement("button");
    check.type = "button";
    check.className = "ghost";
    check.textContent = "Check file";
    const mapHost = document.createElement("div");
    const load = document.createElement("button");
    load.type = "button";
    load.className = "primary";
    load.textContent = "Load claims";
    load.disabled = true;
    let current = null;
    check.addEventListener("click", async () => {
      const ui = bridge();
      if (!file.files || !file.files[0]) {
        ui.setStatus($("denials-status"), "Choose a CSV or XLSX file.", "error");
        return;
      }
      const body = new FormData();
      body.append("file_type", type.value);
      body.append("upload", file.files[0]);
      try {
        current = await ui.api("/denials/inspect", { method: "POST", body });
        drawMapping(mapHost, current);
        load.disabled = false;
        ui.setStatus($("denials-status"), "File passed the de-identification check. Map the columns, then load.", "ok");
      } catch (error) {
        current = null;
        load.disabled = true;
        mapHost.innerHTML = "";
        ui.setStatus($("denials-status"), error.message, "error");
      }
    });
    load.addEventListener("click", async () => {
      const ui = bridge();
      if (!current || !file.files || !file.files[0]) return;
      const mapping = {};
      mapHost.querySelectorAll("select").forEach((select) => {
        if (select.value) mapping[select.dataset.header] = select.value;
      });
      const body = new FormData();
      body.append("file_type", type.value);
      body.append("mapping", JSON.stringify(mapping));
      body.append("upload", file.files[0]);
      try {
        const result = await ui.api("/denials/uploads", { method: "POST", body });
        ui.setStatus(
          $("denials-status"),
          "Loaded " + result.added + " claims. Skipped " + result.skipped_duplicates + " duplicates.",
          "ok"
        );
        await openDenials();
      } catch (error) {
        ui.setStatus($("denials-status"), error.message, "error");
      }
    });
    root.appendChild(type);
    root.appendChild(file);
    root.appendChild(check);
    root.appendChild(mapHost);
    root.appendChild(load);
  }

  function drawMapping(host, inspected) {
    host.innerHTML = "";
    const saved = inspected.saved_mapping || {};
    const suggested = inspected.suggested || {};
    (inspected.headers || []).forEach((header) => {
      const label = document.createElement("label");
      label.textContent = header;
      const select = document.createElement("select");
      select.dataset.header = header;
      const ignore = document.createElement("option");
      ignore.value = "";
      ignore.textContent = "Ignore";
      select.appendChild(ignore);
      (inspected.fields || []).forEach((field) => {
        const option = document.createElement("option");
        option.value = field.id;
        option.textContent = field.label + (field.required ? " (required)" : "");
        select.appendChild(option);
      });
      select.value = saved[header] || suggested[header] || "";
      label.appendChild(select);
      host.appendChild(label);
    });
  }

  async function renderWork(configured) {
    const root = $("denials-work");
    root.innerHTML = "";
    if (!configured) return;
    const ui = bridge();
    const data = await ui.api("/denials/worklist");
    const heading = document.createElement("h3");
    heading.textContent = "Work list";
    root.appendChild(heading);
    const note = document.createElement("p");
    note.className = "meta";
    note.textContent = data.compliance_note || COMPLIANCE;
    root.appendChild(note);
    const exportButton = document.createElement("button");
    exportButton.type = "button";
    exportButton.className = "ghost";
    exportButton.textContent = "Export work list";
    exportButton.addEventListener("click", async () => {
      const headers = {};
      if (ui.state && ui.state.token) headers.Authorization = "Bearer " + ui.state.token;
      const response = await fetch("/denials/export", { headers, credentials: "include" });
      if (!response.ok) {
        ui.setStatus($("denials-status"), "Export failed.", "error");
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "denials-worklist.csv";
      link.click();
      URL.revokeObjectURL(url);
    });
    root.appendChild(exportButton);
    (data.items || []).forEach((item) => {
      const card = document.createElement("div");
      card.className = "tool-card";
      const title = document.createElement("p");
      title.textContent =
        item.claim_key +
        " · " +
        item.payer +
        " · " +
        item.cpt +
        " · " +
        item.claim_status +
        " · " +
        (item.carc || "no CARC");
      const action = document.createElement("p");
      action.textContent = item.action_label || "";
      const owner = document.createElement("input");
      owner.type = "text";
      owner.value = item.owner || "";
      owner.placeholder = "Owner";
      const status = document.createElement("select");
      ["open", "in_progress", "waiting", "done"].forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value.replace("_", " ");
        status.appendChild(option);
      });
      status.value = item.work_status || "open";
      const save = document.createElement("button");
      save.type = "button";
      save.className = "primary";
      save.textContent = "Save";
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "ghost";
      clear.textContent = "Clear";
      async function send(nextOwner, nextStatus) {
        await ui.api("/denials/work/" + item.id, {
          method: "PATCH",
          json: { owner: nextOwner, work_status: nextStatus },
        });
        await openDenials();
      }
      save.addEventListener("click", () => send(owner.value, status.value).catch((error) => {
        ui.setStatus($("denials-status"), error.message, "error");
      }));
      clear.addEventListener("click", () => send("", "open").catch((error) => {
        ui.setStatus($("denials-status"), error.message, "error");
      }));
      card.appendChild(title);
      card.appendChild(action);
      card.appendChild(owner);
      card.appendChild(status);
      card.appendChild(save);
      card.appendChild(clear);
      root.appendChild(card);
    });
  }

  async function renderFiles(configured) {
    const root = $("denials-files");
    root.innerHTML = "";
    if (!configured) return;
    const ui = bridge();
    const data = await ui.api("/denials/uploads");
    const heading = document.createElement("h3");
    heading.textContent = "Uploads";
    root.appendChild(heading);
    (data.uploads || []).forEach((item) => {
      const row = document.createElement("div");
      row.className = "due-row";
      const label = document.createElement("span");
      label.textContent = item.file_type + " · added " + item.added + " · skipped " + item.skipped;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.textContent = "Delete batch";
      button.addEventListener("click", async () => {
        try {
          await ui.api("/denials/uploads/" + item.id, { method: "DELETE" });
          await openDenials();
        } catch (error) {
          ui.setStatus($("denials-status"), error.message, "error");
        }
      });
      row.appendChild(label);
      row.appendChild(button);
      root.appendChild(row);
    });
  }

  async function openSettings() {
    if ((window.location.hash || "") !== "#settings") {
      window.location.hash = "#settings";
      return;
    }
    const ui = bridge();
    ui.show("settings-panel");
    const status = $("settings-status");
    ui.setStatus(status, "Loading…");
    try {
      const data = await ui.api("/denials/settings");
      renderSettings(data);
      ui.setStatus(status, data.configured ? "" : data.detail || "Denial analysis is not configured.", data.configured ? "ok" : "error");
    } catch (error) {
      ui.setStatus(status, error.message, "error");
    }
  }

  function renderSettings(data) {
    const phi = $("settings-phi");
    phi.innerHTML = "";
    const lock = document.createElement("p");
    lock.textContent = "PHI mode is off and cannot be turned on in this phase.";
    phi.appendChild(lock);
    (data.phi_checklist || []).forEach((item) => {
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = false;
      box.disabled = true;
      label.appendChild(box);
      label.appendChild(document.createTextNode(" " + item.label));
      phi.appendChild(label);
    });
    const form = $("settings-retention");
    form.innerHTML = "";
    const days = document.createElement("input");
    days.type = "number";
    days.min = "30";
    days.value = String(data.upload_retention_days || 90);
    const months = document.createElement("input");
    months.type = "number";
    months.min = "6";
    months.value = String(data.row_retention_months || 24);
    const confirm = document.createElement("input");
    confirm.type = "text";
    confirm.placeholder = "Type PURGE DENIALS if this would delete stored files";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save retention";
    save.disabled = !data.configured;
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "ghost";
    restore.textContent = "Restore defaults";
    restore.disabled = !data.configured;
    const auditNote = document.createElement("p");
    auditNote.className = "meta";
    auditNote.textContent = "Audit log: " + (data.audit_retention_years || 6) + " years. Not editable.";
    save.addEventListener("click", () => saveRetention(days.value, months.value, confirm.value));
    restore.addEventListener("click", () => saveRetention("90", "24", confirm.value));
    form.appendChild(document.createTextNode("Original uploads (days)"));
    form.appendChild(days);
    form.appendChild(document.createTextNode("Claim rows (months)"));
    form.appendChild(months);
    form.appendChild(confirm);
    form.appendChild(save);
    form.appendChild(restore);
    form.appendChild(auditNote);
    const audit = $("settings-audit");
    audit.innerHTML = "";
    (data.audit || []).forEach((event) => {
      const line = document.createElement("p");
      line.className = "meta";
      line.textContent = event.action + " · " + event.what + " · " + JSON.stringify(event.counts || {});
      audit.appendChild(line);
    });
  }

  async function saveRetention(days, months, confirmation) {
    const ui = bridge();
    try {
      await ui.api("/denials/settings", {
        method: "PUT",
        json: {
          upload_retention_days: Number(days),
          row_retention_months: Number(months),
          confirmation: confirmation || "",
        },
      });
      await openSettings();
      ui.setStatus($("settings-status"), "Retention saved.", "ok");
    } catch (error) {
      ui.setStatus($("settings-status"), error.message, "error");
    }
  }

  window.openDenials = openDenials;
  window.openSettings = openSettings;
  const home = $("btn-denials-home");
  if (home) home.addEventListener("click", () => { window.location.hash = ""; });
  const settingsHome = $("btn-settings-home");
  if (settingsHome) settingsHome.addEventListener("click", () => { window.location.hash = ""; });
  const settingsButton = $("btn-top-settings");
  if (settingsButton) settingsButton.addEventListener("click", () => { window.location.hash = "#settings"; });
})();
