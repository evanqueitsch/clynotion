/* Credentialing grid. Full IDs are fetched one at a time and never kept in the page payload. */
(function () {
  const TYPES = ["commercial", "medicaid_mco", "chip", "medicare", "tricare", "eap"];
  const PRACTICE_NETWORKS = [
    ["unknown", "Unknown"],
    ["in_network", "In network"],
    ["needs_ma_first", "Need MA first"],
    ["in_progress", "In progress"],
  ];
  let payerEditRequest = "";
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
    let warn = cell && cell.display === "warning" ? (cell.license_warning || "") : "";
    if (cell && cell.follow_up_overdue) {
      warn = (warn ? warn + " " : "") + "Follow-up due " + (formatUsDate(cell.follow_up_on) || cell.follow_up_on || "");
    }
    if (!cell || cell.status === "unknown" || cell.display === "suggested") {
      if (cell && (cell.display === "suggested" || cell.suggestion_reason)) {
        return { text: "X", cls: "cg-suggested", title: cell.suggestion_reason || "Suggested not eligible", warn: "" };
      }
      return { text: "", cls: "", title: "Unknown", warn: "" };
    }
    if (cell.status === "in_network") return { text: "Y", cls: "cg-y", title: "In network", warn: warn };
    if (cell.status === "not_eligible") return { text: "X", cls: "cg-x", title: "Not eligible", warn: "" };
    if (cell.status === "pending") {
      return { text: formatUsDate(cell.submitted_on) || cell.submitted_on || "Pending", cls: "cg-pending", title: "Pending", warn: warn };
    }
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
    const wrap = document.createElement("div");
    wrap.className = "cg-history-block";
    const count = events && events.length ? events.length : 0;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost history-toggle";
    button.textContent = count ? "Change history (" + count + ")" : "Change history";
    button.setAttribute("aria-expanded", "false");
    const panel = document.createElement("div");
    panel.className = "cg-history hidden";
    if (!count) {
      panel.textContent = "No changes yet.";
    } else {
      events.forEach((event) => {
        const row = document.createElement("p");
        row.textContent = event.line || "Change recorded.";
        panel.appendChild(row);
      });
    }
    button.addEventListener("click", () => {
      const hidden = panel.classList.toggle("hidden");
      button.setAttribute("aria-expanded", hidden ? "false" : "true");
    });
    wrap.appendChild(button);
    wrap.appendChild(panel);
    return wrap;
  }

  const DATE_HINT = "Use Nov 9, 2026 as 11/09/2026.";

  function normalizeCalendarDate(value) {
    const raw = String(value == null ? "" : value).trim();
    if (!raw) return "";
    let match = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].+)?$/);
    if (!match) match = raw.match(/^(\d{4})[./](\d{1,2})[./](\d{1,2})$/);
    if (match) return ymd(Number(match[1]), Number(match[2]), Number(match[3]));
    match = raw.match(/^(\d{1,2})[./](\d{1,2})[./](\d{2}|\d{4})$/) || raw.match(/^(\d{1,2})-(\d{1,2})-(\d{2}|\d{4})$/);
    if (!match) return null;
    return ymd(expandYear(match[3]), Number(match[1]), Number(match[2]));
  }

  function expandYear(token) {
    const number = Number(token);
    if (String(token).length === 2) return number <= 68 ? 2000 + number : 1900 + number;
    return number;
  }

  function ymd(year, month, day) {
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    const dt = new Date(Date.UTC(year, month - 1, day));
    if (dt.getUTCFullYear() !== year || dt.getUTCMonth() !== month - 1 || dt.getUTCDate() !== day) return null;
    return String(year) + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0");
  }

  function formatUsDate(value) {
    const iso = normalizeCalendarDate(value);
    if (iso === "") return "";
    if (!iso) return String(value || "").trim();
    const parts = iso.split("-");
    return parts[1] + "/" + parts[2] + "/" + parts[0];
  }

  function readDate(input) {
    const raw = String(input.value || "").trim();
    if (!raw) return "";
    const iso = normalizeCalendarDate(raw);
    if (!iso) throw new Error(DATE_HINT);
    return iso;
  }

  function openDrawer(payer, subject, cell) {
    const drawer = $("cg-drawer");
    drawer.classList.remove("hidden");
    drawer.innerHTML = "";
    const title = document.createElement("h3");
    title.textContent = payer.name + " · " + subject.name;
    drawer.appendChild(title);
    appendPlaybook(drawer, payer);
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
    const submitted = fieldDate("Submitted on", (cell && cell.submitted_on) || "", "", "");
    const effective = fieldDate("Effective on", (cell && cell.effective_on) || "", "", "");
    const expires = fieldDate("Expiration date", (cell && cell.expires_on) || "", "", "");
    const follow = fieldDate("Follow-up date", (cell && cell.next_follow_up) || "", "", "90 days before expiration unless you change it.");
    const provider = field("Payer provider ID", "");
    provider.input.placeholder = (cell && cell.payer_provider_id_masked) || "Leave blank to keep";
    const note = document.createElement("textarea");
    note.value = (cell && cell.note) || "";
    note.rows = 3;
    const appNote = document.createElement("p");
    appNote.className = "meta";
    const expireBox = document.createElement("fieldset");
    expireBox.className = "cg-expire";
    const legend = document.createElement("legend");
    legend.textContent = "Credential expiration";
    expireBox.appendChild(legend);
    const yes = radioChoice("Credentials expire", "yes");
    const no = radioChoice("No expiration", "no");
    expireBox.appendChild(yes.wrap);
    expireBox.appendChild(no.wrap);
    const initial = expirationChoice(cell, payer);
    if (initial === "yes") yes.input.checked = true;
    if (initial === "no") no.input.checked = true;
    drawer.appendChild(labeled("Status", status));
    drawer.appendChild(submitted.wrap);
    drawer.appendChild(effective.wrap);
    drawer.appendChild(appNote);
    drawer.appendChild(expireBox);
    drawer.appendChild(expires.wrap);
    drawer.appendChild(follow.wrap);
    drawer.appendChild(provider.wrap);
    drawer.appendChild(labeled("Note", note));
    if (cell && cell.payer_provider_id_masked) {
      drawer.appendChild(secretRow("Payer provider ID", cell.payer_provider_id_masked, "payer_provider_id", subject.id, payer.id));
    }
    function selectedExpiration() {
      if (yes.input.checked) return "yes";
      if (no.input.checked) return "no";
      return "";
    }
    function refreshExpiration() {
      const show = selectedExpiration() === "yes";
      expires.wrap.classList.toggle("hidden", !show);
      follow.wrap.classList.toggle("hidden", !show);
      if (show && !follow.input.value.trim()) {
        const iso = normalizeCalendarDate(expires.input.value);
        if (iso) follow.input.value = formatUsDate(addDays(iso, -90));
      }
    }
    function refreshApplication() {
      const pending = status.value === "pending" || status.value === "submitted";
      const iso = normalizeCalendarDate(submitted.input.value);
      const days = Number(payer.follow_up_days) > 0 ? Number(payer.follow_up_days) : 14;
      if (!pending || !iso) {
        appNote.textContent = "";
        appNote.classList.add("hidden");
        return;
      }
      appNote.classList.remove("hidden");
      appNote.textContent = "Application follow-up " + formatUsDate(addDays(iso, days)) + ", from the submitted date.";
    }
    yes.input.addEventListener("change", refreshExpiration);
    no.input.addEventListener("change", refreshExpiration);
    expires.input.addEventListener("change", () => {
      if (follow.input.value.trim()) return;
      refreshExpiration();
    });
    status.addEventListener("change", refreshApplication);
    submitted.input.addEventListener("change", refreshApplication);
    refreshExpiration();
    refreshApplication();
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
      let body;
      try {
        const choice = selectedExpiration();
        if (!choice) throw new Error("Choose whether credentials expire.");
        body = {
          subject_id: subject.id,
          payer_id: payer.id,
          status: status.value,
          submitted_on: readDate(submitted.input),
          effective_on: readDate(effective.input),
          has_expiration: choice === "yes",
          note: note.value,
          payer_provider_id: provider.input.value.trim() ? provider.input.value.trim() : null,
        };
        if (choice === "yes") {
          body.expires_on = readDate(expires.input);
          if (!body.expires_on) throw new Error("Enter an expiration date. " + DATE_HINT);
          body.next_follow_up = readDate(follow.input) || addDays(body.expires_on, -90);
        } else {
          body.expires_on = "";
          body.next_follow_up = "";
        }
      } catch (error) {
        result.className = "status error";
        result.textContent = errText(error);
        setMessage(errText(error), "error");
        return;
      }
      try {
        await bridge().api("/credentialing/enrollments", { method: "PUT", json: body });
        await reload();
        const updated = cellFor(payer.id, subject.id);
        const history = drawer.querySelector(".cg-history-block");
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
    drawer.appendChild(historyBlock(cell && cell.history));
  }

  function networkLabel(value) {
    const pair = PRACTICE_NETWORKS.find((item) => item[0] === value);
    return pair ? pair[1] : "";
  }

  function linkify(text) {
    const span = document.createElement("span");
    span.className = "cg-howto";
    const source = String(text || "");
    const re = /https?:\/\/[^\s<>"']+/gi;
    let last = 0;
    let match;
    while ((match = re.exec(source))) {
      span.appendChild(document.createTextNode(source.slice(last, match.index)));
      const raw = match[0];
      const href = raw.replace(/[),.;:]+$/g, "");
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.textContent = href;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      span.appendChild(anchor);
      if (href.length < raw.length) {
        span.appendChild(document.createTextNode(raw.slice(href.length)));
      }
      last = match.index + raw.length;
    }
    span.appendChild(document.createTextNode(source.slice(last)));
    return span;
  }

  function playbookRow(label, value) {
    const row = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = label;
    row.appendChild(strong);
    row.appendChild(document.createTextNode(" "));
    if (typeof value === "string") {
      row.appendChild(document.createTextNode(value));
    } else {
      row.appendChild(value);
    }
    return row;
  }

  function appendPlaybook(drawer, payer) {
    const lines = [];
    const payerId = String((payer && payer.electronic_payer_id) || "").trim();
    if (payerId) lines.push(playbookRow("Electronic payer ID", payerId));
    const network = payer && payer.practice_network;
    if (network && network !== "unknown") {
      lines.push(playbookRow("Practice network", networkLabel(network) || network));
    }
    const networkNote = String((payer && payer.practice_network_note) || "").trim();
    if (networkNote) lines.push(playbookRow("Network note", networkNote));
    const licenses = ((payer && payer.accepts_licenses) || []).map((item) => String(item).trim()).filter(Boolean);
    if (licenses.length) lines.push(playbookRow("Licenses", licenses.join(", ")));
    const notes = String((payer && payer.notes) || "").trim();
    if (notes) lines.push(playbookRow("How-to", linkify(notes)));
    const canEdit = !!(payload && payload.can_manage && payload.can_payers);
    if (!lines.length && !canEdit) return;
    const panel = document.createElement("section");
    panel.id = "cg-playbook";
    panel.className = "cg-playbook";
    if (lines.length) {
      const heading = document.createElement("h4");
      heading.textContent = "Payer playbook";
      panel.appendChild(heading);
      lines.forEach((line) => panel.appendChild(line));
    }
    if (canEdit) {
      const edit = document.createElement("button");
      edit.type = "button";
      edit.id = "cg-edit-payer";
      edit.className = "ghost";
      edit.textContent = "Edit payer";
      edit.addEventListener("click", () => openPayerEditor(payer.id));
      panel.appendChild(edit);
    }
    drawer.appendChild(panel);
  }

  function openPayerEditor(payerId) {
    payerEditRequest = payerId;
    showSection("payers");
    renderPayers();
    const form = $("cg-payer-form");
    if (form) form.scrollIntoView({ block: "nearest" });
  }

  function expirationChoice(cell, payer) {
    if (cell && cell.enrollment_saved) return cell.has_expiration ? "yes" : "no";
    if (payer && payer.credentials_expire === true) return "yes";
    if (payer && payer.credentials_expire === false) return "no";
    return "";
  }

  function radioChoice(text, value) {
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "cg-expire";
    input.value = value;
    const wrap = document.createElement("label");
    wrap.className = "cg-choice";
    wrap.appendChild(input);
    wrap.appendChild(document.createTextNode(text));
    return { wrap: wrap, input: input };
  }

  function area(label, value) {
    const input = document.createElement("textarea");
    input.rows = 4;
    input.className = "cg-howto-input";
    input.value = value || "";
    return { wrap: labeled(label, input), input: input };
  }

  function field(label, value, type) {
    const input = document.createElement("input");
    input.type = "text";
    if (type === "date") {
      input.placeholder = "MM/DD/YYYY";
      input.autocomplete = "off";
      input.value = formatUsDate(value);
    } else {
      input.value = value || "";
    }
    return { wrap: labeled(label, input), input: input };
  }

  function addDays(iso, days) {
    const parts = String(iso || "").slice(0, 10).split("-");
    if (parts.length !== 3) return "";
    const dt = new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])));
    if (Number.isNaN(dt.getTime())) return "";
    dt.setUTCDate(dt.getUTCDate() + days);
    return dt.toISOString().slice(0, 10);
  }

  function fieldDate(label, value, dueOn, helper) {
    const made = field(label, value, "date");
    if (helper) {
      const help = document.createElement("span");
      help.className = "meta";
      help.textContent = " " + helper;
      made.wrap.appendChild(help);
    }
    if (dueOn) {
      const note = document.createElement("span");
      note.className = "meta";
      note.textContent = " Due " + (formatUsDate(dueOn) || dueOn);
      made.wrap.appendChild(note);
    }
    return made;
  }

  function cycleHelp(days) {
    if (days === 365) return "Due one year after this date.";
    if (days === 730) return "Due two years after this date.";
    if (days === 1095) return "Due 3 years after this date.";
    if (days === 1825) return "Due 5 years after this date.";
    return "Due " + days + " days after this date.";
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
    const previous = ($("cg-profile-pick") && $("cg-profile-pick").value) || "";
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
      const hire = fieldDate("Hire date", subject.hire_date, "", "");
      const device = field("Device", subject.device);
      const expires = fieldDate(
        "License expires on",
        subject.license_expires_on,
        (subject.license_expires_on || "").slice(0, 10),
        "This is the expiration date."
      );
      const caqhDate = fieldDate(
        "CAQH last attested",
        subject.caqh_attested_on,
        subject.caqh_attested_on ? addDays(subject.caqh_attested_on, 120) : "",
        "Due 120 days after this date."
      );
      const legal = field("Legal name", subject.legal_name);
      const address = field("Service address", subject.service_address);
      const clockInputs = {};
      const clockNames = [
        ["license_renewal", "License renewal", 730],
        ["caqh_reattestation", "CAQH re-attestation", 120],
        ["malpractice_rider", "Malpractice rider", 365],
        ["recredentialing", "Recredentialing", 1095],
        ["ma_revalidation", "MA revalidation", 1825],
        ["clinician_exclusion_screening", "Exclusion screening", 30],
      ];
      clockNames.forEach((pair) => {
        const current = (subject.clocks && subject.clocks[pair[0]]) || "";
        clockInputs[pair[0]] = fieldDate(pair[1], current, current ? addDays(current, pair[2]) : "", cycleHelp(pair[2]));
      });
      const npi = field("Replace NPI", "");
      const tin = field("Replace TIN", "");
      const licenseNo = field("Replace license #", "");
      const caqh = field("Replace CAQH ID", "");
      form.appendChild(name.wrap);
      form.appendChild(labeled("Kind", kind));
      form.appendChild(license.wrap);
      const specialties = document.createElement("p");
      specialties.className = "meta";
      const specialtyNames = (subject.specialties || []).filter(Boolean);
      specialties.textContent = specialtyNames.length
        ? "Specialties: " + specialtyNames.join(", ")
        : "Specialties: none yet";
      form.appendChild(specialties);
      form.appendChild(hire.wrap);
      form.appendChild(device.wrap);
      form.appendChild(expires.wrap);
      form.appendChild(caqhDate.wrap);
      Object.keys(clockInputs).forEach((key) => form.appendChild(clockInputs[key].wrap));
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
        let body;
        try {
          body = {
            id: subject.id,
            name: name.input.value.trim(),
            kind: kind.value,
            license_type: license.input.value.trim(),
            hire_date: readDate(hire.input),
            device: device.input.value.trim(),
            legal_name: legal.input.value.trim(),
            service_address: address.input.value.trim(),
            license_expires_on: readDate(expires.input),
            caqh_attested_on: readDate(caqhDate.input),
            clocks: {},
            active: true,
          };
          Object.keys(clockInputs).forEach((key) => {
            body.clocks[key] = readDate(clockInputs[key].input);
          });
        } catch (error) {
          setMessage(errText(error), "error");
          return;
        }
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
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "ghost history-toggle";
        toggle.textContent = "Reveal log";
        toggle.setAttribute("aria-expanded", "false");
        const log = document.createElement("div");
        log.className = "cg-history hidden";
        log.setAttribute("data-audit-for", subject.id);
        log.textContent = "No reveals or copies yet.";
        toggle.addEventListener("click", () => {
          const hidden = log.classList.toggle("hidden");
          toggle.setAttribute("aria-expanded", hidden ? "false" : "true");
        });
        form.appendChild(toggle);
        form.appendChild(log);
        refreshAudit(subject.id);
      }
    };
    if (previous && Array.from(picker.options).some((option) => option.value === previous)) {
      picker.value = previous;
    }
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
    name.input.id = "cg-payer-name";
    const type = document.createElement("select");
    type.id = "cg-payer-type";
    TYPES.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      type.appendChild(option);
    });
    const electronic = field("Electronic payer ID", "");
    electronic.input.id = "cg-payer-electronic-id";
    const network = document.createElement("select");
    network.id = "cg-payer-network";
    PRACTICE_NETWORKS.forEach((pair) => {
      const option = document.createElement("option");
      option.value = pair[0];
      option.textContent = pair[1];
      network.appendChild(option);
    });
    const networkNote = field("Network note", "");
    networkNote.input.id = "cg-payer-network-note";
    const family = field("Family members (; separated)", "");
    const licenses = field("Accepts licenses (; separated, ALL, or masters)", "");
    const notes = area("How-to notes", "");
    notes.input.id = "cg-payer-notes";
    const parent = field("Parent payer", "");
    const counties = field("Counties (; separated)", "");
    const expireDefault = document.createElement("select");
    [["", "No default"], ["yes", "Credentials expire"], ["no", "No expiration"]].forEach((pair) => {
      const option = document.createElement("option");
      option.value = pair[0];
      option.textContent = pair[1];
      expireDefault.appendChild(option);
    });
    form.appendChild(name.wrap);
    form.appendChild(labeled("Type", type));
    form.appendChild(electronic.wrap);
    form.appendChild(labeled("Practice network", network));
    form.appendChild(networkNote.wrap);
    form.appendChild(family.wrap);
    form.appendChild(licenses.wrap);
    form.appendChild(notes.wrap);
    form.appendChild(parent.wrap);
    form.appendChild(counties.wrap);
    form.appendChild(labeled("New enrollments", expireDefault));
    const save = document.createElement("button");
    save.type = "button";
    save.id = "cg-payer-save";
    save.className = "primary";
    save.textContent = "Save changes";
    save.disabled = !(payload.can_manage && payload.can_payers);
    save.addEventListener("click", async () => {
      setMessage("Saving…");
      const body = {
        name: name.input.value.trim(),
        type: type.value,
        electronic_payer_id: electronic.input.value.trim(),
        practice_network: network.value || "unknown",
        practice_network_note: networkNote.input.value.trim(),
        family_members: splitList(family.input.value),
        accepts_licenses: splitList(licenses.input.value),
        notes: notes.input.value.trim(),
        parent_payer: parent.input.value.trim(),
        counties: splitList(counties.input.value),
        credentials_expire: expireDefault.value === "yes" ? true : expireDefault.value === "no" ? false : null,
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
      electronic.input.value = "";
      network.value = "unknown";
      networkNote.input.value = "";
      family.input.value = "";
      licenses.input.value = "";
      notes.input.value = "";
      parent.input.value = "";
      counties.input.value = "";
      type.selectedIndex = 0;
      expireDefault.value = "";
    };
    add.addEventListener("click", () => {
      clearForm();
      form.classList.remove("hidden");
      name.input.focus();
    });
    const fillForm = (payer) => {
      name.input.value = payer.name;
      type.value = payer.type;
      electronic.input.value = payer.electronic_payer_id || "";
      network.value = payer.practice_network || "unknown";
      networkNote.input.value = payer.practice_network_note || "";
      family.input.value = (payer.family_members || []).join("; ");
      licenses.input.value = (payer.accepts_licenses || []).join("; ");
      notes.input.value = payer.notes || "";
      parent.input.value = payer.parent_payer || "";
      counties.input.value = (payer.counties || []).join("; ");
      expireDefault.value = payer.credentials_expire === true ? "yes" : payer.credentials_expire === false ? "no" : "";
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
    if (payerEditRequest) {
      const requested = (payload.payers || []).find((item) => item.id === payerEditRequest);
      payerEditRequest = "";
      if (requested) fillForm(requested);
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
    const params = new URLSearchParams((window.location.hash.split("?")[1]) || "");
    if (!params.get("payer") && !params.get("profile")) {
      window.history.replaceState({}, "", "/#credentialing");
    }
    setMessage("Loading…");
    try {
      await reload();
      if (params.get("profile")) {
        showSection("profiles");
        const picker = document.getElementById("cg-profile-pick");
        if (picker) {
          picker.value = params.get("profile");
          picker.dispatchEvent(new Event("change"));
        }
      } else {
        showSection("grid");
      }
      const payerId = params.get("payer");
      const subjectId = params.get("subject");
      if (payerId && subjectId && payload) {
        const payer = (payload.payers || []).find((item) => item.id === payerId);
        const subject = (payload.subjects || []).find((item) => item.id === subjectId);
        if (payer && subject) openDrawer(payer, subject, cellFor(payerId, subjectId));
      }
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
