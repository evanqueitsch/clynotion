(function () {
  const $ = (id) => document.getElementById(id);
  let pendingTestId = "";
  let keepQaOpen = false;

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
        ui().api("/compliance/screening"),
      ]);
      await renderScreening(root, settings, roster, history, { people: [] }, "", true);
      ui().setStatus(status, "");
      const controller = new AbortController();
      const timer = setTimeout(function () { controller.abort(); }, 8000);
      try {
        const reviews = await ui().api("/compliance/reviews/open", { signal: controller.signal });
        clearTimeout(timer);
        await renderScreening(root, settings, roster, history, reviews, "", false);
      } catch (error) {
        clearTimeout(timer);
        const reviewError = controller.signal.aborted
          ? "The review is taking too long. The rest of this page is ready."
          : "Possible-match review could not be loaded. The rest of this page is still available.";
        await renderScreening(root, settings, roster, history, { people: [] }, reviewError, false);
      }
    } catch (error) {
      ui().setStatus(status, error.message, "error");
    }
  }

  function renderReviews(root, reviews) {
    const people = (reviews && reviews.people) || [];
    if (!people.length) return;
    const heading = document.createElement("h3");
    heading.textContent = "Possible matches to review";
    root.appendChild(heading);
    people.forEach(function (person) {
      const card = document.createElement("section");
      card.className = "review-card";
      card.id = "review-" + String(person.review_id || "");
      const header = document.createElement("h3");
      header.textContent = person.header || "";
      card.appendChild(header);
      (person.lists || []).forEach(function (list) {
        const block = document.createElement("div");
        block.className = "review-list";
        const title = document.createElement("h4");
        title.textContent = list.list_label || list.list_name || "List";
        block.appendChild(title);
        const why = document.createElement("p");
        why.textContent = "Why this was flagged: " + (list.why || "");
        block.appendChild(why);
        const likelihood = document.createElement("p");
        const label = document.createElement("strong");
        label.textContent = (list.likelihood || "") + ".";
        likelihood.appendChild(label);
        if (list.likelihood_reason) {
          likelihood.appendChild(document.createElement("br"));
          likelihood.appendChild(document.createTextNode(list.likelihood_reason));
        }
        block.appendChild(likelihood);
        if (list.next_step) {
          const step = document.createElement("p");
          step.textContent = list.next_step;
          block.appendChild(step);
        }
        if (list.alignment) {
          const same = document.createElement("p");
          same.textContent = list.alignment;
          block.appendChild(same);
        }
        if (list.sam_busy || (list.list_name === "sam" && list.detail_note)) {
          const busy = document.createElement("p");
          busy.textContent = list.sam_busy
            ? "SAM.gov was busy, so details couldn't be loaded. Try again later."
            : (list.detail_note || "");
          block.appendChild(busy);
          block.appendChild(button("Retry", "ghost", async function () {
            try {
              const refreshed = await ui().api("/compliance/reviews/sam-retry", {
                method: "POST",
                json: { review_id: person.review_id },
              });
              const root = $("screening-root");
              const current = root && root._screen;
              if (!current) {
                await openScreening();
                return;
              }
              await renderScreening(root, current.settings, current.roster, current.history, refreshed, "", false);
            } catch (error) {
              ui().setStatus($("screening-status"), error.message, "error");
            }
          }));
        } else if (list.detail_note) {
          const note = document.createElement("p");
          note.className = "meta";
          note.textContent = list.detail_note;
          block.appendChild(note);
        }
        const table = document.createElement("table");
        table.className = "cmp";
        const head = document.createElement("tr");
        ["Field", "Exclusion list", "Our record", ""].forEach(function (label) {
          const cell = document.createElement("th");
          cell.textContent = label;
          head.appendChild(cell);
        });
        table.appendChild(head);
        const extra = document.createElement("tbody");
        extra.hidden = true;
        function addRow(item, target) {
          const tr = document.createElement("tr");
          const mark = item.mark || "Not available";
          tr.className = mark === "Match" ? "cmp-match" : (mark === "Different" ? "cmp-different" : "cmp-missing");
          [item.label, item.list_value, item.staff_value, mark].forEach(function (value) {
            const cell = document.createElement("td");
            cell.textContent = value || "";
            tr.appendChild(cell);
          });
          target.appendChild(tr);
        }
        (list.rows || []).forEach(function (item) {
          const blank = item.both_blank && item.list_value === "Not available" && item.staff_value === "Not available";
          addRow(item, blank ? extra : table);
        });
        block.appendChild(table);
        if (extra.childElementCount) {
          const more = document.createElement("details");
          const summary = document.createElement("summary");
          summary.textContent = "Show all fields";
          more.appendChild(summary);
          const rest = document.createElement("table");
          rest.className = "cmp";
          rest.appendChild(extra);
          more.appendChild(rest);
          block.appendChild(more);
        }
        (list.links || []).forEach(function (link) {
          const anchor = document.createElement("a");
          anchor.href = link.url;
          anchor.target = "_blank";
          anchor.rel = "noopener";
          anchor.textContent = link.label;
          block.appendChild(anchor);
          block.appendChild(document.createTextNode(" "));
        });
        if (list.ssn_note) {
          const ssn = document.createElement("p");
          ssn.className = "meta";
          ssn.textContent = list.ssn_note;
          block.appendChild(ssn);
        }
        card.appendChild(block);
      });
      const reason = document.createElement("input");
      reason.placeholder = "Short reason, for example NPI differs";
      card.appendChild(reason);
      const actions = person.consequences || {};
      const clearClass = person.likelihood === "Likely different person" ? "primary" : "ghost";
      card.appendChild(button("Not the same person — clear this match", clearClass, function () {
        decide(person.review_id, "not_the_same_person", reason.value);
      }));
      const clearText = document.createElement("p");
      clearText.className = "meta";
      clearText.textContent = actions.not_the_same_person || "";
      card.appendChild(clearText);
      card.appendChild(button("Same person — confirmed exclusion", "danger", function () {
        const ok = window.confirm("This confirms the exclusion for every flagged list. This person cannot bill Medicaid or Medicare, and Activate stays blocked.");
        if (!ok) return;
        decide(person.review_id, "confirmed_exclusion", reason.value);
      }));
      const confirmText = document.createElement("p");
      confirmText.className = "meta";
      confirmText.textContent = actions.confirmed_exclusion || "";
      card.appendChild(confirmText);
      card.appendChild(button("Need more info", "ghost", function () {
        decide(person.review_id, "need_more_info", reason.value);
      }));
      const holdText = document.createElement("p");
      holdText.className = "meta";
      holdText.textContent = actions.need_more_info || "";
      card.appendChild(holdText);
      root.appendChild(card);
    });
  }

  async function decide(id, decision, note) {
    try {
      await ui().api("/compliance/reviews/decide", {
        method: "POST",
        json: { review_id: id, decision: decision, note: note || "" },
      });
      await openScreening();
    } catch (error) {
      ui().setStatus($("screening-status"), error.message, "error");
    }
  }

  async function renderScreening(root, settings, roster, history, reviews, reviewError, reviewLoading) {
    root._screen = { settings: settings, roster: roster, history: history };
    const intro = document.createElement("p");
    intro.className = "meta";
    const counts = roster.counts || {};
    intro.textContent = "Screening roster: " + (counts.total || (roster.people || []).length)
      + " people (employees and paid outside supervisors). "
      + "Monthly schedule: " + (settings.monthly_screening_enabled ? "on" : "off")
      + ". SAM: " + settings.sam_status
      + ". " + settings.monthly_detail;
    root.appendChild(intro);
    if (reviewError) {
      const box = document.createElement("p");
      box.className = "warn";
      box.textContent = reviewError;
      root.appendChild(box);
    } else if (reviewLoading) {
      const loading = document.createElement("p");
      loading.className = "meta";
      loading.textContent = "Loading possible-match review…";
      root.appendChild(loading);
    }
    renderReviews(root, reviews);

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

    const qa = document.createElement("details");
    qa.className = "qa-tools";
    if (keepQaOpen) qa.open = true;
    keepQaOpen = false;
    const qaSummary = document.createElement("summary");
    qaSummary.textContent = "QA tools (test records only)";
    qa.appendChild(qaSummary);
    const qaLabel = document.createElement("p");
    qaLabel.className = "meta";
    qaLabel.textContent = "Fake records used only to test this feature; not real staff.";
    qa.appendChild(qaLabel);
    const fixture = document.createElement("div");
    const fixtureNote = document.createElement("p");
    fixtureNote.className = "meta";
    fixtureNote.textContent = "Record test match stores a labeled TEST possible match. No name list is required.";
    const testPicker = document.createElement("select");
    const testBlank = document.createElement("option");
    testBlank.value = "";
    testBlank.textContent = "Choose a test record";
    testPicker.appendChild(testBlank);
    (roster.test_people || []).forEach(function (item) {
      const option = document.createElement("option");
      option.value = item.person_id;
      option.textContent = item.name + " — " + item.person_id + (item.created_on ? " — created " + item.created_on : "");
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
        keepQaOpen = true;
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
        keepQaOpen = true;
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
        pendingTestId = testPicker.value;
        keepQaOpen = true;
        await openScreening();
        ui().setStatus($("screening-status"), "Test screening saved.", "ok");
      } catch (error) {
        ui().setStatus($("screening-status"), error.message, "error");
      }
    }));
    fixture.appendChild(recordMatch);
    qa.appendChild(fixture);
    const testRows = document.createElement("div");
    testRows.className = "due-list";
    qa.appendChild(testRows);
    if (qa.open) {
      const testHistory = await ui().api("/compliance/screening?include_test=1");
      drawRows(testRows, (testHistory.results || []).filter(function (item) { return item.kind === "test"; }), testHistory.resolutions || [], roster.test_people || []);
    } else {
      qa.addEventListener("toggle", async function () {
        if (!qa.open || testRows.childElementCount) return;
        const testHistory = await ui().api("/compliance/screening?include_test=1");
        drawRows(testRows, (testHistory.results || []).filter(function (item) { return item.kind === "test"; }), testHistory.resolutions || [], roster.test_people || []);
      });
    }
    root.appendChild(qa);

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

    const rows = document.createElement("div");
    rows.className = "due-list";
    root.appendChild(rows);
    drawRows(rows, (history.results || []).filter(function (item) { return item.kind !== "test"; }), history.resolutions || []);
    const wanted = (window.location.hash.split("review=")[1] || "").split("&")[0];
    if (wanted) {
      const card = document.getElementById("review-" + decodeURIComponent(wanted).replace(/[^A-Za-z0-9_-]/g, "-"));
      if (card) card.scrollIntoView();
    }
  }

  function drawRows(root, results, resolutions, testPeople) {
    root.innerHTML = "";
    const decided = {};
    const created = {};
    (testPeople || []).forEach(function (item) { created[item.person_id] = item.created_on || ""; });
    (resolutions || []).forEach(function (item) { decided[item.result_id] = item; });
    (results || []).forEach(function (item) {
      const row = document.createElement("div");
      row.className = "due-row";
      const text = document.createElement("div");
      const bits = [item.checked_at_et, item.display_name, item.list_name, item.result, item.detail].filter(Boolean);
      if (item.kind === "test") {
        bits.push(item.person_id);
        const createdOn = item.created_on || created[item.person_id];
        if (createdOn) bits.push("created " + createdOn);
      }
      if (decided[item.id] && decided[item.id].decision === "closed_test_record") {
        bits.push("Closed — test hire deleted");
      }
      text.textContent = bits.join(" — ");
      row.appendChild(text);
      if (testPeople && item.result === "possible_match" && !decided[item.id]) {
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
