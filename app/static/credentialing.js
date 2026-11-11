/* Credentialing grid. Full IDs are fetched one at a time and never kept in the page payload. */
(function () {
  const TYPES = ["commercial", "medicaid_mco", "chip", "medicare", "tricare", "eap"];
  const STATUSES = [
    ["unknown", "Unknown"],
    ["pending", "Pending"],
    ["in_network", "In network"],
    ["not_eligible", "Not eligible"],
    ["needs_review", "Needs review"],
  ];
  let payload = null;
  let focusTouched = false;
  let timers = [];

  function $(id) {
    return document.getElementById(id);
  }

  function bridge() {
    return window.clynotion;
  }

  function errText(error) {
    return (error && error.message) || "Something went wrong.";
  }

  function maskAll() {
    timers.forEach((id) => window.clearTimeout(id));
    timers = [];
    document.querySelectorAll("[data-revealed]").forEach((el) => {
      el.textContent = el.getAttribute("data-masked") || "";
      el.removeAttribute("data-revealed");
    });
  }

  window.credentialingMask = maskAll;
  window.addEventListener("hashchange", () => {
    const hash = window.location.hash;
    if (hash === "#onboarding" && window.clynotion && typeof openCredentialing === "function") {
      openCredentialing();
      return;
    }
    if (hash !== "#credentialing") maskAll();
  });

  function setMessage(text, kind) {
    const ui = bridge();
    if (!ui) return;
    ui.setStatus($("cg-status"), text, kind || "");
  }

  function subjectName(id) {
    const row = (payload.subjects || []).find((item) => item.id === id);
    return row ? row.name : id;
  }

  function payerName(id) {
    const row = (payload.payers || []).find((item) => item.id === id);
    return row ? row.name : id;
  }

  function cellFor(payerId, subjectId) {
    return (payload.cells || []).find((item) => item.payer_id === payerId && item.subject_id === subjectId);
  }

  function cellLabel(cell) {
    const warn = cell && cell.display === "warning" ? (cell.license_warning || "License does not match this payer.") : "";
    if (!cell || cell.status === "unknown" || cell.display === "suggested") {
      if (cell && (cell.display === "suggested" || cell.suggestion_reason)) {
        return { text: "X", cls: "cg-suggested", title: cell.suggestion_reason || "Suggested not eligible", warn: "" };
      }
      return { text: "", cls: "", title: "Unknown", warn: "" };
    }
    if (cell.status === "in_network") return { text: "Y", cls: "cg-y", title: "In network", warn: warn };
    if (cell.status === "not_eligible") return { text: "X", cls: "cg-x", title: "Not eligible", warn: "" };
    if (cell.status === "pending") return { text: cell.submitted_on || "Pending", cls: "cg-pending", title: "Pending", warn: warn };
    if (cell.status === "needs_review") return { text: "?", cls: "cg-review", title: "Needs review", warn: warn };
    return { text: cell.status, cls: "", title: cell.status, warn: warn };
  }

  function needsAction(cell) {
    if (!cell) return false;
    return cell.status === "pending" || cell.status === "needs_review" || (cell.status === "unknown" && cell.suggestion_reason);
  }

  function activeSubjects() {
    return (payload.subjects || []).filter((item) => !item.archived);
  }

  function visibleSubjects() {
    const focus = $("cg-focus").value;
    const subjects = activeSubjects();
    if (!focus || focus === "all") return subjects;
    return subjects.filter((item) => item.id === focus);
  }

  function renderFocus() {
    const select = $("cg-focus");
    const current = select.value;
    select.innerHTML = "";
    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "All columns";
    select.appendChild(all);
    activeSubjects().forEach((subject) => {
      const option = document.createElement("option");
      option.value = subject.id;
      option.textContent = subject.name;
      select.appendChild(option);
    });
    if (current && Array.from(select.options).some((option) => option.value === current)) {
      select.value = current;
    } else if (!focusTouched && window.innerWidth < 800 && select.options[1]) {
      select.value = select.options[1].value;
    } else {
      select.value = "all";
    }
  }

  function renderGrid() {
    if (!payload) return;
    const root = $("cg-grid");
    root.innerHTML = "";
    const search = ($("cg-search").value || "").trim().toLowerCase();
    const type = $("cg-type").value;
    const status = $("cg-status-filter").value;
    const needs = $("cg-needs").checked;
    const subjects = visibleSubjects();
    const payers = (payload.payers || []).filter((payer) => {
      if (payer.archived) return false;
      if (type && payer.type !== type) return false;
      if (search && (payer.name || "").toLowerCase().indexOf(search) === -1) return false;
      if (!status && !needs) return true;
      return subjects.some((subject) => {
        const cell = cellFor(payer.id, subject.id);
        if (needs && !needsAction(cell)) return false;
        if (!status) return true;
        if (status === "suggested") return cell && cell.status === "unknown" && cell.suggestion_reason;
        return cell && cell.status === status;
      });
    });
    const table = document.createElement("table");
    table.className = "cg-grid";
    const head = document.createElement("tr");
    const corner = document.createElement("th");
    corner.textContent = "Payer";
    head.appendChild(corner);
    subjects.forEach((subject) => {
      const th = document.createElement("th");
      th.textContent = subject.name;
      head.appendChild(th);
    });
    const thead = document.createElement("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    const body = document.createElement("tbody");
    payers.forEach((payer) => {
      const tr = document.createElement("tr");
      const name = document.createElement("td");
      const title = document.createElement("strong");
      title.textContent = payer.name;
      name.appendChild(title);
      const meta = document.createElement("div");
      meta.className = "meta";
      const licenses = (payer.accepts_licenses || []).join(", ") || "licenses not set";
      meta.textContent = payer.type + " · " + licenses;
      name.appendChild(meta);
      tr.appendChild(name);
      subjects.forEach((subject) => {
        const td = document.createElement("td");
        const cell = cellFor(payer.id, subject.id);
        const look = cellLabel(cell);
        td.className = look.cls;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "cg-cell";
        button.textContent = look.text;
        button.title = look.title;
        if (look.warn) {
          const mark = document.createElement("span");
          mark.className = "cg-warn";
          mark.textContent = "!";
          mark.title = look.warn;
          button.appendChild(document.createTextNode(" "));
          button.appendChild(mark);
        }
        button.addEventListener("click", () => openDrawer(payer, subject, cell));
        td.appendChild(button);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    root.appendChild(table);
    if (!payers.length) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "No payers yet. Add one, or import the three CSVs.";
      root.appendChild(empty);
    }
  }

  function secretRow(label, masked, field, subjectId, payerId) {
    const wrap = document.createElement("div");
    wrap.style.margin = "0.35rem 0";
    const name = document.createElement("span");
    name.textContent = label + " ";
    const value = document.createElement("span");
    value.className = "cg-secret";
    value.textContent = masked || "—";
    value.setAttribute("data-masked", masked || "—");
    wrap.appendChild(name);
    wrap.appendChild(value);
    if (payload.can_sensitive && masked) {
      const eye = document.createElement("button");
      eye.type = "button";
      eye.className = "ghost";
      eye.textContent = "Show";
      const remask = () => {
        value.textContent = value.getAttribute("data-masked") || "";
        value.removeAttribute("data-revealed");
        eye.textContent = "Show";
      };
      eye.addEventListener("click", async () => {
        if (value.getAttribute("data-revealed") === "1") {
          remask();
          return;
        }
        try {
          const data = await bridge().api("/credentialing/reveal", {
            method: "POST",
            json: { subject_id: subjectId, field: field, payer_id: payerId || "" },
          });
          value.textContent = data.value || "—";
          value.setAttribute("data-revealed", "1");
          eye.textContent = "Hide";
          const timer = window.setTimeout(remask, 30000);
          timers.push(timer);
          refreshAudit(subjectId);
        } catch (error) {
          setMessage(errText(error), "error");
        }
      });
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "ghost";
      copy.textContent = "Copy";
      copy.addEventListener("click", async () => {
        try {
          const data = await bridge().api("/credentialing/copy", {
            method: "POST",
            json: { subject_id: subjectId, field: field, payer_id: payerId || "" },
          });
          await navigator.clipboard.writeText(data.value || "");
          setMessage("Copied", "ok");
          refreshAudit(subjectId);
        } catch (error) {
          setMessage(errText(error), "error");
        }
      });
      wrap.appendChild(eye);
      wrap.appendChild(copy);
    }
    return wrap;
  }

  async function refreshAudit(subjectId) {
    if (!subjectId) return;
    const log = document.querySelector('[data-audit-for="' + subjectId + '"]');
    if (!log) return;
    try {
      const data = await bridge().api("/credentialing/audit?subject_id=" + encodeURIComponent(subjectId));
      const events = data.events || [];
      log.textContent = events.length
        ? events.map((event) => event.at + " " + event.actor + " " + event.action + " " + event.field).join("\n")
        : "No reveals or copies yet.";
    } catch (error) {
      log.textContent = errText(error);
    }
  }

  function historyBlock(events) {
    const pre = document.createElement("div");
    pre.className = "cg-history";
    if (!events || !events.length) {
      pre.textContent = "No history yet.";
      return pre;
    }
    pre.textContent = events.map((event) => {
      const bits = Object.keys(event.changes || {}).map((key) => key + " " + JSON.stringify(event.changes[key]));
      return (event.at || "") + " " + (event.by || "") + " " + (event.source || "") + " " + bits.join("; ");
    }).join("\n");
    return pre;
  }

  function openDrawer(payer, subject, cell) {
    const drawer = $("cg-drawer");
    drawer.classList.remove("hidden");
    drawer.innerHTML = "";
    const title = document.createElement("h3");
    title.textContent = payer.name + " · " + subject.name;
    drawer.appendChild(title);
    if (cell && cell.suggestion_reason) {
      const reason = document.createElement("p");
      reason.textContent = "Suggested not eligible: " + cell.suggestion_reason + " Overriding it requires a note.";
      drawer.appendChild(reason);
    }
    if (cell && cell.license_warning) {
      const warn = document.createElement("p");
      warn.textContent = cell.license_warning;
      drawer.appendChild(warn);
    }
    const status = document.createElement("select");
    STATUSES.forEach((pair) => {
      const option = document.createElement("option");
      option.value = pair[0];
      option.textContent = pair[1];
      status.appendChild(option);
    });
    status.value = (cell && cell.status) || "unknown";
    const submitted = field("Submitted on", (cell && cell.submitted_on) || "");
    const effective = field("Effective on", (cell && cell.effective_on) || "");
    const follow = field("Next follow-up", (cell && cell.next_follow_up) || "");
    const provider = field("Payer provider ID", "");
    provider.input.placeholder = (cell && cell.payer_provider_id_masked) || "Leave blank to keep";
    const note = document.createElement("textarea");
    note.value = (cell && cell.note) || "";
    note.rows = 3;
    drawer.appendChild(labeled("Status", status));
    drawer.appendChild(submitted.wrap);
    drawer.appendChild(effective.wrap);
    drawer.appendChild(follow.wrap);
    drawer.appendChild(provider.wrap);
    drawer.appendChild(labeled("Note", note));
    if (cell && cell.payer_provider_id_masked) {
      drawer.appendChild(secretRow("Payer provider ID", cell.payer_provider_id_masked, "payer_provider_id", subject.id, payer.id));
    }
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save changes";
    save.disabled = !payload.can_manage;
    const result = document.createElement("p");
    result.className = "status";
    save.addEventListener("click", async () => {
      result.className = "status";
      result.textContent = "Saving…";
      setMessage("Saving…");
      const body = {
        subject_id: subject.id,
        payer_id: payer.id,
        status: status.value,
        submitted_on: submitted.input.value.trim(),
        effective_on: effective.input.value.trim(),
        next_follow_up: follow.input.value.trim(),
        note: note.value,
        payer_provider_id: provider.input.value.trim() ? provider.input.value.trim() : null,
      };
      try {
        await bridge().api("/credentialing/enrollments", { method: "PUT", json: body });
        await reload();
        const updated = cellFor(payer.id, subject.id);
        const history = drawer.querySelector(".cg-history");
        if (history) history.replaceWith(historyBlock(updated && updated.history));
        refreshAudit(subject.id);
        result.className = "status ok";
        result.textContent = "Saved";
        setMessage("Saved", "ok");
        result.scrollIntoView({ block: "center" });
      } catch (error) {
        result.className = "status error";
        result.textContent = errText(error);
        setMessage(errText(error), "error");
      }
    });
    drawer.appendChild(save);
    drawer.appendChild(result);
    const historyTitle = document.createElement("h4");
    historyTitle.textContent = "History";
    drawer.appendChild(historyTitle);
    drawer.appendChild(historyBlock(cell && cell.history));
  }

  function field(label, value) {
    const input = document.createElement("input");
    input.type = "text";
    input.value = value || "";
    return { wrap: labeled(label, input), input: input };
  }

  function labeled(text, control) {
    const wrap = document.createElement("label");
    wrap.textContent = text;
    wrap.appendChild(control);
    return wrap;
  }

  async function reload() {
    payload = await bridge().api("/credentialing/grid");
    renderFocus();
    renderGrid();
    renderProfiles();
    renderPayers();
    $("cg-tab-import").classList.toggle("hidden", !payload.can_manage);
    $("cg-tab-payers").classList.toggle("hidden", !payload.can_payers && !payload.can_manage);
  }

  function renderProfiles() {
    const root = $("cg-profiles-section");
    root.innerHTML = "";
    const picker = document.createElement("select");
    picker.id = "cg-profile-pick";
    activeSubjects().forEach((subject) => {
      const option = document.createElement("option");
      option.value = subject.id;
      option.textContent = subject.name + " (" + subject.kind + ")";
      picker.appendChild(option);
    });
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "New profile";
    picker.appendChild(blank);
    root.appendChild(labeled("Profile", picker));
    const form = document.createElement("div");
    form.className = "cg-form";
    root.appendChild(form);
    const draw = () => {
      const subject = (payload.subjects || []).find((item) => item.id === picker.value) || {
        id: "",
        name: "",
        kind: "clinician",
        license_type: "",
        hire_date: "",
        device: "",
        legal_name: "",
        service_address: "",
        license_state: "",
        license_expires_on: "",
        active: true,
      };
      form.innerHTML = "";
      const name = field("Name", subject.name);
      const kind = document.createElement("select");
      ["practice", "clinician"].forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        kind.appendChild(option);
      });
      kind.value = subject.kind || "clinician";
      const license = field("License type", subject.license_type);
      const hire = field("Hire date", subject.hire_date);
      const device = field("Device", subject.device);
      const legal = field("Legal name", subject.legal_name);
      const address = field("Service address", subject.service_address);
      const npi = field("Replace NPI", "");
      const tin = field("Replace TIN", "");
      const licenseNo = field("Replace license #", "");
      const caqh = field("Replace CAQH ID", "");
      form.appendChild(name.wrap);
      form.appendChild(labeled("Kind", kind));
      form.appendChild(license.wrap);
      form.appendChild(hire.wrap);
      form.appendChild(device.wrap);
      form.appendChild(legal.wrap);
      form.appendChild(address.wrap);
      if (subject.id) {
        form.appendChild(secretRow("NPI", subject.npi_masked, "npi", subject.id, ""));
        form.appendChild(secretRow("TIN", subject.tin_masked, "tin", subject.id, ""));
        form.appendChild(secretRow("License #", subject.license_number_masked, "license_number", subject.id, ""));
        form.appendChild(secretRow("CAQH ID", subject.caqh_id_masked, "caqh_id", subject.id, ""));
      }
      form.appendChild(npi.wrap);
      form.appendChild(tin.wrap);
      form.appendChild(licenseNo.wrap);
      form.appendChild(caqh.wrap);
      const save = document.createElement("button");
      save.type = "button";
      save.className = "primary";
      save.textContent = "Save changes";
      save.disabled = !payload.can_manage;
      save.addEventListener("click", async () => {
        setMessage("Saving…");
        const body = {
          id: subject.id,
          name: name.input.value.trim(),
          kind: kind.value,
          license_type: license.input.value.trim(),
          hire_date: hire.input.value.trim(),
          device: device.input.value.trim(),
          legal_name: legal.input.value.trim(),
          service_address: address.input.value.trim(),
          active: true,
        };
        if (npi.input.value.trim()) body.npi = npi.input.value.trim();
        if (tin.input.value.trim()) body.tin = tin.input.value.trim();
        if (licenseNo.input.value.trim()) body.license_number = licenseNo.input.value.trim();
        if (caqh.input.value.trim()) body.caqh_id = caqh.input.value.trim();
        try {
          await bridge().api("/credentialing/subjects", { method: "PUT", json: body });
          setMessage("Saved", "ok");
          await reload();
          refreshAudit(subject.id);
        } catch (error) {
          setMessage(errText(error), "error");
        }
      });
      form.appendChild(save);
      if (subject.id && payload.can_manage) {
        const archive = document.createElement("button");
        archive.type = "button";
        archive.className = "ghost";
        archive.textContent = "Remove from grid";
        archive.addEventListener("click", () => {
          const box = confirmBox(
            "Remove " + subject.name + " from the grid? They will be hidden. History is kept.",
            async () => {
              await bridge().api("/credentialing/subjects/" + subject.id + "/archive", { method: "POST" });
              setMessage("Saved", "ok");
              await reload();
            }
          );
          form.appendChild(box);
        });
        form.appendChild(archive);
      }
      if (subject.id && payload.can_sensitive) {
        const log = document.createElement("div");
        log.className = "cg-history";
        log.setAttribute("data-audit-for", subject.id);
        log.textContent = "Loading reveal log…";
        form.appendChild(log);
        refreshAudit(subject.id);
      }
    };
    picker.addEventListener("change", draw);
    if (picker.options.length) draw();
    const removed = (payload.subjects || []).filter((item) => item.archived);
    if (removed.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Removed (" + removed.length + ")";
      details.appendChild(summary);
      removed.forEach((subject) => {
        const row = document.createElement("div");
        row.className = "due-row";
        const label = document.createElement("span");
        label.textContent = subject.name;
        row.appendChild(label);
        if (payload.can_manage) {
          const restore = document.createElement("button");
          restore.type = "button";
          restore.className = "ghost";
          restore.textContent = "Restore";
          restore.addEventListener("click", async () => {
            try {
              await bridge().api("/credentialing/subjects/" + subject.id + "/restore", { method: "POST" });
              setMessage("Saved", "ok");
              await reload();
            } catch (error) {
              setMessage(errText(error), "error");
            }
          });
          row.appendChild(restore);
        }
        details.appendChild(row);
      });
      root.appendChild(details);
    }
  }

  function confirmBox(message, onYes) {
    const box = document.createElement("div");
    box.className = "cg-confirm";
    const text = document.createElement("p");
    text.textContent = message;
    const yes = document.createElement("button");
    yes.type = "button";
    yes.className = "primary";
    yes.textContent = "Confirm";
    const no = document.createElement("button");
    no.type = "button";
    no.className = "ghost";
    no.textContent = "Cancel";
    no.addEventListener("click", () => box.remove());
    yes.addEventListener("click", async () => {
      yes.disabled = true;
      try {
        await onYes();
        box.remove();
      } catch (error) {
        yes.disabled = false;
        setMessage(errText(error), "error");
      }
    });
    box.appendChild(text);
    box.appendChild(yes);
    box.appendChild(no);
    return box;
  }

  function renderPayers() {
    const root = $("cg-payers-section");
    root.innerHTML = "";
    const add = document.createElement("button");
    add.type = "button";
    add.id = "cg-add-payer";
    add.className = "primary";
    add.textContent = "Add payer";
    add.disabled = !(payload.can_manage && payload.can_payers);
    root.appendChild(add);
    const form = document.createElement("div");
    form.className = "cg-form hidden";
    form.id = "cg-payer-form";
    const name = field("Payer", "");
    const type = document.createElement("select");
    TYPES.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      type.appendChild(option);
    });
    const family = field("Family members (; separated)", "");
    const licenses = field("Accepts licenses (; separated, ALL, or masters)", "");
    const notes = field("Notes", "");
    const parent = field("Parent payer", "");
    const counties = field("Counties (; separated)", "");
    form.appendChild(name.wrap);
    form.appendChild(labeled("Type", type));
    form.appendChild(family.wrap);
    form.appendChild(licenses.wrap);
    form.appendChild(notes.wrap);
    form.appendChild(parent.wrap);
    form.appendChild(counties.wrap);
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save changes";
    save.disabled = !(payload.can_manage && payload.can_payers);
    save.addEventListener("click", async () => {
      setMessage("Saving…");
      const body = {
        name: name.input.value.trim(),
        type: type.value,
        family_members: splitList(family.input.value),
        accepts_licenses: splitList(licenses.input.value),
        notes: notes.input.value.trim(),
        parent_payer: parent.input.value.trim(),
        counties: splitList(counties.input.value),
      };
      const editing = form.dataset.id || "";
      try {
        await bridge().api(editing ? "/credentialing/payers/" + editing : "/credentialing/payers", {
          method: editing ? "PUT" : "POST",
          json: body,
        });
        form.dataset.id = "";
        form.classList.add("hidden");
        setMessage("Saved", "ok");
        await reload();
      } catch (error) {
        setMessage(errText(error), "error");
      }
    });
    form.appendChild(save);
    root.appendChild(form);
    const clearForm = () => {
      form.dataset.id = "";
      name.input.value = "";
      family.input.value = "";
      licenses.input.value = "";
      notes.input.value = "";
      parent.input.value = "";
      counties.input.value = "";
      type.selectedIndex = 0;
    };
    add.addEventListener("click", () => {
      clearForm();
      form.classList.remove("hidden");
      name.input.focus();
    });
    const fillForm = (payer) => {
      name.input.value = payer.name;
      type.value = payer.type;
      family.input.value = (payer.family_members || []).join("; ");
      licenses.input.value = (payer.accepts_licenses || []).join("; ");
      notes.input.value = payer.notes || "";
      parent.input.value = payer.parent_payer || "";
      counties.input.value = (payer.counties || []).join("; ");
      form.dataset.id = payer.id;
      form.classList.remove("hidden");
    };
    const active = (payload.payers || []).filter((payer) => !payer.archived);
    const archived = (payload.payers || []).filter((payer) => payer.archived);
    active.forEach((payer) => root.appendChild(payerRow(payer, false)));
    if (archived.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Archived (" + archived.length + ")";
      details.appendChild(summary);
      archived.forEach((payer) => details.appendChild(payerRow(payer, true)));
      root.appendChild(details);
    }

    function payerRow(payer, isArchived) {
      const row = document.createElement("div");
      row.className = "due-row";
      const label = document.createElement("span");
      label.textContent = payer.name + " · " + payer.type;
      row.appendChild(label);
      if (payload.can_manage && payload.can_payers) {
        if (isArchived) {
          const restore = document.createElement("button");
          restore.type = "button";
          restore.className = "ghost";
          restore.textContent = "Restore";
          restore.addEventListener("click", async () => {
            try {
              await bridge().api("/credentialing/payers/" + payer.id + "/restore", { method: "POST" });
              setMessage("Saved", "ok");
              await reload();
            } catch (error) {
              setMessage(errText(error), "error");
            }
          });
          row.appendChild(restore);
        } else {
          const archive = document.createElement("button");
          archive.type = "button";
          archive.className = "ghost";
          archive.textContent = "Archive";
          archive.addEventListener("click", () => {
            row.appendChild(confirmBox(
              "Archive " + payer.name + "? It will be hidden from the grid. History is kept.",
              async () => {
                await bridge().api("/credentialing/payers/" + payer.id + "/archive", { method: "POST" });
                setMessage("Saved", "ok");
                await reload();
              }
            ));
          });
          const edit = document.createElement("button");
          edit.type = "button";
          edit.className = "ghost";
          edit.textContent = "Edit";
          edit.addEventListener("click", () => fillForm(payer));
          row.appendChild(archive);
          row.appendChild(edit);
        }
      }
      return row;
    }
  }

  function splitList(value) {
    return (value || "").split(";").map((item) => item.trim()).filter(Boolean);
  }

  function showSection(name) {
    ["grid", "profiles", "payers", "import"].forEach((item) => {
      $("cg-" + item + "-section").classList.toggle("hidden", item !== name);
      const tab = $("cg-tab-" + item);
      const selected = item === name;
      tab.classList.toggle("primary", selected);
      tab.classList.toggle("ghost", !selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
    });
  }

  async function postFiles(path) {
    const data = new FormData();
    const payers = $("cg-file-payers").files[0];
    const people = $("cg-file-people").files[0];
    const grid = $("cg-file-grid").files[0];
    if (payers) data.append("payers", payers);
    if (people) data.append("people", people);
    if (grid) data.append("grid", grid);
    const headers = {};
    if (bridge().state.token) headers.Authorization = "Bearer " + bridge().state.token;
    const response = await fetch(path, { method: "POST", headers: headers, body: data, credentials: "include" });
    const text = await response.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch (error) { body = { detail: text }; }
    if (!response.ok) {
      const detail = body && body.detail;
      const message = typeof detail === "string" ? detail : (detail && detail.errors && detail.errors.join(" ")) || response.statusText;
      const failure = new Error(message || "Import failed");
      failure.payload = body;
      throw failure;
    }
    return body;
  }

  function renderPreview(body) {
    const root = $("cg-import-preview");
    root.innerHTML = "";
    const errors = (body && body.errors) || [];
    const warnings = (body && body.warnings) || [];
    const changes = (body && body.changes) || [];
    if (body && body.summary) {
      const summary = document.createElement("p");
      summary.id = "cg-import-summary";
      summary.textContent = body.summary;
      root.appendChild(summary);
    }
    const block = document.createElement("div");
    block.className = "cg-history";
    const lines = [];
    errors.forEach((item) => lines.push("Error: " + item));
    warnings.forEach((item) => lines.push("Warning: " + item));
    changes.forEach((item) => lines.push(item));
    block.textContent = lines.join("\n") || "Nothing to change.";
    root.appendChild(block);
    $("cg-commit").disabled = !payload.can_manage || errors.length > 0 || (!changes.length && !warnings.length);
  }

  async function download(kind) {
    const headers = {};
    if (bridge().state.token) headers.Authorization = "Bearer " + bridge().state.token;
    const response = await fetch("/credentialing/export/" + kind, { headers: headers, credentials: "include" });
    if (!response.ok) {
      setMessage("Export failed", "error");
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = kind + ".csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function openCredentialing() {
    const ui = bridge();
    ui.show("credentialing-panel");
    window.history.replaceState({}, "", "/#credentialing");
    setMessage("Loading…");
    try {
      await reload();
      showSection("grid");
      setMessage("");
    } catch (error) {
      setMessage(errText(error), "error");
    }
  }

  window.openCredentialing = openCredentialing;

  $("btn-credentialing-home").addEventListener("click", () => {
    window.location.href = "/";
  });
  $("cg-tab-grid").addEventListener("click", () => showSection("grid"));
  $("cg-tab-profiles").addEventListener("click", () => showSection("profiles"));
  $("cg-tab-payers").addEventListener("click", () => showSection("payers"));
  $("cg-tab-import").addEventListener("click", () => showSection("import"));
  ["cg-search", "cg-type", "cg-status-filter", "cg-needs", "cg-focus"].forEach((id) => {
    $(id).addEventListener("input", renderGrid);
    $(id).addEventListener("change", () => {
      if (id === "cg-focus") focusTouched = true;
      renderGrid();
    });
  });
  $("cg-preview").addEventListener("click", async () => {
    setMessage("Checking the files…");
    try {
      const body = await postFiles("/credentialing/import/preview");
      renderPreview(body);
      setMessage(body.errors && body.errors.length ? "Fix the errors before confirming." : "Preview ready. Nothing is saved yet.", body.errors && body.errors.length ? "error" : "ok");
    } catch (error) {
      setMessage(errText(error), "error");
    }
  });
  $("cg-commit").addEventListener("click", async () => {
    setMessage("Saving the import…");
    try {
      const body = await postFiles("/credentialing/import/commit");
      renderPreview(body);
      setMessage("Saved", "ok");
      await reload();
      showSection("grid");
    } catch (error) {
      const detail = error.payload && error.payload.detail;
      if (detail && detail.errors) renderPreview(detail);
      setMessage(errText(error), "error");
    }
  });
  $("cg-export-payers").addEventListener("click", () => download("payers"));
  $("cg-export-people").addEventListener("click", () => download("people"));
  $("cg-export-grid").addEventListener("click", () => download("grid"));
})();
