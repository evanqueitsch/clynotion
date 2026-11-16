/* Hiring offers. Pay stays in the form response and is not written to the console. */
(function () {
  const FIELDS = [
    ["candidate_full_name", "Full legal name", "text"],
    ["candidate_first_name", "First name", "text"],
    ["candidate_credentials", "Credentials (blank if none)", "text"],
    ["personal_email", "Personal email", "email"],
    ["position_title", "Position (blank uses the default)", "text"],
    ["supervisor_title", "Supervisor title", "text"],
    ["supervisor_name", "Supervisor name", "text"],
    ["hourly_rate", "Hourly rate", "text"],
    ["licensed_rate", "Licensed rate (optional)", "text"],
    ["caseload_range", "Caseload range", "text"],
    ["qsehra_monthly_amount", "QSEHRA monthly amount", "text"],
    ["qsehra_wait_days", "QSEHRA wait days", "text"],
    ["offer_date", "Offer date (MM/DD/YYYY)", "text"],
    ["start_date", "Start date (MM/DD/YYYY)", "text"],
    ["response_deadline", "Response deadline", "text"],
  ];

  function $(id) {
    return document.getElementById(id);
  }

  function ui() {
    return window.clynotion;
  }

  function todayMDY() {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    return month + "/" + day + "/" + now.getFullYear();
  }

  function fieldValue(id) {
    const node = $(id);
    return node ? node.value.trim() : "";
  }

  const REQUIRED = {
    candidate_full_name: true,
    candidate_first_name: true,
    personal_email: true,
    hourly_rate: true,
    caseload_range: true,
    qsehra_monthly_amount: true,
    qsehra_wait_days: true,
    offer_date: true,
    start_date: true,
    response_deadline: true,
  };

  function showStatus(msg, kind) {
    const el = document.getElementById("hiring-status");
    const api = ui();
    if (!el || !api || !api.setStatus) return;
    const text = msg || (kind === "error" ? "Could not save." : "");
    api.setStatus(el, text, text ? (kind || "error") : "");
  }

  function formEl() {
    return document.getElementById("hire-form");
  }

  function fieldNode(name) {
    if (name === "candidate_credentials") return document.getElementById("hire-certs");
    return document.getElementById("hire-" + name);
  }

  function valueOf(name) {
    const direct = fieldNode(name);
    if (direct && "value" in direct) return String(direct.value || "").trim();
    const form = formEl();
    if (!form || !form.elements) return "";
    const el = form.elements.namedItem(name);
    if (!el || el.value == null) return "";
    return String(el.value || "").trim();
  }

  function readForm() {
    const form = formEl();
    if (!form) throw new Error("The offer form is not on the page. Reload and try again.");
    const body = {
      employment_type: valueOf("employment_type"),
      direct_client_access: !!(form.elements.direct_client_access && form.elements.direct_client_access.checked),
    };
    FIELDS.forEach(function (field) {
      body[field[0]] = valueOf(field[0]);
    });
    body.display_name = body.candidate_full_name;
    body.resume_id = valueOf("resume_id");
    return body;
  }

  let currentSettings = {};

  function addDays(mdy, days) {
    const parts = String(mdy || "").split("/");
    if (parts.length !== 3) return "";
    const dt = new Date(Number(parts[2]), Number(parts[0]) - 1, Number(parts[1]));
    if (Number.isNaN(dt.getTime())) return "";
    dt.setDate(dt.getDate() + Number(days || 0));
    const month = String(dt.getMonth() + 1).padStart(2, "0");
    const day = String(dt.getDate()).padStart(2, "0");
    return month + "/" + day + "/" + dt.getFullYear();
  }

  function fillForm(offer) {
    const fields = (offer && offer.fields) || {};
    const settings = currentSettings || {};
    $("hire-type").value = offer ? offer.employment_type : "full-time";
    $("hire-client").checked = offer ? !!offer.direct_client_access : true;
    FIELDS.forEach(function (field) {
      const node = fieldNode(field[0]);
      if (!node) return;
      if (offer) {
        if (field[0] === "personal_email") node.value = offer.personal_email || "";
        else node.value = fields[field[0]] || "";
      } else if (field[0] === "offer_date") {
        node.value = todayMDY();
      } else if (field[0] === "supervisor_title") {
        node.value = settings.offer_supervisor_title_default || "Director";
      } else if (field[0] === "supervisor_name") {
        node.value = settings.offer_supervisor_name_default || "";
      } else if (field[0] === "response_deadline") {
        node.value = addDays(todayMDY(), settings.offer_response_days || 30);
      } else {
        node.value = "";
      }
    });
    applyType();
  }

  function applyType() {
    const part = fieldValue("hire-type") === "part-time";
    ["qsehra_monthly_amount", "qsehra_wait_days"].forEach(function (name) {
      const node = $("hire-" + name);
      if (!node) return;
      node.disabled = part;
      if (part) node.value = "";
    });
    const caseload = $("hire-caseload_range");
    if (caseload && !caseload.value) caseload.placeholder = part ? "part-time range" : "full-time range";
  }

  function clearFieldErrors() {
    const form = formEl();
    if (!form || !form.querySelectorAll) return;
    const marked = form.querySelectorAll(".field-error");
    for (let i = 0; i < marked.length; i++) {
      const node = marked[i];
      if (!node || !node.classList) continue;
      try {
        node.classList.remove("field-error");
      } catch (ignore) {
        /* A focused field can throw while its class is removed. Keep going. */
      }
    }
  }

  function releaseRoot(root) {
    const active = document.activeElement;
    if (active && active.blur && root.contains(active)) {
      try {
        active.blur();
      } catch (ignore) {
        /* Focus can sit on the field that just failed validation. */
      }
    }
    clearFieldErrors();
    try {
      root.replaceChildren();
    } catch (ignore) {
      root.textContent = "";
    }
  }

  function render(data, options) {
    const opts = typeof options === "string" ? { editId: options } : (options || {});
    const editId = opts.editId || "";
    const root = $("hiring-root");
    if (!root) return;
    releaseRoot(root);
    currentSettings = data.settings || {};
    const settings = currentSettings;
    const settingsBox = document.createElement("div");
    const label = document.createElement("label");
    label.textContent = "Response period (days)";
    const days = document.createElement("input");
    days.id = "hire-response-days";
    days.type = "number";
    days.min = "1";
    days.max = "365";
    days.value = String(settings.offer_response_days || 30);
    label.appendChild(days);
    const saveDays = document.createElement("button");
    saveDays.type = "button";
    saveDays.className = "primary";
    saveDays.textContent = "Save response period";
    saveDays.onclick = async function () {
      try {
        await ui().api("/hiring/settings", {
          method: "PUT",
          json: { offer_response_days: Number(days.value) },
        });
        await window.clyHiring.open({ message: "Saved." });
      } catch (error) {
        ui().setStatus($("hiring-status"), error.message, "error");
      }
    };
    settingsBox.appendChild(label);
    settingsBox.appendChild(saveDays);
    root.appendChild(settingsBox);

    const form = document.createElement("form");
    form.id = "hire-form";
    form.noValidate = true;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      saveOffer();
    });
    form.style.marginTop = "1rem";
    const resumeWrap = document.createElement("div");
    resumeWrap.id = "hire-resume-wrap";
    const resumeLabel = document.createElement("label");
    resumeLabel.textContent = "Upload resume";
    const resumeInput = document.createElement("input");
    resumeInput.id = "hire-resume";
    resumeInput.type = "file";
    resumeInput.accept = ".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";
    resumeInput.addEventListener("change", function () {
      const file = resumeInput.files && resumeInput.files[0];
      resumeInput.value = "";
      uploadResume(file);
    });
    resumeLabel.appendChild(resumeInput);
    resumeWrap.appendChild(resumeLabel);
    form.appendChild(resumeWrap);
    const resumeId = document.createElement("input");
    resumeId.id = "hire-resume-id";
    resumeId.name = "resume_id";
    resumeId.type = "hidden";
    form.appendChild(resumeId);
    const typeLabel = document.createElement("label");
    typeLabel.textContent = "Employment type *";
    const type = document.createElement("select");
    type.id = "hire-type";
    type.name = "employment_type";
    ["full-time", "part-time"].forEach(function (value) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      type.appendChild(option);
    });
    type.onchange = applyType;
    typeLabel.appendChild(type);
    form.appendChild(typeLabel);
    const clientLabel = document.createElement("label");
    clientLabel.textContent = "Direct client access";
    const client = document.createElement("input");
    client.id = "hire-client";
    client.name = "direct_client_access";
    client.type = "checkbox";
    client.checked = true;
    clientLabel.appendChild(client);
    form.appendChild(clientLabel);
    FIELDS.forEach(function (field) {
      const row = document.createElement("label");
      row.textContent = field[1] + (REQUIRED[field[0]] ? " *" : "");
      const input = document.createElement("input");
      input.id = field[0] === "candidate_credentials" ? "hire-certs" : "hire-" + field[0];
      input.name = field[0] === "candidate_credentials" ? "letter_certs" : field[0];
      input.type = "text";
      input.setAttribute("autocomplete", "off");
      if (field[0] === "candidate_credentials") input.setAttribute("data-lpignore", "true");
      if (field[0] === "personal_email") input.setAttribute("inputmode", "email");
      input.autocomplete = "off";
      row.appendChild(input);
      form.appendChild(row);
    });
    const editing = document.createElement("input");
    editing.id = "hire-editing";
    editing.name = "editing";
    editing.type = "hidden";
    form.appendChild(editing);
    const actions = document.createElement("div");
    actions.className = "row";
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "primary";
    save.textContent = "Save offer";
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "ghost";
    clear.textContent = "New offer";
    clear.onclick = function () {
      editing.value = "";
      const kept = document.getElementById("hire-resume-id");
      if (kept) kept.value = "";
      const wrap = document.getElementById("hire-resume-wrap");
      if (wrap) wrap.hidden = false;
      fillForm(null);
      clearResumeNotes();
      showStatus("", "ok");
    };
    actions.appendChild(save);
    actions.appendChild(clear);
    form.appendChild(actions);
    root.appendChild(form);
    fillForm(null);

    const list = document.createElement("div");
    list.className = "due-list";
    list.style.marginTop = "1.25rem";
    const offers = (data.offers || []).slice();
    const extra = opts.offer;
    if (extra && extra.id && !offers.some(function (offer) { return offer.id === extra.id; })) {
      offers.unshift(extra);
    }
    offers.forEach(function (offer) {
      const row = document.createElement("div");
      row.className = "due-row";
      const left = document.createElement("div");
      const title = document.createElement("div");
      title.className = "due-title";
      title.textContent = (offer.display_name || "Offer") + " — " + offer.status;
      const meta = document.createElement("div");
      meta.className = "due-meta";
      meta.textContent =
        (offer.employment_type || "") +
        " · respond by " +
        (offer.response_deadline || "") +
        (offer.direct_client_access ? " · direct client access" : "");
      left.appendChild(title);
      left.appendChild(meta);
      const buttons = document.createElement("div");
      buttons.className = "row";
      function button(text, className, action) {
        const node = document.createElement("button");
        node.type = "button";
        node.className = className;
        node.textContent = text;
        node.onclick = action;
        buttons.appendChild(node);
      }
      button("Edit", "ghost", function () {
        editing.value = offer.id;
        fillForm(offer);
        ui().setStatus($("hiring-status"), "Editing this draft or starting a new version.", "ok");
      });
      button("PDF", "ghost", function () {
        download(offer.id, "pdf");
      });
      button("DOCX", "ghost", function () {
        download(offer.id, "docx");
      });
      if (offer.resume) {
        button("Resume", "ghost", function () {
          download(offer.id, "resume");
        });
      }
      if (offer.drive_folder_url) {
        const link = document.createElement("a");
        link.href = offer.drive_folder_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Personnel folder";
        buttons.appendChild(link);
      }
      if (!offer.drive_connected) {
        const note = document.createElement("span");
        note.className = "meta";
        note.textContent = "Drive not connected";
        buttons.appendChild(note);
        button("Retry", "ghost", function () {
          retryDrive(offer.id);
        });
      } else if (!offer.drive_folder_url) {
        button("Create Drive folder", "ghost", function () {
          retryDrive(offer.id);
        });
      }
      if (offer.status === "draft") {
        button("Mark sent", "primary", function () {
          post(offer.id, "/send", { channel: "outside" }, "Marked sent.");
        });
      }
      if (offer.status === "draft" || offer.status === "sent" || offer.status === "viewed") {
        button("Withdraw", "ghost", function () {
          const reason = window.prompt("Why is this offer being withdrawn?");
          if (!reason) return;
          const editingNow = document.getElementById("hire-editing");
          const keep = !!(editingNow && editingNow.value === offer.id);
          post(offer.id, "/withdraw", { reason: reason }, "Withdrawn.", keep);
        });
      }
      button("Delete", "ghost", function (event) {
        if (event && event.preventDefault) event.preventDefault();
        const typed = window.prompt("Type the candidate's name to delete this offer");
        if (typed == null) return;
        const confirmName = String(typed).trim();
        if (!confirmName) return;
        post(offer.id, "/delete", { confirm: confirmName }, "Deleted.");
      });
      if (offer.status === "sent" || offer.status === "viewed") {
        button("Record acceptance", "primary", function () {
          showDecision(offer, "accept");
        });
        button("Record decline", "ghost", function () {
          showDecision(offer, "decline");
        });
      }
      row.appendChild(left);
      row.appendChild(buttons);
      list.appendChild(row);
    });
    if (!offers.length) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "No offers yet.";
      list.appendChild(empty);
    }
    root.appendChild(list);
    renderLibrary(root, data.forms || []);
    renderHires(root, data.hires || []);
    if (editId) {
      const match = offers.filter(function (offer) { return offer.id === editId; })[0];
      if (match) {
        const editingNow = document.getElementById("hire-editing");
        if (editingNow) editingNow.value = match.id;
        fillForm(match);
      }
    }
    const wrap = document.getElementById("hire-resume-wrap");
    if (wrap) wrap.hidden = false;
  }

  function showDecision(offer, kind) {
    const existing = document.getElementById("hire-decision");
    if (existing) existing.remove();
    const box = document.createElement("form");
    box.id = "hire-decision";
    box.className = "due-row";
    box.addEventListener("submit", function (event) {
      event.preventDefault();
      if (kind === "accept") recordAcceptance(offer.id, box);
      else recordDecline(offer.id, box);
    });
    const title = document.createElement("div");
    title.className = "due-title";
    title.textContent = kind === "accept" ? "Record acceptance" : "Record decline";
    box.appendChild(title);
    if (kind === "accept") {
      const fileLabel = document.createElement("label");
      fileLabel.textContent = "Signed offer PDF";
      const file = document.createElement("input");
      file.type = "file";
      file.name = "upload";
      file.accept = "application/pdf,.pdf";
      file.required = true;
      fileLabel.appendChild(file);
      const dateLabel = document.createElement("label");
      dateLabel.textContent = "Acceptance date";
      const date = document.createElement("input");
      date.type = "text";
      date.name = "accepted_on";
      date.placeholder = "MM/DD/YYYY";
      date.value = todayMDY();
      date.required = true;
      dateLabel.appendChild(date);
      box.appendChild(fileLabel);
      box.appendChild(dateLabel);
    } else {
      const reasonLabel = document.createElement("label");
      reasonLabel.textContent = "Reason (optional)";
      const reason = document.createElement("input");
      reason.type = "text";
      reason.name = "reason";
      reasonLabel.appendChild(reason);
      box.appendChild(reasonLabel);
    }
    const confirm = document.createElement("button");
    confirm.type = "submit";
    confirm.className = "primary";
    confirm.textContent = "Confirm";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ghost";
    cancel.textContent = "Cancel";
    cancel.onclick = function () {
      box.remove();
    };
    box.appendChild(confirm);
    box.appendChild(cancel);
    const list = document.querySelector("#hiring-root .due-list");
    if (list) list.appendChild(box);
  }

  async function recordAcceptance(id, box) {
    const file = box.querySelector("input[type=file]");
    const date = box.querySelector("input[name=accepted_on]");
    const chosen = file && file.files && file.files[0];
    if (!chosen) {
      showStatus("Upload the signed offer PDF.", "error");
      return;
    }
    const body = new FormData();
    body.append("upload", chosen, chosen.name || "signed.pdf");
    body.append("accepted_on", (date && date.value) || "");
    showStatus("Saving…", "ok");
    try {
      await ui().api("/hiring/offers/" + id + "/accept-paper", { method: "POST", body: body });
      await window.clyHiring.open({ message: "Acceptance recorded." });
    } catch (error) {
      showStatus((error && error.message) || "Could not save.", "error");
    }
  }

  async function recordDecline(id, box) {
    const reason = box.querySelector("input[name=reason]");
    const text = reason ? String(reason.value || "").trim() : "";
    const mapped = text ? "other" : "";
    await post(id, "/decline", { reason: mapped, note: text }, "Decline recorded.");
  }

  function renderLibrary(root, forms) {
    const box = document.createElement("section");
    box.style.marginTop = "1.5rem";
    const heading = document.createElement("h3");
    heading.textContent = "Forms";
    box.appendChild(heading);
    const note = document.createElement("p");
    note.className = "meta";
    note.textContent = "English forms only. Download the packet and send it yourself. Candidate email stays off.";
    box.appendChild(note);
    (forms || []).forEach(function (form) {
      if (!form.available) return;
      const link = document.createElement("button");
      link.type = "button";
      link.className = "ghost";
      link.textContent = form.title || form.form_key;
      link.onclick = function () {
        downloadForm(form.form_key);
      };
      box.appendChild(link);
    });
    const missing = document.createElement("p");
    missing.className = "meta";
    missing.textContent = "Employment agreement, non-solicitation, and handbook files are not on file yet.";
    box.appendChild(missing);
    root.appendChild(box);
  }

  function renderHires(root, hires) {
    const box = document.createElement("section");
    box.style.marginTop = "1.5rem";
    const heading = document.createElement("h3");
    heading.textContent = "Onboarding";
    box.appendChild(heading);
    if (!hires.length) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "No hires yet.";
      box.appendChild(empty);
      root.appendChild(box);
      return;
    }
    hires.forEach(function (hire) {
      const card = document.createElement("article");
      card.className = "due-row";
      const title = document.createElement("div");
      title.className = "due-title";
      title.textContent = (hire.display_name || "Hire") + " — " + (hire.status || "");
      card.appendChild(title);
      if (hire.i9_drive !== "ready") {
        const pending = document.createElement("p");
        pending.className = "meta";
        pending.textContent = "I-9 drive is not configured. This stays a pending setup item.";
        card.appendChild(pending);
      }
      const welcome = document.createElement("textarea");
      welcome.readOnly = true;
      welcome.rows = 6;
      welcome.value = hire.welcome || "";
      card.appendChild(welcome);
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "ghost";
      copy.textContent = "Copy welcome message";
      copy.onclick = function () {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(welcome.value).then(function () {
            showStatus("Welcome message copied.", "ok");
          });
        } else {
          welcome.select();
          showStatus("Select the message and copy it.", "ok");
        }
      };
      card.appendChild(copy);
      (hire.items || []).forEach(function (item) {
        const row = document.createElement("div");
        row.className = "due-row";
        if (item.warning) row.style.color = "#8b2e2e";
        const label = document.createElement("div");
        label.textContent =
          item.title +
          " — " +
          (item.owner_role || "") +
          " — due " +
          (item.due_date || "—") +
          " — " +
          item.status +
          (item.done_by ? " — " + item.done_by : "") +
          (item.done_at ? " — " + String(item.done_at).slice(0, 10) : "");
        row.appendChild(label);
        if (item.note) {
          const noted = document.createElement("div");
          noted.className = "meta";
          noted.textContent = item.note;
          row.appendChild(noted);
        }
        if (item.kind !== "auto") {
          const note = document.createElement("input");
          note.type = "text";
          note.placeholder = "Note";
          note.value = item.note || "";
          row.appendChild(note);
          function mark(status) {
            ui()
              .api("/hiring/hires/" + hire.id + "/items/" + item.template_key, {
                method: "POST",
                json: { status: status, note: note.value },
              })
              .then(function () {
                return window.clyHiring.open({ message: "Checklist updated." });
              })
              .catch(function (error) {
                showStatus((error && error.message) || "Could not save.", "error");
              });
          }
          const done = document.createElement("button");
          done.type = "button";
          done.className = "ghost";
          done.textContent = "Done";
          done.onclick = function () {
            mark("done");
          };
          const na = document.createElement("button");
          na.type = "button";
          na.className = "ghost";
          na.textContent = "N/A";
          na.onclick = function () {
            mark("n/a");
          };
          const undo = document.createElement("button");
          undo.type = "button";
          undo.className = "ghost";
          undo.textContent = "Undo";
          undo.onclick = function () {
            mark("open");
          };
          row.appendChild(done);
          row.appendChild(na);
          row.appendChild(undo);
        }
        card.appendChild(row);
      });
      if (hire.status === "onboarding") {
        const activate = document.createElement("button");
        activate.type = "button";
        activate.className = "primary";
        activate.textContent = "Activate";
        activate.onclick = function () {
          const email = window.prompt("Work email for the employee record");
          if (!email) return;
          ui()
            .api("/hiring/hires/" + hire.id + "/activate", { method: "POST", json: { work_email: email } })
            .then(function () {
              return window.clyHiring.open({ message: "Hire activated." });
            })
            .catch(function (error) {
              showStatus((error && error.message) || "Could not activate.", "error");
            });
        };
        card.appendChild(activate);
      }
      box.appendChild(card);
    });
    root.appendChild(box);
  }

  function downloadForm(key) {
    const headers = {};
    if (ui().state && ui().state.token) headers.Authorization = "Bearer " + ui().state.token;
    fetch("/hiring/forms/" + key, { headers: headers })
      .then(function (res) {
        if (!res.ok) throw new Error("Could not download that form.");
        const named = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
        return res.blob().then(function (blob) {
          return { blob: blob, filename: named ? named[1] : key + ".pdf" };
        });
      })
      .then(function (file) {
        const url = URL.createObjectURL(file.blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = file.filename;
        link.click();
        URL.revokeObjectURL(url);
      })
      .catch(function (error) {
        showStatus((error && error.message) || "Could not download that form.", "error");
      });
  }

  function clearResumeNotes() {
    document.querySelectorAll("#hire-form .resume-note").forEach(function (node) {
      node.remove();
    });
    document.querySelectorAll("#hire-form .from-resume").forEach(function (node) {
      node.classList.remove("from-resume");
    });
  }

  function markFromResume(input) {
    if (!input) return;
    input.classList.add("from-resume");
    if (input.parentElement && !input.parentElement.querySelector(".resume-note")) {
      const note = document.createElement("span");
      note.className = "resume-note";
      note.textContent = "Filled from resume, please check.";
      input.insertAdjacentElement("afterend", note);
    }
  }

  function applyResume(fields) {
    const incoming = fields || {};
    ["candidate_full_name", "candidate_first_name", "personal_email", "candidate_credentials", "position_title"].forEach(function (name) {
      const value = String(incoming[name] || "").trim();
      const input = document.getElementById("hire-" + name) || (name === "candidate_credentials" ? document.getElementById("hire-certs") : null);
      if (!value || !input || String(input.value || "").trim()) return;
      input.value = value;
      markFromResume(input);
    });
    const phoneValue = String(incoming.phone || "").trim();
    const phone = document.getElementById("hire-phone") || document.getElementById("hire-personal_phone");
    if (phoneValue && phone && !String(phone.value || "").trim()) {
      phone.value = phoneValue;
      markFromResume(phone);
    }
  }

  async function uploadResume(file) {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      showStatus("That resume is larger than 10 MB.", "error");
      return;
    }
    showStatus("Reading resume…", "ok");
    const body = new FormData();
    body.append("resume", file, file.name || "resume");
    try {
      const data = await ui().api("/hiring/resumes", { method: "POST", body: body });
      const hidden = document.getElementById("hire-resume-id");
      if (hidden) hidden.value = (data && data.resume_id) || "";
      applyResume(data && data.fields);
      if (data && data.warning) showStatus(data.warning, "error");
      else showStatus("", "ok");
    } catch (error) {
      showStatus((error && error.message) || "That resume could not be read.", "error");
    }
  }

  let saving = false;

  async function saveOffer() {
    if (saving) return;
    saving = true;
    const saveButton = formEl() && formEl().querySelector("button[type=submit]");
    if (saveButton) saveButton.disabled = true;
    clearFieldErrors();
    showStatus("Saving…", "ok");
    try {
      let body;
      try {
        body = readForm();
      } catch (error) {
        showStatus((error && error.message) || "Could not save.", "error");
        return;
      }
      const editingId = valueOf("editing");
      const payload = Object.assign({}, body);
      delete payload._editing;
      let saved;
      if (editingId) {
        saved = await ui().api("/hiring/offers/" + editingId, { method: "PUT", json: payload });
      } else {
        saved = await ui().api("/hiring/offers", { method: "POST", json: payload });
      }
      const offerId = (saved && saved.id) || editingId;
      const opened = await window.clyHiring.open({ message: "Saved.", editId: offerId, offer: saved });
      if (!opened) {
        const editingNow = document.getElementById("hire-editing");
        if (editingNow && offerId) editingNow.value = offerId;
        clearFieldErrors();
        showStatus("Saved.", "ok");
      }
    } catch (error) {
      showStatus((error && error.message) || "Could not save.", "error");
      markField(error && error.field);
    } finally {
      saving = false;
      const button = formEl() && formEl().querySelector("button[type=submit]");
      if (button) button.disabled = false;
    }
  }

  function markField(name) {
    clearFieldErrors();
    if (!name) return;
    const el = fieldNode(name) || (formEl() && formEl().querySelector('[name="' + String(name).replace(/"/g, "") + '"]'));
    if (!el || !el.classList) return;
    el.classList.add("field-error");
    try {
      if (el.focus) el.focus();
    } catch (ignore) {
      /* The field is already marked. Focusing it is optional. */
    }
  }

  async function retryDrive(id) {
    showStatus("Saving…", "ok");
    try {
      const offer = await ui().api("/hiring/offers/" + id + "/drive", { method: "POST", json: {} });
      const ready = offer && offer.drive_connected && offer.drive_folder_url;
      await window.clyHiring.open({ editId: id, message: ready ? "Personnel folder ready." : "Drive not connected" });
    } catch (error) {
      showStatus((error && error.message) || "Drive not connected", "error");
    }
  }

  async function post(id, suffix, json, ok, keepEdit) {
    if (suffix === "/delete") {
      const confirmName = json && json.confirm != null ? String(json.confirm).trim() : "";
      if (!confirmName) return;
      json = { confirm: confirmName };
    }
    try {
      await ui().api("/hiring/offers/" + id + suffix, { method: "POST", json: json });
      await window.clyHiring.open({ editId: keepEdit ? id : "", message: ok || "Saved." });
    } catch (error) {
      showStatus((error && error.message) || "Could not save.", "error");
    }
  }

  function download(id, kind) {
    const headers = {};
    if (ui().state && ui().state.token) headers.Authorization = "Bearer " + ui().state.token;
    fetch("/hiring/offers/" + id + "/" + kind, { headers: headers, credentials: "include" })
      .then(function (res) {
        if (!res.ok) throw new Error("Could not download the letter");
        const disposition = res.headers.get("Content-Disposition") || "";
        const named = disposition.match(/filename="([^"]+)"/i);
        const filename = named ? named[1] : id + "." + (kind === "resume" ? "pdf" : kind);
        return res.blob().then(function (blob) {
          return { blob: blob, filename: filename };
        });
      })
      .then(function (file) {
        const url = URL.createObjectURL(file.blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = file.filename;
        link.click();
        URL.revokeObjectURL(url);
      })
      .catch(function (error) {
        ui().setStatus($("hiring-status"), error.message, "error");
      });
  }

  window.clyHiring = {
    open: async function (opts) {
      const options = opts || {};
      const api = ui();
      if (!api) return false;
      try {
        if (api.show) api.show("hiring-panel");
        const data = await api.api("/hiring/bootstrap", { cache: "no-store" });
        render(data, options);
        showStatus(options.message || "", options.message ? "ok" : "");
        return true;
      } catch (error) {
        showStatus((error && error.message) || "Could not open hiring.", "error");
        return false;
      }
    },
  };
})();
