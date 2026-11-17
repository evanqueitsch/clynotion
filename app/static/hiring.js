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

  function askDialog(options) {
    const existing = document.getElementById("hire-dialog");
    if (existing) existing.remove();
    const dialog = document.createElement("form");
    dialog.id = "hire-dialog";
    dialog.className = "hire-dialog";
    const title = document.createElement("div");
    title.className = "due-title";
    title.textContent = options.title || "";
    const label = document.createElement("label");
    label.textContent = options.label || "";
    const input = document.createElement("input");
    input.type = options.label === "Work email" ? "email" : "text";
    input.required = true;
    input.autocomplete = "off";
    label.appendChild(input);
    const confirm = document.createElement("button");
    confirm.type = "submit";
    confirm.className = "primary";
    confirm.textContent = options.confirmText || "Confirm";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ghost";
    cancel.textContent = "Cancel";
    cancel.onclick = function () {
      dialog.remove();
    };
    dialog.appendChild(title);
    dialog.appendChild(label);
    dialog.appendChild(confirm);
    dialog.appendChild(cancel);
    dialog.addEventListener("submit", function (event) {
      event.preventDefault();
      const typed = String(input.value || "").trim();
      if (!typed) return;
      dialog.remove();
      options.onConfirm(typed);
    });
    if (options.anchor && options.anchor.insertAdjacentElement) options.anchor.insertAdjacentElement("afterend", dialog);
    else (document.getElementById("hiring-root") || document.body).appendChild(dialog);
    input.focus();
  }

  function showBusy(msg) {
    const el = document.getElementById("hiring-status");
    const api = ui();
    if (!el || !api || !api.setStatus) return;
    api.setStatus(el, msg, "ok");
    el.classList.add("busy");
  }

  function showStatus(msg, kind) {
    const el = document.getElementById("hiring-status");
    const api = ui();
    if (!el || !api || !api.setStatus) return;
    const text = msg || (kind === "error" ? "Could not save." : "");
    api.setStatus(el, text, text ? (kind || "error") : "");
    el.classList.remove("busy");
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  async function watchDrive(offerId, editId) {
    showBusy("Creating Drive folders…");
    for (let i = 0; i < 40; i += 1) {
      await sleep(1500);
      try {
        const offer = await ui().api("/hiring/offers/" + offerId);
        if (!offer || offer.drive_status !== "working") {
          const ready = offer && offer.drive_status === "connected";
          await window.clyHiring.open({
            editId: editId,
            offer: editId ? offer : undefined,
            message: ready ? "Drive folders ready." : "Saved.",
          });
          return;
        }
      } catch (error) {
        showStatus((error && error.message) || "Drive folders are still updating.", "error");
        return;
      }
    }
    showStatus("Drive folders are still updating.", "ok");
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
    const form = formEl();
    const locked = !!(offer && (offer.status === "accepted" || offer.status === "sent" || offer.status === "viewed"));
    if (form) {
      form.querySelectorAll("input, select, textarea, button[type=submit]").forEach(function (node) {
        if (!locked && node.id !== "hire-editing") {
          if (node.id === "hire-qsehra_monthly_amount" || node.id === "hire-qsehra_wait_days") return;
          node.disabled = false;
        }
      });
      if (locked) {
        form.querySelectorAll("input, select, textarea, button[type=submit]").forEach(function (node) {
          node.disabled = true;
        });
      }
      const submit = form.querySelector("button[type=submit]");
      if (submit && !locked) submit.textContent = offer && offer.id ? "Update offer" : "Save offer";
    }
    let lockedNote = document.getElementById("hire-locked-note");
    if (locked) {
      if (!lockedNote && form) {
        lockedNote = document.createElement("p");
        lockedNote.id = "hire-locked-note";
        lockedNote.className = "meta";
        form.appendChild(lockedNote);
      }
      if (lockedNote) {
        lockedNote.textContent = offer.status === "accepted"
          ? "Accepted offers can't be edited."
          : "Sent offers can't be edited. Revise offer starts a new draft.";
      }
    } else if (lockedNote) {
      lockedNote.remove();
    }
  }

  function maybeWatch(offers) {
    const working = (offers || []).filter(function (offer) { return offer.drive_status === "working"; })[0];
    if (!working) {
      watchingId = "";
      return;
    }
    if (watchingId === working.id) return;
    watchingId = working.id;
    watchDrive(working.id, keptEditId || working.id);
  }

  function applyType() {
    const part = fieldValue("hire-type") === "part-time";
    ["qsehra_monthly_amount", "qsehra_wait_days"].forEach(function (name) {
      const node = $("hire-" + name);
      if (!node) return;
      if (node.parentElement) node.parentElement.hidden = part;
      node.disabled = part;
      if (part) node.value = "";
    });
    const caseload = $("hire-caseload_range");
    if (caseload && !caseload.value) caseload.placeholder = part ? "part-time range" : "full-time range";
  }

  function clearFieldErrors() {
    const form = formEl();
    if (!form || !form.querySelectorAll) return;
    form.querySelectorAll(".field-error-text").forEach(function (node) { node.remove(); });
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

  function easternToday() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
  }

  function shortDate(iso) {
    if (!iso) return "";
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return String(iso).slice(0, 10);
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(parsed);
  }

  function stepper(steps) {
    const list = document.createElement("ol");
    list.className = "hire-stepper";
    steps.forEach(function (step, index) {
      const item = document.createElement("li");
      item.className = "hire-step " + step.state;
      if (step.state === "current") item.setAttribute("aria-current", "step");
      item.textContent = String(index + 1) + ". " + step.label;
      list.appendChild(item);
    });
    return list;
  }

  function stepState(done, current) {
    if (done) return "done";
    if (current) return "current";
    return "todo";
  }

  function wizardSteps(offer, hire) {
    const sent = !!(offer && (offer.status === "sent" || offer.status === "viewed" || offer.status === "accepted"));
    const accepted = !!(offer && offer.status === "accepted") || !!(hire && hire.id);
    const chosen = !!(hire && (hire.packet_form_keys || []).length);
    const handed = !!(hire && hire.owner === "billing" && (hire.stage === "payroll" || hire.stage === "active"));
    return [
      { label: "Create the offer", state: stepState(!!(offer && offer.id), !(offer && offer.id)) },
      { label: "Mark sent", state: stepState(sent || accepted, !!(offer && offer.id) && !sent && !accepted) },
      { label: "Record acceptance", state: stepState(accepted, sent && !accepted) },
      { label: "Choose packet documents", state: stepState(handed || chosen, accepted && !chosen && !handed) },
      { label: "Review and hand off", state: stepState(handed, accepted && chosen && !handed) },
    ];
  }

  function hireWord(hire) {
    if (!hire) return "";
    if (hire.status === "active" || hire.stage === "active") return "active";
    if (hire.owner === "billing" && hire.stage === "payroll") return "handed off";
    return hire.stage || "";
  }

  function offerHeading(offer, hire) {
    const version = offer && offer.current_version ? " — v" + offer.current_version : "";
    const name = (offer && offer.display_name) || (hire && hire.display_name) || "Offer";
    if (hire) {
      const word = hireWord(hire);
      return name + (word ? " — " + word : "") + version;
    }
    return name + " — " + ((offer && offer.status) || "") + version;
  }

  function stageBar(stage, owner, label) {
    const line = document.createElement("div");
    line.className = "due-meta hire-stage";
    const who = owner === "billing" ? (label || "Billing") : "Admin";
    line.textContent = "Stage: " + (stage || "offer") + " · Owner: " + who;
    return line;
  }

  function emailText(status) {
    if (status === "sent") return "Email sent";
    if (status === "suppressed") return "Email not sent (setting off)";
    if (status === "failed") return "Email not sent";
    return "\u00a0";
  }

  function checkedPacketKeys(root) {
    return Array.prototype.map.call(root.querySelectorAll("input.packet-key:checked"), function (box) {
      return box.value;
    });
  }

  function renderPacketStep(card, hire) {
    const block = document.createElement("div");
    const heading = document.createElement("h4");
    heading.textContent = "4. Choose packet documents";
    block.appendChild(heading);
    const choices = hire.packet_choices || [];
    const saved = hire.packet_form_keys || [];
    choices.forEach(function (choice) {
      const row = document.createElement("label");
      row.className = "packet-row";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.className = "packet-key";
      box.value = choice.form_key;
      box.disabled = !choice.available;
      box.checked = !!choice.available && (saved.length ? saved.indexOf(choice.form_key) !== -1 : !!choice.default);
      row.appendChild(box);
      const text = document.createElement("span");
      text.textContent = choice.title + (choice.available ? "" : " — not in the library yet");
      row.appendChild(text);
      if (choice.available) {
        const download = document.createElement("button");
        download.type = "button";
        download.className = "ghost";
        download.textContent = choice.form_key === "h4c_welcome_template" ? "Download welcome letter (.docx)" : "Download";
        download.onclick = function (event) {
          event.preventDefault();
          downloadForm(choice.form_key);
        };
        row.appendChild(download);
      }
      block.appendChild(row);
    });
    const downloadSelected = document.createElement("button");
    downloadSelected.type = "button";
    downloadSelected.className = "ghost";
    downloadSelected.textContent = "Download selected";
    downloadSelected.onclick = function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (downloadSelected.disabled) return;
      downloadSelected.disabled = true;
      const headers = { "Content-Type": "application/json" };
      if (ui().state && ui().state.token) headers.Authorization = "Bearer " + ui().state.token;
      fetch("/hiring/hires/" + hire.id + "/packet.zip", {
        method: "POST",
        headers: headers,
        body: JSON.stringify({ form_keys: checkedPacketKeys(block) }),
      })
        .then(function (res) {
          if (!res.ok) throw new Error("Could not download the packet.");
          const named = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
          return res.blob().then(function (blob) {
            return { blob: blob, filename: named ? named[1] : "packet.zip" };
          });
        })
        .then(function (file) {
          const url = URL.createObjectURL(file.blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = file.filename;
          document.body.appendChild(link);
          link.click();
          link.remove();
          window.setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
        })
        .catch(function (error) {
          showStatus((error && error.message) || "Could not download the packet.", "error");
        })
        .then(function () {
          downloadSelected.disabled = false;
        });
    };
    block.appendChild(downloadSelected);
    card.appendChild(block);
    return block;
  }

  function renderHandoff(card, hire, packetRoot, billingLabel) {
    const block = document.createElement("div");
    const heading = document.createElement("h4");
    heading.textContent = "5. Review and hand off";
    block.appendChild(heading);
    const summary = document.createElement("p");
    summary.className = "meta";
    summary.textContent = [
      hire.display_name || "",
      hire.position || "",
      hire.employment_type || "",
      hire.start_date || "",
    ].filter(Boolean).join(" · ");
    block.appendChild(summary);
    const task = document.createElement("p");
    task.textContent = "Add to QuickBooks Workforce with Employee self onboard on (name, hire date, email). QuickBooks emails the hire to enter their own W-4 and direct deposit.";
    block.appendChild(task);
    const copies = [
      ["First name", hire.first_name || ""],
      ["Last name", hire.last_name || ""],
      ["Hire date", hire.start_date || ""],
      ["Email", hire.personal_email || ""],
    ];
    copies.forEach(function (pair) {
      const row = document.createElement("div");
      row.className = "row";
      const label = document.createElement("span");
      label.textContent = pair[0] + ": " + (pair[1] || "—");
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "ghost";
        copy.textContent = "Copy " + pair[0].toLowerCase();
        copy.onclick = function () {
          const value = pair[1] || "";
          const label = copy.textContent;
          const showCopied = function () {
            copy.textContent = "Copied";
            window.setTimeout(function () { copy.textContent = label; }, 1200);
          };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(value).then(showCopied).catch(showCopied);
            return;
          }
          showCopied();
        };
      row.appendChild(label);
      row.appendChild(copy);
      block.appendChild(row);
    });
    const inviteLabel = document.createElement("label");
    const invite = document.createElement("input");
    invite.type = "checkbox";
    invite.checked = !!hire.qb_invite_at;
    inviteLabel.appendChild(invite);
    inviteLabel.appendChild(document.createTextNode(" Invite sent in QuickBooks"));
    block.appendChild(inviteLabel);
    const inviteNote = document.createElement("p");
    inviteNote.className = "meta";
    inviteNote.textContent = hire.qb_invite_at
      ? "Recorded by " + (hire.qb_invite_by || "admin") + " on " + shortDate(hire.qb_invite_at) + "."
      : "";
    block.appendChild(inviteNote);
    const handedOff = hire.owner === "billing" && (hire.stage === "payroll" || hire.stage === "active");
    const reason = document.createElement("p");
    reason.className = "meta";
    reason.textContent = hire.qb_invite_at || handedOff ? "" : "Mark Invite sent in QuickBooks before handing this hire to Billing.";
    const hand = document.createElement("button");
    hand.type = "button";
    hand.className = "primary";
    hand.textContent = handedOff
      ? "Handed off to " + (billingLabel || "Billing") + (hire.handoff_at ? " on " + shortDate(hire.handoff_at) : "")
      : "Hand off to " + (billingLabel || "Billing");
    hand.disabled = handedOff || !hire.qb_invite_at;
    invite.onchange = function () {
      ui()
        .api("/hiring/hires/" + hire.id + "/qb-invite", { method: "POST", json: { sent: invite.checked } })
        .then(function (saved) { refreshHire(saved, invite.checked ? "Invite recorded." : "Invite cleared."); })
        .catch(function (error) { showStatus((error && error.message) || "Could not record the invite.", "error"); });
    };
    hand.onclick = function () {
      if (hand.disabled) return;
      hand.disabled = true;
      hand.textContent = "Handing off…";
      ui()
        .api("/hiring/hires/" + hire.id + "/handoff", {
          method: "POST",
          json: { form_keys: checkedPacketKeys(packetRoot) },
        })
        .then(function (saved) {
          const note = saved && saved.notice === "already handed off" ? "Already handed off." : "Handed off.";
          refreshHire(saved, note);
        })
        .catch(function (error) {
          hand.disabled = handedOff || !hire.qb_invite_at;
          hand.textContent = handedOff
            ? "Handed off to " + (billingLabel || "Billing") + (hire.handoff_at ? " on " + shortDate(hire.handoff_at) : "")
            : "Hand off to " + (billingLabel || "Billing");
          showStatus((error && error.message) || "Could not hand off.", "error");
        });
    };
    block.appendChild(reason);
    block.appendChild(hand);
    if (hire.owner === "billing") {
      const recall = document.createElement("button");
      recall.type = "button";
      recall.className = "ghost";
      recall.textContent = "Recall to Admin";
      recall.onclick = function () {
        ui()
          .api("/hiring/hires/" + hire.id + "/recall", { method: "POST", json: {} })
          .then(function (saved) {
            refreshHire(saved, "Handoff recalled.");
          })
          .catch(function (error) {
            showStatus((error && error.message) || "Could not recall.", "error");
          });
      };
      block.appendChild(recall);
    }
    const mail = document.createElement("div");
    mail.className = "meta hire-mail";
    if (hire.handoff_at) {
      const staff = emailText(hire.staff_email_status);
      const candidate = emailText(hire.candidate_email_status);
      mail.textContent = "Staff: " + staff + "\nCandidate: " + candidate;
      block.appendChild(mail);
    }
    card.appendChild(block);
  }

  function itemByKey(hire, key) {
    return (hire.items || []).filter(function (item) { return item.template_key === key; })[0] || {};
  }

  function itemLine(item) {
    const bits = [
      item.title,
      item.owner_role || "",
      "due " + (item.due_date || "—"),
      item.status,
    ];
    if (item.done_by) bits.push(item.done_by);
    const enteredDate = item.template_key === "i9_section2"
      ? ((item.dates && (item.dates.i9_completed_on || item.dates.i9_section2_completed_on)) || "")
      : "";
    if (enteredDate) bits.push(enteredDate);
    else if (item.done_at) bits.push(String(item.done_at).slice(0, 10));
    if (item.note) bits.push(item.note);
    return bits.filter(Boolean).join(" — ");
  }

  function paintChecklist(card, hire) {
    if (!card || !hire) return;
    (hire.items || []).forEach(function (item) {
      const row = card.querySelector('[data-item="' + item.template_key + '"]');
      if (!row) return;
      row.classList.toggle("late", !!item.warning);
      const label = row.querySelector(".hire-item-text");
      if (label) label.textContent = itemLine(item);
      const badge = row.querySelector(".badge-due");
      if (item.badge) {
        if (badge) badge.textContent = item.badge;
      } else if (badge) {
        badge.remove();
      }
    });
  }

  function postItem(card, hire, key, body, message) {
    ui()
      .api("/hiring/hires/" + hire.id + "/items/" + key, { method: "POST", json: body })
      .then(function (saved) {
        refreshHire(saved, message || "Saved.");
      })
      .catch(function (error) {
        showStatus((error && error.message) || "Could not save.", "error");
      });
  }

  function dateStep(card, hire, item, label, dateKey, doneWhenSet) {
    const row = document.createElement("div");
    row.className = "hire-item" + (item.warning ? " late" : "");
    const recorded = !!(item.dates && item.dates[dateKey]);
    const text = document.createElement("div");
    text.textContent = label + " — " + (recorded ? "done" : "open");
    row.appendChild(text);
    if (item.badge && item.status === "open") {
      const badge = document.createElement("span");
      badge.className = "badge-due";
      badge.textContent = item.badge;
      row.appendChild(badge);
    }
    const fieldId = "hire-date-" + hire.id + "-" + dateKey;
    const fieldLabel = document.createElement("label");
    fieldLabel.htmlFor = fieldId;
    fieldLabel.className = "meta";
    fieldLabel.textContent = label + " date";
    const input = document.createElement("input");
    input.id = fieldId;
    input.type = "date";
    input.setAttribute("aria-label", label + " date");
    input.value = (item.dates && item.dates[dateKey]) || "";
    row.appendChild(fieldLabel);
    row.appendChild(input);
    const save = document.createElement("button");
    save.type = "button";
    save.className = "ghost";
    save.textContent = "Save";
    save.onclick = function () {
      const dates = Object.assign({}, item.dates || {});
      dates[dateKey] = input.value;
      let status = "open";
      if (item.template_key === "payroll_w4_dd") {
        status = dates.w4_received_on && dates.direct_deposit_received_on ? "done" : "open";
      } else if (doneWhenSet) {
        status = input.value ? "done" : "open";
      }
      text.textContent = label + " — " + (input.value ? "done" : "open");
      postItem(card, hire, item.template_key, { status: status, dates: dates, note: item.note || "" }, label + " saved.");
    };
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "ghost";
    undo.textContent = "Undo";
    undo.onclick = function () {
      const dates = Object.assign({}, item.dates || {});
      dates[dateKey] = "";
      text.textContent = label + " — open";
      postItem(card, hire, item.template_key, { status: "open", dates: dates, note: "" }, label + " undone.");
    };
    row.appendChild(save);
    row.appendChild(undo);
    card.appendChild(row);
  }

  function renderBillingSteps(card, hire) {
    const heading = document.createElement("h4");
    heading.textContent = "Payroll steps";
    card.appendChild(heading);
    const payroll = itemByKey(hire, "payroll_w4_dd");
    const confirm = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = payroll.status === "done";
    confirm.appendChild(box);
    confirm.appendChild(document.createTextNode(" 1. Confirm the hire finished QuickBooks Workforce self-onboarding (W-4 and direct deposit show complete in QuickBooks Workforce)."));
    box.onchange = function () {
      const dates = Object.assign({}, payroll.dates || {});
      dates.qb_onboarding_confirmed_on = box.checked ? new Date().toISOString().slice(0, 10) : "";
      postItem(card, hire, "payroll_w4_dd", { status: box.checked ? "done" : "open", dates: dates }, "QuickBooks onboarding saved.");
    };
    card.appendChild(confirm);
    dateStep(card, hire, itemByKey(hire, "pa_new_hire_report"), "2. PA new-hire report filed", "pa_new_hire_filed_on", true);
    dateStep(card, hire, itemByKey(hire, "i9_section2"), "3. I-9 in-person appointment", "i9_appointment_on", false);
    dateStep(card, hire, itemByKey(hire, "i9_section2"), "3. I-9 completed", "i9_completed_on", true);
    dateStep(card, hire, itemByKey(hire, "clearances"), "4. PA clearances received", "clearances_received_on", true);
    dateStep(card, hire, itemByKey(hire, "exclusion_screening"), "4. Exclusion screening verified", "exclusion_received_on", true);
  }

  function render(data, options) {
    lastHiring = data;
    const opts = typeof options === "string" ? { editId: options } : (options || {});
    const root = $("hiring-root");
    if (!root) return;
    releaseRoot(root);
    if (data.viewer === "billing") {
      renderBillingHome(root, data, opts);
      return;
    }
    currentSettings = data.settings || {};
    const settings = currentSettings;
    const intro = document.getElementById("hiring-intro");
    if (intro) {
      intro.textContent = settings.candidate_emails_enabled
        ? "Offers stay with Admin. Candidate email is on, so the selected packet is emailed at handoff."
        : "Offers stay with Admin. Letters are downloaded and sent by hand until candidate email is turned on.";
    }
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
    const mailHead = document.createElement("h3");
    mailHead.textContent = "Wizard emails";
    settingsBox.appendChild(mailHead);
    const staffLabel = document.createElement("label");
    staffLabel.textContent = "Staff handoff emails — " + (settings.staff_handoff_emails_enabled ? "currently on" : "currently off");
    const staffOn = document.createElement("input");
    staffOn.type = "checkbox";
    staffOn.checked = !!settings.staff_handoff_emails_enabled;
    staffLabel.appendChild(staffOn);
    const candidateLabel = document.createElement("label");
    candidateLabel.textContent = "Candidate emails — " + (settings.candidate_emails_enabled ? "currently on" : "currently off");
    const candidateOn = document.createElement("input");
    candidateOn.type = "checkbox";
    candidateOn.checked = !!settings.candidate_emails_enabled;
    candidateLabel.appendChild(candidateOn);
    const overrideLabel = document.createElement("label");
    overrideLabel.textContent = "Test recipient override — " + (settings.wizard_email_override ? "currently " + settings.wizard_email_override : "currently unset");
    const override = document.createElement("input");
    override.type = "email";
    override.value = settings.wizard_email_override || "";
    override.placeholder = "operations@heart4change.org";
    overrideLabel.appendChild(override);
    const greetingLabel = document.createElement("label");
    greetingLabel.textContent = "Billing contact first name — " + (settings.staff_greeting_name ? "currently " + settings.staff_greeting_name : "currently unset");
    const greetingName = document.createElement("input");
    greetingName.type = "text";
    greetingName.value = settings.staff_greeting_name || "";
    greetingName.placeholder = "Currently unset";
    greetingLabel.appendChild(greetingName);
    const help = document.createElement("p");
    help.className = "meta";
    help.textContent = "Placeholders: {name} {first_name} {last_name} {position} {employment} {start_date} {start_date_long} {link} {supervisor_name} {packet_list} {billing_first_name}. A next-step line starting with [direct] is included only when the hire has direct client access. [office] is included only when they do not.";
    settingsBox.appendChild(staffLabel);
    settingsBox.appendChild(candidateLabel);
    settingsBox.appendChild(overrideLabel);
    settingsBox.appendChild(greetingLabel);
    settingsBox.appendChild(help);
    const fields = {};
    function section(title, prefix) {
      const heading = document.createElement("h3");
      heading.textContent = title;
      settingsBox.appendChild(heading);
      function add(label, key, rows, single) {
        const fieldLabel = document.createElement("label");
        fieldLabel.textContent = label;
        const area = single ? document.createElement("input") : document.createElement("textarea");
        if (!single) {
          area.rows = rows;
        }
        area.value = settings[prefix + "_" + key] || "";
        fieldLabel.appendChild(area);
        settingsBox.appendChild(fieldLabel);
        fields[prefix + "_" + key] = area;
      }
      add("Subject", "subject", 1, true);
      add("Intro paragraph", "intro", 5, false);
      add("Next steps (one per line)", "steps", 8, false);
      add("Closing paragraph", "closing", 4, false);
      add("Signature", "signature", 4, false);
    }
    section("Candidate email", "candidate_packet");
    section("Staff handoff email", "staff_handoff");
    const previewFrame = document.createElement("iframe");
    previewFrame.title = "Wizard email preview";
    previewFrame.setAttribute("sandbox", "");
    previewFrame.style.width = "100%";
    previewFrame.style.height = "720px";
    previewFrame.style.border = "1px solid #d9ddd9";
    previewFrame.style.background = "#f3f4f3";
    const previewSubject = document.createElement("p");
    previewSubject.className = "meta";
    function showPreview(kind) {
      const prefix = kind === "staff" ? "staff_handoff" : "candidate_packet";
      ui().api("/hiring/wizard-email-preview", {
        method: "POST",
        json: {
          kind: kind,
          subject: fields[prefix + "_subject"].value,
          intro: fields[prefix + "_intro"].value,
          steps: fields[prefix + "_steps"].value,
          closing: fields[prefix + "_closing"].value,
          signature: fields[prefix + "_signature"].value,
          staff_greeting_name: greetingName.value.trim(),
        },
      }).then(function (result) {
        previewSubject.textContent = "Preview for Riley Sample — " + result.subject;
        previewFrame.srcdoc = result.html;
      }).catch(function (error) {
        ui().setStatus($("hiring-status"), error.message, "error");
      });
    }
    const previewCandidate = document.createElement("button");
    previewCandidate.type = "button";
    previewCandidate.className = "ghost";
    previewCandidate.textContent = "Preview candidate email";
    previewCandidate.onclick = function () { showPreview("candidate"); };
    const previewStaff = document.createElement("button");
    previewStaff.type = "button";
    previewStaff.className = "ghost";
    previewStaff.textContent = "Preview staff email";
    previewStaff.onclick = function () { showPreview("staff"); };
    const saveMail = document.createElement("button");
    saveMail.type = "button";
    saveMail.className = "ghost";
    saveMail.textContent = "Save email settings";
    saveMail.onclick = async function () {
      const json = {
        staff_handoff_emails_enabled: staffOn.checked,
        candidate_emails_enabled: candidateOn.checked,
        wizard_email_override: override.value.trim(),
        staff_greeting_name: greetingName.value.trim(),
      };
      Object.keys(fields).forEach(function (key) { json[key] = fields[key].value; });
      try {
        await ui().api("/hiring/settings", { method: "PUT", json: json });
        await window.clyHiring.open({ message: "Saved." });
      } catch (error) {
        ui().setStatus($("hiring-status"), error.message, "error");
      }
    };
    const testMail = document.createElement("button");
    testMail.type = "button";
    testMail.className = "ghost";
    testMail.textContent = "Send test wizard emails";
    testMail.onclick = async function () {
      testMail.disabled = true;
      try {
        const result = await ui().api("/hiring/wizard-email-test", { method: "POST", json: {} });
        ui().setStatus(
          $("hiring-status"),
          "Test emails: staff " + result.staff + ", candidate " + result.candidate + ".",
          "ok"
        );
      } catch (error) {
        ui().setStatus($("hiring-status"), error.message, "error");
      } finally {
        testMail.disabled = false;
      }
    };
    settingsBox.appendChild(saveMail);
    settingsBox.appendChild(previewCandidate);
    settingsBox.appendChild(previewStaff);
    settingsBox.appendChild(testMail);
    settingsBox.appendChild(previewSubject);
    settingsBox.appendChild(previewFrame);
    root.appendChild(settingsBox);
    const steps = document.createElement("p");
    steps.className = "meta";
    steps.textContent = "Each offer shows its own step.";
    root.appendChild(steps);

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
      keptEditId = "";
      keptOffer = null;
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
    const hasEdit = Object.prototype.hasOwnProperty.call(opts, "editId");
    const editId = hasEdit ? (opts.editId || "") : keptEditId;
    if (opts.offer && opts.offer.id) keptOffer = opts.offer;

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
      const meta = document.createElement("div");
      meta.className = "due-meta";
      meta.textContent =
        (offer.employment_type || "") +
        " · respond by " +
        (offer.response_deadline || "") +
        (offer.direct_client_access ? " · direct client access" : "");
      left.appendChild(title);
      left.appendChild(meta);
      const driveLine = document.createElement("div");
      driveLine.className = "due-meta hire-drive-note";
      driveLine.textContent = offer.drive_note || "\u00a0";
      left.appendChild(driveLine);
      row.dataset.offerId = offer.id;
      row.dataset.version = offer.current_version ? String(offer.current_version) : "";
      const linkedHire = (data.hires || []).filter(function (hire) { return hire.offer_id === offer.id; })[0];
      title.textContent = offerHeading(offer, linkedHire);
      if (linkedHire) left.appendChild(stageBar(linkedHire.stage, linkedHire.owner, data.billing_label));
      else {
        const offerStage = offer.status === "draft" ? "offer" : offer.status;
        left.appendChild(stageBar(offerStage, "admin", data.billing_label));
      }
      left.appendChild(stepper(wizardSteps(offer, linkedHire)));
      const buttons = document.createElement("div");
      buttons.className = "row hire-actions";
      function button(text, className, action) {
        const node = document.createElement("button");
        node.type = "button";
        node.className = className;
        node.textContent = text;
        node.onclick = action;
        buttons.appendChild(node);
      }
      button(offer.status === "draft" ? "Edit" : "View", "ghost", function () {
        editing.value = offer.id;
        fillForm(offer);
        ui().setStatus(
          $("hiring-status"),
          offer.status === "accepted" ? "Accepted offers can't be edited." : "Editing this draft or starting a new version.",
          "ok"
        );
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
      if (offer.drive_retry) {
        button("Retry", "ghost", function () {
          retryDrive(offer.id);
        });
      }
      if (offer.status === "sent" || offer.status === "viewed") {
        button("Revise offer", "ghost", function () {
          ui()
            .api("/hiring/offers/" + offer.id + "/revise", { method: "POST", json: {} })
            .then(function (saved) {
              return window.clyHiring.open({
                editId: (saved && saved.id) || offer.id,
                offer: saved,
                message: "New draft started. Mark it sent again.",
              });
            })
            .catch(function (error) {
              showStatus((error && error.message) || "Could not revise.", "error");
            });
        });
      }
      if (offer.status === "draft") {
        button("Mark sent", "primary", function () {
          post(offer.id, "/send", { channel: "outside" }, "Marked sent.", true);
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
        askDialog({
          title: "Delete offer for " + (offer.display_name || "this candidate"),
          label: "Type the candidate's name",
          expected: offer.display_name || "",
          confirmText: "Delete offer",
          anchor: row,
          onConfirm: function (typed) {
            showStatus("Deleting…", "ok");
            ui()
              .api("/hiring/offers/" + offer.id + "/delete", { method: "POST", json: { confirm: typed } })
              .then(function () {
                document.querySelectorAll('[data-offer-id="' + offer.id + '"]').forEach(function (node) {
                  node.remove();
                });
                const editingNow = document.getElementById("hire-editing");
                if (editingNow && editingNow.value === offer.id) {
                  editingNow.value = "";
                  fillForm(null);
                }
                const decision = document.getElementById("hire-decision");
                if (decision) decision.remove();
                showStatus("Offer deleted.", "ok");
              })
              .catch(function (error) {
                showStatus((error && error.message) || "Could not delete.", "error");
              });
          },
        });
      });
      if (offer.status === "sent" || offer.status === "viewed") {
        button("Record acceptance", "primary", function () {
          showDecision(offer, "accept", row);
        });
        button("Record decline", "ghost", function () {
          showDecision(offer, "decline", row);
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
    renderLibrary(root, data.forms || [], settings);
    renderHires(root, data.hires || [], data);
    function usable(offer) {
      return !!(offer && offer.id === editId && offer.fields);
    }
    const listed = offers.filter(function (offer) { return offer.id === editId; })[0];
    const match = (usable(opts.offer) ? opts.offer : null) || listed || (usable(keptOffer) ? keptOffer : null);
    if (editId && match) {
      keptEditId = editId;
      keptOffer = match;
      const editingNow = document.getElementById("hire-editing");
      if (editingNow) editingNow.value = match.id;
      fillForm(match);
    } else {
      keptEditId = "";
      keptOffer = null;
      fillForm(null);
    }
    const wrap = document.getElementById("hire-resume-wrap");
    if (wrap) wrap.hidden = false;
    maybeWatch(offers);
    focusHire(opts.hireId);
  }

  function focusHire(hireId) {
    if (!hireId) return;
    const card = document.getElementById("hire-" + hireId);
    if (card && card.scrollIntoView) card.scrollIntoView();
  }

  function renderBillingHome(root, data, opts) {
    const heading = document.createElement("h3");
    heading.textContent = "Payroll onboarding";
    root.appendChild(heading);
    const hires = data.hires || [];
    if (!hires.length) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "No hires have been handed off.";
      root.appendChild(empty);
      return;
    }
    hires.forEach(function (hire) {
      root.appendChild(renderBillingCard(hire));
    });
    focusHire(opts.hireId);
  }

  function renderBillingCard(hire) {
      const card = document.createElement("article");
      card.className = "hire-card";
      card.id = "hire-" + hire.id;
      const title = document.createElement("div");
      title.className = "due-title";
      title.textContent = (hire.display_name || "Hire") + " — " + (hire.position || "") + " — " + (hire.employment_type || "");
      card.appendChild(title);
      card.appendChild(stageBar(hire.stage, hire.owner, "Billing"));
      const meta = document.createElement("p");
      meta.className = "meta";
      meta.textContent = "Start date " + (hire.start_date || "—");
      card.appendChild(meta);
      renderBillingSteps(card, hire);
      if (hire.status === "onboarding") {
        const clearances = itemByKey(hire, "clearances");
        const needsClearance = hire.direct_client_access && clearances.status !== "done" && clearances.status !== "n/a";
        const beforeStart = !!(hire.start_date && hire.start_date > easternToday());
        const activate = document.createElement("button");
        activate.type = "button";
        activate.className = "primary";
        activate.textContent = "Activate";
        activate.disabled = !!needsClearance || beforeStart;
        if (beforeStart) {
          const why = document.createElement("p");
          why.className = "meta hire-activate-reason";
          why.textContent = "Available on or after " + hire.start_date;
          card.appendChild(why);
        }
        if (needsClearance) {
          const why = document.createElement("p");
          why.className = "meta hire-activate-reason";
          why.textContent = "Mark PA clearances received first";
          card.appendChild(why);
        }
        activate.onclick = function () {
          askDialog({
            title: "Activate " + (hire.display_name || "this hire"),
            label: "Work email",
            confirmText: "Activate",
            anchor: activate,
            onConfirm: function (email) {
              ui()
                .api("/hiring/hires/" + hire.id + "/activate", { method: "POST", json: { work_email: email } })
                .then(function (saved) {
                  refreshHire(saved, "Hire activated.");
                })
                .catch(function (error) {
                  showStatus((error && error.message) || "Could not activate.", "error");
                });
            },
          });
        };
        card.appendChild(activate);
      }
      return card;
  }

  function showDecision(offer, kind, anchor) {
    const existing = document.getElementById("hire-decision");
    if (existing) existing.remove();
    const box = document.createElement("form");
    box.id = "hire-decision";
    box.className = "hire-decision";
    box.addEventListener("submit", function (event) {
      event.preventDefault();
      if (kind === "accept") recordAcceptance(offer.id, box);
      else recordDecline(offer.id, box);
    });
    const title = document.createElement("div");
    title.className = "due-title";
    const who = offer.display_name || "this candidate";
    title.textContent = (kind === "accept" ? "Record acceptance" : "Record decline") + " for " + who;
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
    const working = document.createElement("p");
    working.className = "meta";
    working.hidden = true;
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ghost";
    cancel.textContent = "Cancel";
    cancel.onclick = function () {
      box.remove();
    };
    box.appendChild(confirm);
    box.appendChild(cancel);
    box.appendChild(working);
    if (anchor && anchor.insertAdjacentElement) anchor.insertAdjacentElement("afterend", box);
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
    const confirm = box.querySelector("button.primary");
    if (confirm) {
      confirm.disabled = true;
      confirm.classList.add("busy");
    }
    showBusy("Saving…");
    try {
      const saved = await ui().api("/hiring/offers/" + id + "/accept-paper", { method: "POST", body: body });
      const offer = saved && saved.offer;
      await window.clyHiring.open({ editId: id, message: "Acceptance recorded." });
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

  function renderLibrary(root, forms, settings) {
    const box = document.createElement("section");
    box.style.marginTop = "1.5rem";
    const heading = document.createElement("h3");
    heading.textContent = "Forms";
    box.appendChild(heading);
    const note = document.createElement("p");
    note.className = "meta";
    note.textContent = settings && settings.candidate_emails_enabled
      ? "English forms only. Candidate email is on, so the selected packet is emailed at handoff."
      : "English forms only. Download the packet and send it yourself. Candidate email stays off.";
    box.appendChild(note);
    (forms || []).forEach(function (form) {
      if (!form.available) return;
      const link = document.createElement("button");
      link.type = "button";
      link.className = "ghost";
      link.textContent = form.form_key === "h4c_welcome_template" ? "Download welcome letter (.docx)" : (form.title || form.form_key);
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

  function renderHires(root, hires, data) {
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
      box.appendChild(renderHireCard(hire, data));
    });
    root.appendChild(box);
  }

  function refreshHire(saved, message) {
    if (!saved || !saved.id) return;
    if (lastHiring && lastHiring.hires) {
      let found = false;
      lastHiring.hires = lastHiring.hires.map(function (item) {
        if (item.id === saved.id) {
          found = true;
          return saved;
        }
        return item;
      });
      if (!found) lastHiring.hires.push(saved);
    }
    const existing = document.getElementById("hire-" + saved.id);
    const data = lastHiring || { offers: [], billing_label: "Billing", viewer: "admin" };
    const card = data.viewer === "billing" ? renderBillingCard(saved) : renderHireCard(saved, data);
    if (existing) existing.replaceWith(card);
    syncOfferCard(saved);
    if (message) showStatus(message, "ok");
  }

  function syncOfferCard(hire) {
    const row = document.querySelector('.due-row[data-offer-id="' + hire.offer_id + '"]');
    if (!row) return;
    const title = row.querySelector(".due-title");
    if (title) {
      title.textContent = offerHeading({
        display_name: hire.display_name,
        current_version: row.dataset.version || "",
        status: "accepted",
      }, hire);
    }
    const bar = row.querySelector(".hire-stage");
    if (bar) {
      const label = (lastHiring && lastHiring.billing_label) || "Billing";
      const who = hire.owner === "billing" ? label : "Admin";
      bar.textContent = "Stage: " + (hire.stage || "accepted") + " · Owner: " + who;
    }
  }

  function renderHireCard(hire, data) {
      const card = document.createElement("article");
      card.className = "hire-card";
      card.id = "hire-" + hire.id;
      card.dataset.offerId = hire.offer_id;
      const title = document.createElement("div");
      title.className = "due-title";
      title.textContent = (hire.display_name || "Hire") + " — " + (hire.status || "");
      card.appendChild(title);
      card.appendChild(stageBar(hire.stage, hire.owner, data.billing_label));
      const linkedOffer = (data.offers || []).filter(function (offer) { return offer.id === hire.offer_id; })[0];
      card.appendChild(stepper(wizardSteps(linkedOffer || { id: hire.offer_id, status: "accepted" }, hire)));
      const packetRoot = renderPacketStep(card, hire);
      renderHandoff(card, hire, packetRoot, data.billing_label);
      renderBillingSteps(card, hire);
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
        row.className = "hire-item" + (item.warning ? " late" : "");
        row.dataset.item = item.template_key;
        const label = document.createElement("div");
        label.className = "hire-item-text";
        label.textContent = itemLine(item);
        row.appendChild(label);
        if (item.badge && item.status === "open") {
          const badge = document.createElement("span");
          badge.className = item.badge === "due now" ? "badge-due" : "badge-due";
          badge.textContent = item.badge;
          row.appendChild(badge);
        }
        if (item.kind !== "auto" && item.template_key !== "activate") {
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
              .then(function (saved) {
                refreshHire(saved, "Checklist updated.");
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
        const clearances = (hire.items || []).filter(function (item) { return item.template_key === "clearances"; })[0];
        const needsClearance = hire.direct_client_access && clearances && clearances.status !== "done" && clearances.status !== "n/a";
        const beforeStart = !!(hire.start_date && hire.start_date > easternToday());
        const activate = document.createElement("button");
        activate.type = "button";
        activate.className = "primary";
        activate.textContent = "Activate";
        activate.disabled = !!needsClearance || beforeStart;
        if (beforeStart) {
          const why = document.createElement("p");
          why.className = "meta";
          why.textContent = "Available on or after " + hire.start_date;
          card.appendChild(why);
        }
        if (needsClearance) {
          const why = document.createElement("p");
          why.className = "meta";
          why.textContent = "Mark PA clearances received first";
          card.appendChild(why);
        }
        activate.onclick = function () {
          askDialog({
            title: "Activate " + (hire.display_name || "this hire"),
            label: "Work email",
            confirmText: "Activate",
            anchor: activate,
            onConfirm: function (email) {
              ui()
                .api("/hiring/hires/" + hire.id + "/activate", { method: "POST", json: { work_email: email } })
                .then(function (saved) {
                  refreshHire(saved, "Hire activated.");
                })
                .catch(function (error) {
                  showStatus((error && error.message) || "Could not activate.", "error");
                });
            },
          });
        };
        card.appendChild(activate);
        const packet = document.createElement("button");
        packet.type = "button";
        packet.className = "ghost";
        packet.textContent = hire.packet_complete ? "Packet complete" : "Mark packet complete";
        packet.disabled = !!hire.packet_complete;
        packet.onclick = function () {
          ui()
            .api("/hiring/hires/" + hire.id + "/packet-complete", { method: "POST", json: {} })
            .then(function (saved) {
              refreshHire(saved, "Packet marked complete.");
            })
            .catch(function (error) {
              showStatus((error && error.message) || "Could not save.", "error");
            });
        };
        card.appendChild(packet);
        if (hire.packet_complete) {
          const done = document.createElement("p");
          done.className = "meta";
          done.textContent = "Packet marked complete.";
          card.appendChild(done);
          const undoPacket = document.createElement("button");
          undoPacket.type = "button";
          undoPacket.className = "ghost";
          undoPacket.textContent = "Undo packet";
          undoPacket.onclick = function () {
            ui()
              .api("/hiring/hires/" + hire.id + "/packet-undo", { method: "POST", json: {} })
              .then(function (saved) {
                refreshHire(saved, "Packet completion undone.");
              })
              .catch(function (error) {
                showStatus((error && error.message) || "Could not save.", "error");
              });
          };
          card.appendChild(undoPacket);
        }
      }
      const removeWrap = document.createElement("div");
      removeWrap.className = "hire-action";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost";
      remove.textContent = "Delete hire";
      remove.onclick = function (event) {
        if (event && event.preventDefault) event.preventDefault();
        askDialog({
          title: "Delete hire for " + (hire.display_name || "this candidate"),
          label: "Type the candidate's name",
          expected: hire.display_name || "",
          confirmText: "Delete hire",
          anchor: removeWrap,
          onConfirm: function (typed) {
            remove.disabled = true;
            remove.textContent = "Deleting…";
            ui()
              .api("/hiring/hires/" + hire.id + "/delete", { method: "POST", json: { confirm: typed } })
              .then(function () {
                const card = document.getElementById("hire-" + hire.id);
                if (card) card.remove();
                showStatus("Hire deleted.", "ok");
              })
              .catch(function (error) {
                remove.disabled = false;
                remove.textContent = "Delete hire";
                showStatus((error && error.message) || "Could not delete.", "error");
              });
          },
        });
      };
      removeWrap.appendChild(remove);
      card.appendChild(removeWrap);
      return card;
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
  let keptEditId = "";
  let keptOffer = null;
  let watchingId = "";
  let lastHiring = null;

  async function saveOffer() {
    if (saving) return;
    saving = true;
    const saveButton = formEl() && formEl().querySelector("button[type=submit]");
    if (saveButton) saveButton.disabled = true;
    clearFieldErrors();
    showBusy("Saving…");
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
      if (error && error.field) showFieldError(error.field, error.message);
      else showStatus((error && error.message) || "Could not save.", "error");
    } finally {
      saving = false;
      const button = formEl() && formEl().querySelector("button[type=submit]");
      if (button) button.disabled = false;
    }
  }

  function markField(name) {
    clearFieldErrors();
    if (!name) return null;
    const el = fieldNode(name) || (formEl() && formEl().querySelector('[name="' + String(name).replace(/"/g, "") + '"]'));
    if (!el || !el.classList) return null;
    el.classList.add("field-error");
    return el;
  }

  function showFieldError(name, message) {
    const el = markField(name);
    if (!el) {
      showStatus(message, "error");
      return;
    }
    const note = document.createElement("div");
    note.className = "field-error-text";
    note.textContent = message;
    el.insertAdjacentElement("afterend", note);
    if (el.scrollIntoView) el.scrollIntoView({ block: "center" });
    try {
      if (el.focus) el.focus();
    } catch (ignore) {
      /* The message is already under the field. */
    }
    showStatus(message, "error");
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
