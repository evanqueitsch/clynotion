(function () {
  const $ = (id) => document.getElementById(id);
  let pendingTestId = "";

  function ui() {
    return window.clynotion;
  }

  function field(label, value) {
    const wrap = document.createElement("label");
    wrap.textContent = label;
    const input = document.createElement("input");
    input.value = value || "";
    wrap.appendChild(input);
    return { wrap: wrap, input: input };
  }

  function button(text, className, onclick) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className || "ghost";
    node.textContent = text;
    node.onclick = onclick;
    return node;
  }

  async function openScreening() {
    const root = $("screening-root");
    const status = $("screening-status");
    if (!root) return;
    ui().setStatus(status, "Loading…");
    root.innerHTML = "";
    try {
      const [settings, roster, history] = await Promise.all([
        ui().api("/compliance/settings"),
        ui().api("/compliance/roster"),
        ui().api("/compliance/screening?include_test=1"),
      ]);
      renderScreening(root, settings, roster, history);
      ui().setStatus(status, "");
    } catch (error) {
      ui().setStatus(status, error.message, "error");
    }
  }

  function renderScreening(root, settings, roster, history) {
    const intro = document.createElement("p");
    intro.className = "meta";
    intro.textContent = "Monthly schedule: " + (settings.monthly_screening_enabled ? "on" : "off")
      + ". SAM: " + settings.sam_status
      + ". " + settings.monthly_detail;
    root.appendChild(intro);

    const filters = document.createElement("div");
    filters.className = "cg-filters";
    const person = field("Person id", "");
    const month = field("Month (YYYY-MM)", "");
    const list = field("List", "");
    filters.appendChild(person.wrap);
    filters.appendChild(month.wrap);
    filters.appendChild(list.wrap);
    filters.appendChild(button("Filter", "ghost", async function () {
      const params = new URLSearchParams();
      if (person.input.value) params.set("person_id", person.input.value.trim());
      if (month.input.value) params.set("month", month.input.value.trim());
      if (list.input.value) params.set("list_name", list.input.value.trim());
      const filtered = await ui().api("/compliance/screening?" + params.toString());
      drawRows(rows, filtered.results || [], filtered.resolutions || []);
    }));
    const query = new URLSearchParams();
    filters.appendChild(button("CSV", "ghost", function () {
      download("/compliance/screening/export.csv");
    }));
    filters.appendChild(button("PDF", "ghost", function () {
      download("/compliance/screening/export.pdf");
    }));
    root.appendChild(filters);

    const runBox = document.createElement("div");
    const picker = document.createElement("select");
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Choose one person";
    picker.appendChild(blank);
    (roster.people || []).forEach(function (item) {
      const option = document.createElement("option");
      option.value = item.person_id;
      option.textContent = item.name + " (" + item.kind + ")";
      picker.appendChild(option);
    });
    runBox.appendChild(picker);
    runBox.appendChild(button("Screen this person", "primary", async function () {
      if (!picker.value) {
        ui().setStatus($("screening-status"), "Choose one person. This does not screen the roster.", "error");
        return;
      }
      ui().setStatus($("screening-status"), "Running…");
      try {
        await ui().api("/compliance/screening/run", { method: "POST", json: { person_id: picker.value } });
        await openScreening();
        ui().setStatus($("screening-status"), "Screening saved.", "ok");
      } catch (error) {
        ui().setStatus($("screening-status"), error.message, "error");
      }
    }));
    const confirm = document.createElement("input");
    confirm.placeholder = "Type SCREEN ROSTER";
    runBox.appendChild(confirm);
    runBox.appendChild(button("Run screening now", "ghost", async function () {
      ui().setStatus($("screening-status"), "Running roster…");
      try {
        await ui().api("/compliance/screening/run", { method: "POST", json: { confirm: confirm.value } });
        await openScreening();
      } catch (error) {
        ui().setStatus($("screening-status"), error.message, "error");
      }
    }));
    root.appendChild(runBox);

    const fixture = document.createElement("div");
    const fixtureNote = document.createElement("p");
    fixtureNote.className = "meta";
    fixtureNote.textContent = "Test records only. Record test match always stores a labeled TEST possible match. No name list is required.";
    const testPicker = document.createElement("select");
    const testBlank = document.createElement("option");
    testBlank.value = "";
    testBlank.textContent = "Choose a test record";
    testPicker.appendChild(testBlank);
    (roster.test_people || []).forEach(function (item) {
      const option = document.createElement("option");
      option.value = item.person_id;
      option.textContent = item.name;
      testPicker.appendChild(option);
    });
    const recordMatch = button("Record test match", "ghost", async function () {
      if (!testPicker.value) {
        ui().setStatus($("screening-status"), "Choose a test record. Real employees cannot be used here.", "error");
        return;
      }
      try {
        await ui().api("/compliance/screening/fixture", {
          method: "POST",
          json: { person_id: testPicker.value, list_name: "leie" },
        });
        pendingTestId = testPicker.value;
        await openScreening();
      } catch (error) {
        ui().setStatus($("screening-status"), error.message, "error");
      }
    });
    recordMatch.disabled = !(roster.test_people || []).length;
    fixture.appendChild(fixtureNote);
    fixture.appendChild(button("Create test hire", "ghost", async function () {
      try {
        const created = await ui().api("/compliance/test-hires", { method: "POST", json: {} });
        ui().setStatus($("screening-status"), "Test record created. No email was sent.", "ok");
        if (created && created.id) pendingTestId = "hire:" + created.id;
        await openScreening();
      } catch (error) {
        ui().setStatus($("screening-status"), error.message, "error");
      }
    }));
    fixture.appendChild(testPicker);
    if (pendingTestId) {
      testPicker.value = pendingTestId;
      pendingTestId = "";
    }
    const hiringLink = document.createElement("a");
    hiringLink.href = "#hiring";
    hiringLink.textContent = "Open Hiring";
    fixture.appendChild(hiringLink);
    fixture.appendChild(button("Screen this test record", "primary", async function () {
      if (!testPicker.value) {
        ui().setStatus($("screening-status"), "Choose a test record.", "error");
        return;
      }
      ui().setStatus($("screening-status"), "Running…");
      try {
        await ui().api("/compliance/screening/run", { method: "POST", json: { person_id: testPicker.value } });
        await openScreening();
        ui().setStatus($("screening-status"), "Test screening saved.", "ok");
      } catch (error) {
        ui().setStatus($("screening-status"), error.message, "error");
      }
    }));
    fixture.appendChild(recordMatch);
    root.appendChild(fixture);

    const manual = document.createElement("form");
    manual.className = "cg-form";
    const manualPerson = field("Person id for Medicheck", "");
    const manualDate = field("Date checked", "");
    manualDate.input.type = "date";
    const file = document.createElement("input");
    file.type = "file";
    file.accept = ".pdf,.png,.jpg,.jpeg,.webp";
    manual.appendChild(manualPerson.wrap);
    manual.appendChild(manualDate.wrap);
    manual.appendChild(file);
    manual.appendChild(button("Save manual Medicheck check", "ghost", async function () {
      const body = new FormData();
      body.set("person_id", manualPerson.input.value.trim());
      body.set("checked_on", manualDate.input.value);
      if (file.files && file.files[0]) body.set("upload", file.files[0]);
      const headers = {};
      if (ui().state && ui().state.token) headers.Authorization = "Bearer " + ui().state.token;
      const response = await fetch("/compliance/medicheck/manual", { method: "POST", body: body, headers: headers, credentials: "include" });
      if (!response.ok) {
        ui().setStatus($("screening-status"), "Could not save the manual check.", "error");
        return;
      }
      await openScreening();
    }));
    root.appendChild(manual);

    const testHeading = document.createElement("h3");
    testHeading.textContent = "Test record results";
    root.appendChild(testHeading);
    const testRows = document.createElement("div");
    testRows.className = "due-list";
    root.appendChild(testRows);
    const allResults = history.results || [];
    drawRows(testRows, allResults.filter(function (item) { return item.kind === "test"; }), history.resolutions || []);
    const rows = document.createElement("div");
    rows.className = "due-list";
    root.appendChild(rows);
    drawRows(rows, allResults.filter(function (item) { return item.kind !== "test"; }), history.resolutions || []);
  }

  function drawRows(root, results, resolutions) {
    root.innerHTML = "";
    const decided = {};
    (resolutions || []).forEach(function (item) { decided[item.result_id] = item; });
    (results || []).forEach(function (item) {
      const row = document.createElement("div");
      row.className = "due-row";
      const text = document.createElement("div");
      text.textContent = [item.checked_at_et, item.display_name, item.list_name, item.result, item.detail].filter(Boolean).join(" — ");
      row.appendChild(text);
      if (item.result === "possible_match" && !decided[item.id]) {
        const note = document.createElement("input");
        note.placeholder = "Note";
        row.appendChild(note);
        row.appendChild(button("Not the same person", "ghost", function () {
          resolve(item.id, "not_the_same_person", note.value);
        }));
        row.appendChild(button("Confirmed exclusion", "primary", function () {
          resolve(item.id, "confirmed_exclusion", note.value);
        }));
      }
      root.appendChild(row);
    });
    if (!results.length) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "No screening results yet.";
      root.appendChild(empty);
    }
  }

  async function resolve(id, decision, note) {
    try {
      await ui().api("/compliance/reviews/" + encodeURIComponent(id), {
        method: "POST",
        json: { decision: decision, note: note || "" },
      });
      await openScreening();
    } catch (error) {
      ui().setStatus($("screening-status"), error.message, "error");
    }
  }

  async function download(path) {
    const headers = {};
    if (ui().state && ui().state.token) headers.Authorization = "Bearer " + ui().state.token;
    const response = await fetch(path, { headers: headers, credentials: "include" });
    if (!response.ok) {
      ui().setStatus($("screening-status"), "Export failed.", "error");
      return;
    }
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = path.indexOf(".pdf") >= 0 ? "screening.pdf" : "screening.csv";
    link.click();
  }

  async function openAudit() {
    const root = $("audit-root");
    const status = $("audit-status");
    if (!root) return;
    ui().setStatus(status, "Loading…");
    root.innerHTML = "";
    try {
      const data = await ui().api("/compliance/audit");
      renderAudit(root, data);
      ui().setStatus(status, "");
    } catch (error) {
      ui().setStatus(status, error.message, "error");
    }
  }

  function renderAudit(root, data) {
    const filters = document.createElement("div");
    filters.className = "cg-filters";
    const person = field("Person", "");
    const action = field("Action", "");
    const objectType = field("Object", "");
    const start = field("From", "");
    const end = field("To", "");
    start.input.type = "date";
    end.input.type = "date";
    [person, action, objectType, start, end].forEach(function (item) { filters.appendChild(item.wrap); });
    filters.appendChild(button("Filter", "ghost", async function () {
      const params = new URLSearchParams();
      if (person.input.value) params.set("person", person.input.value.trim());
      if (action.input.value) params.set("action", action.input.value.trim());
      if (objectType.input.value) params.set("object_type", objectType.input.value.trim());
      if (start.input.value) params.set("start", start.input.value);
      if (end.input.value) params.set("end", end.input.value);
      const filtered = await ui().api("/compliance/audit?" + params.toString());
      drawAudit(list, filtered.entries || []);
    }));
    filters.appendChild(button("CSV", "ghost", function () { downloadAudit("/compliance/audit/export.csv"); }));
    filters.appendChild(button("PDF", "ghost", function () { downloadAudit("/compliance/audit/export.pdf"); }));
    if (!data.read_only) {
      filters.appendChild(button("Verify log", "primary", async function () {
        ui().setStatus($("audit-status"), "Checking…");
        try {
          const result = await ui().api("/compliance/audit/verify", { method: "POST" });
          ui().setStatus($("audit-status"), result.ok ? "Log verified." : "The log chain is broken.", result.ok ? "ok" : "error");
          await openAudit();
        } catch (error) {
          ui().setStatus($("audit-status"), error.message, "error");
        }
      }));
    }
    root.appendChild(filters);
    const check = document.createElement("p");
    check.className = "meta";
    check.textContent = data.integrity
      ? ("Last check " + data.integrity.at_et + " — " + (data.integrity.ok ? "intact" : "broken") + " — " + data.integrity.checked + " entries")
      : "No integrity check yet.";
    root.appendChild(check);
    const list = document.createElement("div");
    list.className = "history-log";
    root.appendChild(list);
    drawAudit(list, data.entries || []);
  }

  function drawAudit(root, entries) {
    root.innerHTML = "";
    entries.forEach(function (item) {
      const line = document.createElement("p");
      line.textContent = [item.ts_et, item.actor, item.actor_role, item.action, item.object_type, item.object_id].filter(Boolean).join(" — ");
      root.appendChild(line);
    });
  }

  async function downloadAudit(path) {
    const headers = {};
    if (ui().state && ui().state.token) headers.Authorization = "Bearer " + ui().state.token;
    const response = await fetch(path, { headers: headers, credentials: "include" });
    if (!response.ok) return;
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = path.indexOf(".pdf") >= 0 ? "audit.pdf" : "audit.csv";
    link.click();
  }

  async function renderSettings() {
    const root = $("settings-compliance");
    if (!root || !ui()) return;
    root.innerHTML = "";
    let data;
    try {
      data = await ui().api("/compliance/settings");
    } catch (error) {
      return;
    }
    const title = document.createElement("h3");
    title.textContent = "Exclusion screening and audit log";
    root.appendChild(title);
    const monthly = document.createElement("label");
    const monthlyBox = document.createElement("input");
    monthlyBox.type = "checkbox";
    monthlyBox.checked = !!data.monthly_screening_enabled;
    monthly.appendChild(monthlyBox);
    monthly.appendChild(document.createTextNode(" Monthly exclusion screening"));
    root.appendChild(monthly);
    const monthlyNote = document.createElement("p");
    monthlyNote.className = "meta";
    monthlyNote.textContent = data.monthly_detail;
    root.appendChild(monthlyNote);
    const integrity = document.createElement("label");
    const integrityBox = document.createElement("input");
    integrityBox.type = "checkbox";
    integrityBox.checked = !!data.integrity_check_enabled;
    integrity.appendChild(integrityBox);
    integrity.appendChild(document.createTextNode(" Daily audit integrity check"));
    root.appendChild(integrity);
    const integrityNote = document.createElement("p");
    integrityNote.className = "meta";
    integrityNote.textContent = data.integrity_detail;
    root.appendChild(integrityNote);
    const drive = document.createElement("p");
    drive.className = "meta";
    drive.textContent = "Off-box audit copy: " + data.drive_status + ". " + data.drive_detail;
    root.appendChild(drive);
    const sam = document.createElement("p");
    sam.className = "meta";
    sam.textContent = "SAM: " + data.sam_status;
    root.appendChild(sam);
    root.appendChild(button("Save compliance settings", "primary", async function () {
      try {
        await ui().api("/compliance/settings", {
          method: "PUT",
          json: {
            monthly_screening_enabled: monthlyBox.checked,
            integrity_check_enabled: integrityBox.checked,
          },
        });
        ui().setStatus($("settings-status"), "Compliance settings saved. Nothing ran.", "ok");
        await renderSettings();
      } catch (error) {
        ui().setStatus($("settings-status"), error.message, "error");
      }
    }));
  }

  const screeningHome = $("btn-screening-home");
  if (screeningHome) screeningHome.addEventListener("click", function () { window.location.hash = ""; });
  const auditHome = $("btn-audit-home");
  if (auditHome) auditHome.addEventListener("click", function () { window.location.hash = ""; });

  window.clyCompliance = { openScreening: openScreening, openAudit: openAudit, renderSettings: renderSettings };
})();
