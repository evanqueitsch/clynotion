/* Employee file. Contact values stay in the form and are not written to the console. */
(function () {
  const CONTACT = [
    ["preferred_name", "Preferred name"],
    ["street", "Street"],
    ["city", "City"],
    ["state", "State"],
    ["zip", "ZIP"],
    ["personal_phone", "Personal phone"],
    ["personal_email", "Personal email"],
    ["work_phone", "Work phone"],
    ["emergency_name", "Emergency contact name"],
    ["emergency_phone", "Emergency contact phone"],
  ];

  const PHONE_FIELDS = new Set(["personal_phone", "work_phone", "emergency_phone"]);
  const PHONE_HINT = "must be a 10-digit US number like (555)010-0100, or leave it blank.";

  const US_STATES = [
    ["AL", "Alabama"],
    ["AK", "Alaska"],
    ["AZ", "Arizona"],
    ["AR", "Arkansas"],
    ["CA", "California"],
    ["CO", "Colorado"],
    ["CT", "Connecticut"],
    ["DE", "Delaware"],
    ["DC", "District of Columbia"],
    ["FL", "Florida"],
    ["GA", "Georgia"],
    ["HI", "Hawaii"],
    ["ID", "Idaho"],
    ["IL", "Illinois"],
    ["IN", "Indiana"],
    ["IA", "Iowa"],
    ["KS", "Kansas"],
    ["KY", "Kentucky"],
    ["LA", "Louisiana"],
    ["ME", "Maine"],
    ["MD", "Maryland"],
    ["MA", "Massachusetts"],
    ["MI", "Michigan"],
    ["MN", "Minnesota"],
    ["MS", "Mississippi"],
    ["MO", "Missouri"],
    ["MT", "Montana"],
    ["NE", "Nebraska"],
    ["NV", "Nevada"],
    ["NH", "New Hampshire"],
    ["NJ", "New Jersey"],
    ["NM", "New Mexico"],
    ["NY", "New York"],
    ["NC", "North Carolina"],
    ["ND", "North Dakota"],
    ["OH", "Ohio"],
    ["OK", "Oklahoma"],
    ["OR", "Oregon"],
    ["PA", "Pennsylvania"],
    ["RI", "Rhode Island"],
    ["SC", "South Carolina"],
    ["SD", "South Dakota"],
    ["TN", "Tennessee"],
    ["TX", "Texas"],
    ["UT", "Utah"],
    ["VT", "Vermont"],
    ["VA", "Virginia"],
    ["WA", "Washington"],
    ["WV", "West Virginia"],
    ["WI", "Wisconsin"],
    ["WY", "Wyoming"],
  ];

  function canonicalState(value) {
    const text = String(value || "").trim().replace(/\s+/g, " ");
    if (!text) return "";
    const folded = text.toLowerCase().replace(/\.+$/, "");
    const byName = US_STATES.find((pair) => pair[1].toLowerCase() === folded);
    if (byName) return byName[0];
    const letters = folded.replace(/[^a-z]/g, "");
    if (letters.length === 2) {
      const byAbbr = US_STATES.find((pair) => pair[0].toLowerCase() === letters);
      if (byAbbr) return byAbbr[0];
    }
    return null;
  }

  function formatUsPhone(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    let digits = text.replace(/\D/g, "");
    if (digits.length === 11 && digits.charAt(0) === "1") digits = digits.slice(1);
    if (digits.length !== 10) return null;
    return "(" + digits.slice(0, 3) + ")" + digits.slice(3, 6) + "-" + digits.slice(6);
  }

  function displayPhone(stored) {
    const formatted = formatUsPhone(stored);
    if (formatted === null) return String(stored == null ? "" : stored);
    return formatted;
  }

  function phoneError(label) {
    return label + " " + PHONE_HINT;
  }

  function statePicker(stored) {
    const select = document.createElement("select");
    select.dataset.field = "state";
    select.autocomplete = "address-level1";
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "State";
    select.appendChild(blank);
    const canonical = canonicalState(stored);
    const raw = String(stored || "");
    if (raw && canonical === null) {
      const odd = document.createElement("option");
      odd.value = raw;
      odd.textContent = raw;
      select.appendChild(odd);
    }
    US_STATES.forEach((pair) => {
      const option = document.createElement("option");
      option.value = pair[0];
      option.textContent = pair[1];
      select.appendChild(option);
    });
    if (canonical) select.value = canonical;
    else if (raw) select.value = raw;
    else select.value = "";
    return select;
  }

  function stateField(label, stored) {
    const wrap = document.createElement("label");
    wrap.textContent = label;
    const select = statePicker(stored);
    wrap.appendChild(select);
    return { wrap, input: select };
  }

  function clearPhoneStatus(ctx) {
    const status = $(ctx.statusId);
    if (status && status.textContent.indexOf(PHONE_HINT) !== -1) ui().setStatus(status, "");
  }

  function onPhoneBlur(input, label, loaded, ctx) {
    const formatted = formatUsPhone(input.value);
    if (formatted !== null) {
      input.value = formatted;
      clearPhoneStatus(ctx);
      return;
    }
    if (input.value.trim() === String(loaded || "").trim()) {
      input.value = loaded;
      return;
    }
    ui().setStatus($(ctx.statusId), phoneError(label), "error");
  }

  function bindPhone(input, field, label, ctx) {
    const loaded = input.value;
    input.dataset.field = field;
    input.type = "tel";
    input.inputMode = "tel";
    input.autocomplete = "tel";
    input.addEventListener("blur", () => onPhoneBlur(input, label, loaded, ctx));
  }

  const FALLBACK_FIELDS = [
    ["license_type", "License type"],
    ["npi", "NPI"],
    ["license_number", "License number"],
    ["caqh_id", "CAQH ID"],
    ["license_expires_on", "License expiration"],
    ["caqh_attested_on", "CAQH attestation"],
    ["license_renewal", "License renewal"],
  ];

  function $(id) {
    return document.getElementById(id);
  }

  function ui() {
    return window.clynotion;
  }

  function formatUsDate(value) {
    const raw = String(value || "").trim();
    const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!match) return raw;
    return String(Number(match[2])) + "/" + String(Number(match[3])) + "/" + match[1];
  }

  function showCredentialing(data) {
    if (typeof data.show_credentialing === "boolean") return data.show_credentialing;
    const suggested = data.suggested_subject || {};
    return !!(data.is_clinician || data.subject_id || data.credentialing || suggested.id);
  }

  function fieldLabel(data, field) {
    const match = (data.change_fields || []).find((item) => item.id === field);
    if (match && match.label) return match.label;
    const fallback = FALLBACK_FIELDS.find((item) => item[0] === field);
    return fallback ? fallback[1] : field;
  }

  function historySection(events) {
    const wrap = document.createElement("div");
    const count = (events || []).length;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost history-toggle";
    button.textContent = count ? "Change history (" + count + ")" : "Change history";
    button.setAttribute("aria-expanded", "false");
    const panel = document.createElement("div");
    panel.className = "history-log hidden";
    if (!count) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "No changes yet.";
      panel.appendChild(empty);
    } else {
      events.forEach((item) => {
        const row = document.createElement("p");
        row.className = "meta";
        row.textContent = item.line || "Change recorded.";
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

  function inputField(label, value) {
    const wrap = document.createElement("label");
    wrap.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.value = value || "";
    wrap.appendChild(input);
    return { wrap, input };
  }

  async function saveSection(ctx, url, body) {
    try {
      const saved = await ui().api(url, { method: "PUT", json: body });
      ui().setStatus($(ctx.statusId), "Saved", "ok");
      await ctx.reload(saved);
    } catch (error) {
      ui().setStatus($(ctx.statusId), error.message || "Not saved", "error");
    }
  }

  function renderIdentity(root, data) {
    root.innerHTML = "";
    const preferred = data.contact && data.contact.preferred_name;
    const usablePreferred = preferred && preferred !== "saved" && preferred !== "empty" ? preferred : "";
    const name = document.createElement("h3");
    name.textContent = data.name || usablePreferred || data.email || "Employee file";
    root.appendChild(name);
    const license = data.credentialing && data.credentialing.license_type;
    if (license) {
      const sub = document.createElement("p");
      sub.className = "meta";
      sub.textContent = license;
      root.appendChild(sub);
    }
  }

  function renderContact(root, data, ctx) {
    root.innerHTML = "";
    const revealed = data.contact_revealed !== false;
    if (ctx.editable && revealed && ctx.contactUrl) {
      const inputs = {};
      CONTACT.forEach((pair) => {
        const stored = (data.contact && data.contact[pair[0]]) || "";
        const row = pair[0] === "state"
          ? stateField(pair[1], stored)
          : inputField(pair[1], PHONE_FIELDS.has(pair[0]) ? displayPhone(stored) : stored);
        inputs[pair[0]] = row.input;
        if (PHONE_FIELDS.has(pair[0])) bindPhone(row.input, pair[0], pair[1], ctx);
        root.appendChild(row.wrap);
      });
      const save = document.createElement("button");
      save.type = "button";
      save.className = "primary";
      save.textContent = "Save contact";
      save.addEventListener("click", () => {
        const body = {};
        CONTACT.forEach((pair) => {
          const field = pair[0];
          const el = inputs[field];
          if (PHONE_FIELDS.has(field)) {
            const formatted = formatUsPhone(el.value);
            if (formatted !== null) el.value = formatted;
          }
          body[field] = el.value.trim();
        });
        saveSection(ctx, ctx.contactUrl, body);
      });
      root.appendChild(save);
      return;
    }
    const contact = data.contact || {};
    CONTACT.forEach((pair) => {
      const row = document.createElement("p");
      row.className = "meta";
      let value = contact[pair[0]] || "";
      if (PHONE_FIELDS.has(pair[0])) value = displayPhone(value);
      row.textContent = pair[1] + ": " + (value || "empty");
      root.appendChild(row);
    });
    if (!revealed && ctx.revealUrl) {
      const reveal = document.createElement("button");
      reveal.type = "button";
      reveal.className = "ghost";
      reveal.textContent = "Reveal contact";
      reveal.addEventListener("click", async () => {
        try {
          const opened = await ui().api(ctx.revealUrl, { method: "POST" });
          data.contact = opened;
          data.contact_revealed = true;
          renderContact(root, data, ctx);
        } catch (error) {
          ui().setStatus($(ctx.statusId), error.message || "Could not reveal contact", "error");
        }
      });
      root.appendChild(reveal);
    }
  }

  function renderSpecialties(root, data, ctx) {
    root.innerHTML = "";
    const names = data.specialties || [];
    if (!ctx.editable || !ctx.specialtiesUrl) {
      const row = document.createElement("p");
      row.className = "meta";
      row.textContent = names.length ? names.join(", ") : "No specialties yet.";
      root.appendChild(row);
      return;
    }
    const label = document.createElement("label");
    label.textContent = "Specialties";
    const input = document.createElement("input");
    input.type = "text";
    input.value = names.join(", ");
    label.appendChild(input);
    const hint = document.createElement("p");
    hint.className = "meta";
    hint.textContent = "Separate specialties with commas. These save immediately.";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save specialties";
    save.addEventListener("click", () => {
      const next = input.value.split(",").map((item) => item.trim()).filter(Boolean);
      saveSection(ctx, ctx.specialtiesUrl, { names: next });
    });
    root.appendChild(label);
    root.appendChild(hint);
    root.appendChild(save);
  }

  function renderEditableList(root, options) {
    root.innerHTML = "";
    const items = options.items.slice();
    const canEdit = !!(options.ctx.editable && options.url);
    const list = document.createElement("div");
    function draw() {
      list.innerHTML = "";
      items.forEach((item, index) => {
        const row = document.createElement("div");
        row.className = "due-row";
        const text = document.createElement("span");
        text.textContent = options.rowText(item);
        row.appendChild(text);
        if (canEdit) {
          const remove = document.createElement("button");
          remove.type = "button";
          remove.className = "ghost";
          remove.textContent = "Remove";
          remove.addEventListener("click", () => {
            items.splice(index, 1);
            draw();
          });
          row.appendChild(remove);
        }
        list.appendChild(row);
      });
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "meta";
        empty.textContent = options.emptyText;
        list.appendChild(empty);
      }
    }
    draw();
    root.appendChild(list);
    if (!canEdit) {
      if (options.ctx.editable && !options.url) {
        const note = document.createElement("p");
        note.className = "meta";
        note.textContent = "Link a Workspace account before saving this section.";
        root.appendChild(note);
      }
      return;
    }
    const draft = options.draft();
    root.appendChild(draft.node);
    const add = document.createElement("button");
    add.type = "button";
    add.className = "ghost";
    add.textContent = options.addLabel;
    add.addEventListener("click", () => {
      const next = draft.read();
      if (!next) {
        ui().setStatus($(options.ctx.statusId), options.missingMessage, "error");
        return;
      }
      items.push(next);
      draft.clear();
      draw();
    });
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = options.saveLabel;
    save.addEventListener("click", () => {
      const pending = draft.read();
      const payload = pending ? items.concat([pending]) : items.slice();
      saveSection(options.ctx, options.url, { items: payload });
    });
    root.appendChild(add);
    root.appendChild(save);
  }

  function trainingOptions(data, ctx) {
    return {
      ctx,
      items: (data.trainings || []).map((item) => ({ name: item.name, completed_on: item.completed_on || "" })),
      emptyText: "No trainings yet.",
      addLabel: "Add training",
      saveLabel: "Save trainings",
      missingMessage: "Enter a training name.",
      url: ctx.trainingsUrl,
      rowText: (item) => item.name + (item.completed_on ? " — " + formatUsDate(item.completed_on) : ""),
      draft: () => {
        const node = document.createElement("div");
        const nameLabel = document.createElement("label");
        nameLabel.textContent = "Training name";
        const name = document.createElement("input");
        name.type = "text";
        nameLabel.appendChild(name);
        const dateLabel = document.createElement("label");
        dateLabel.textContent = "Completed on";
        const on = document.createElement("input");
        on.type = "date";
        dateLabel.appendChild(on);
        node.appendChild(nameLabel);
        node.appendChild(dateLabel);
        return {
          node,
          read: () => {
            const trainingName = name.value.trim();
            if (!trainingName) return null;
            return { name: trainingName, completed_on: on.value };
          },
          clear: () => {
            name.value = "";
            on.value = "";
          },
        };
      },
    };
  }

  function certificationOptions(data, ctx) {
    return {
      ctx,
      items: (data.certifications || []).map((item) => ({
        name: item.name,
        issued_on: item.issued_on || "",
        expires_on: item.expires_on || "",
      })),
      emptyText: "No certifications yet.",
      addLabel: "Add certification",
      saveLabel: "Save certifications",
      missingMessage: "Enter a certification name.",
      url: ctx.certificationsUrl,
      rowText: (item) => {
        const issued = item.issued_on ? " issued " + formatUsDate(item.issued_on) : "";
        const expires = item.expires_on ? " expires " + formatUsDate(item.expires_on) : "";
        return item.name + issued + expires;
      },
      draft: () => {
        const node = document.createElement("div");
        const nameLabel = document.createElement("label");
        nameLabel.textContent = "Certification";
        const name = document.createElement("input");
        name.type = "text";
        nameLabel.appendChild(name);
        const issuedLabel = document.createElement("label");
        issuedLabel.textContent = "Issued on";
        const issued = document.createElement("input");
        issued.type = "date";
        issuedLabel.appendChild(issued);
        const expiresLabel = document.createElement("label");
        expiresLabel.textContent = "Expires on";
        const expires = document.createElement("input");
        expires.type = "date";
        expiresLabel.appendChild(expires);
        node.appendChild(nameLabel);
        node.appendChild(issuedLabel);
        node.appendChild(expiresLabel);
        return {
          node,
          read: () => {
            const certificationName = name.value.trim();
            if (!certificationName) return null;
            return { name: certificationName, issued_on: issued.value, expires_on: expires.value };
          },
          clear: () => {
            name.value = "";
            issued.value = "";
            expires.value = "";
          },
        };
      },
    };
  }

  function renderProfileLink(root, data, ctx) {
    if (data.workspace_link) return;
    const suggested = data.suggested_subject || {};
    if (!data.subject_id && !suggested.id && !ctx.linkUrl) return;
    const note = document.createElement("p");
    note.className = "meta";
    if (data.subject_id) note.textContent = "Credentialing profile linked.";
    else if (suggested.id) note.textContent = "Suggested profile: " + suggested.name + ". Nothing is linked until you confirm.";
    else note.textContent = "Nothing is linked until you confirm.";
    root.appendChild(note);
    if (!ctx.linkUrl) return;
    const label = document.createElement("label");
    label.textContent = "Credentialing profile";
    const select = document.createElement("select");
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "Choose a profile";
    select.appendChild(empty);
    (data.candidates || []).forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name;
      select.appendChild(option);
    });
    if (data.subject_id) select.value = data.subject_id;
    else if (suggested.id) select.value = suggested.id;
    label.appendChild(select);
    root.appendChild(label);
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = data.subject_id ? "Update link" : "Link profile";
    save.addEventListener("click", () => {
      if (!select.value) {
        ui().setStatus($(ctx.statusId), "Choose a profile to link.", "error");
        return;
      }
      saveSection(ctx, ctx.linkUrl, { subject_id: select.value });
    });
    root.appendChild(save);
    if (data.subject_id) {
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "ghost";
      clear.textContent = "Unlink";
      clear.addEventListener("click", () => saveSection(ctx, ctx.linkUrl, { subject_id: "" }));
      root.appendChild(clear);
    }
  }

  function renderCredentialing(root, data, ctx) {
    root.innerHTML = "";
    if (!showCredentialing(data)) return;
    const block = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = "Credentialing";
    block.appendChild(title);
    const snapshot = data.credentialing || null;
    if (snapshot) {
      const lines = [
        ["License type", snapshot.license_type || ""],
        ["License expires", snapshot.license_expires_on ? formatUsDate(snapshot.license_expires_on) : ""],
        ["NPI", snapshot.npi_masked || ""],
        ["License number", snapshot.license_number_masked || ""],
        ["CAQH ID", snapshot.caqh_id_masked || ""],
        ["CAQH attested", snapshot.caqh_attested_on ? formatUsDate(snapshot.caqh_attested_on) : ""],
        ["Hire date", snapshot.hire_date ? formatUsDate(snapshot.hire_date) : ""],
        ["Device", snapshot.device || ""],
      ];
      lines.forEach((pair) => {
        if (!pair[1]) return;
        const row = document.createElement("p");
        row.className = "meta";
        row.textContent = pair[0] + ": " + pair[1];
        block.appendChild(row);
      });
      const clocks = snapshot.clocks || {};
      const labels = snapshot.clock_labels || {};
      Object.keys(clocks).forEach((key) => {
        if (!clocks[key]) return;
        const row = document.createElement("p");
        row.className = "meta";
        const label = labels[key] || fieldLabel(data, key);
        row.textContent = label + ": " + formatUsDate(clocks[key]);
        block.appendChild(row);
      });
    }
    renderProfileLink(block, data, ctx);
    if (ctx.changesUrl && data.is_clinician) {
      const request = document.createElement("div");
      const field = document.createElement("select");
      const choices = (data.change_fields && data.change_fields.length) ? data.change_fields : FALLBACK_FIELDS.map((item) => ({ id: item[0], label: item[1] }));
      choices.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.label;
        field.appendChild(option);
      });
      const value = document.createElement("input");
      value.type = "text";
      value.placeholder = "Requested value";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.textContent = "Request a change";
      button.addEventListener("click", async () => {
        try {
          const saved = await ui().api(ctx.changesUrl, {
            method: "POST",
            json: { field: field.value, value: value.value.trim() },
          });
          ui().setStatus($(ctx.statusId), "Submitted for approval", "ok");
          await ctx.reload(saved);
        } catch (error) {
          ui().setStatus($(ctx.statusId), error.message || "Not saved", "error");
        }
      });
      request.appendChild(field);
      request.appendChild(value);
      request.appendChild(button);
      block.appendChild(request);
    }
    (data.pending || []).forEach((item) => {
      const row = document.createElement("p");
      row.className = "meta";
      const label = item.field_label || fieldLabel(data, item.field);
      row.textContent = "Waiting: " + label + (item.overdue ? " (overdue)" : "");
      block.appendChild(row);
    });
    root.appendChild(block);
  }

  function renderFileSections(data, ctx, targets) {
    renderIdentity(targets.identity, data);
    renderContact(targets.contact, data, ctx);
    renderSpecialties(targets.specialties, data, ctx);
    renderEditableList(targets.trainings, trainingOptions(data, ctx));
    renderEditableList(targets.certifications, certificationOptions(data, ctx));
    renderCredentialing(targets.credentialing, data, ctx);
    targets.history.innerHTML = "";
    targets.history.appendChild(historySection(data.history || []));
  }

  function selfContext() {
    return {
      statusId: "employee-status",
      editable: true,
      contactUrl: "/employee/me/contact",
      specialtiesUrl: "/employee/me/specialties",
      trainingsUrl: "/employee/me/trainings",
      certificationsUrl: "/employee/me/certifications",
      linkUrl: "/employee/me/link",
      changesUrl: "/employee/me/changes",
      revealUrl: "",
      reload: async (saved) => {
        if (saved && saved.contact) renderSelf(saved);
      },
    };
  }

  function renderSelf(data) {
    renderFileSections(data, selfContext(), {
      identity: $("employee-identity"),
      contact: $("employee-contact"),
      specialties: $("employee-specialties"),
      trainings: $("employee-trainings"),
      certifications: $("employee-certifications"),
      credentialing: $("employee-credentialing"),
      history: $("employee-history"),
    });
  }

  async function openSelf() {
    ui().show("employee-panel");
    ui().setStatus($("employee-status"), "Loading…");
    try {
      const data = await ui().api("/employee/me");
      ui().setStatus($("employee-status"), "");
      renderSelf(data);
    } catch (error) {
      ui().setStatus($("employee-status"), error.message || "Could not open your record", "error");
    }
  }

  async function openDirectory() {
    ui().show("employees-panel");
    const status = $("employees-status");
    ui().setStatus(status, "Loading…");
    try {
      const data = await ui().api("/employee/directory");
      ui().setStatus(status, "");
      const wrap = $("employees-rollout-wrap");
      const box = $("employees-rollout");
      wrap.classList.toggle("hidden", !data.can_rollout);
      box.checked = !!data.rollout_enabled;
      box.onchange = async () => {
        try {
          await ui().api("/employee/rollout", { method: "PUT", json: { enabled: box.checked } });
          ui().setStatus(status, "Saved", "ok");
        } catch (error) {
          box.checked = !box.checked;
          ui().setStatus(status, error.message || "Not saved", "error");
        }
      };
      const list = $("employees-directory");
      list.innerHTML = "";
      (data.people || []).forEach((person) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "ghost";
        const specialties = (person.specialties || []).join(", ");
        row.textContent = person.name + (specialties ? " — " + specialties : "");
        row.addEventListener("click", () => openPerson(person.key));
        list.appendChild(row);
      });
      if (!(data.people || []).length) list.textContent = "No staff records yet.";
    } catch (error) {
      ui().setStatus(status, error.message || "Could not open the directory", "error");
    }
  }

  function renderWorkspaceLink(root, key, data) {
    const block = document.createElement("div");
    const link = data.workspace_link;
    if (!link) {
      if (data.workspace_account) {
        const account = document.createElement("p");
        account.className = "meta";
        account.textContent = "Workspace account: " + data.workspace_account;
        block.appendChild(account);
        root.appendChild(block);
      }
      return;
    }
    const label = document.createElement("label");
    label.textContent = "Workspace account";
    const select = document.createElement("select");
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "Choose a Workspace account";
    select.appendChild(empty);
    (link.accounts || []).forEach((account) => {
      const option = document.createElement("option");
      option.value = account.email;
      option.textContent = account.name + " (" + account.email + ")";
      select.appendChild(option);
    });
    if (link.linked_email) select.value = link.linked_email;
    else if (link.suggested_email) select.value = link.suggested_email;
    label.appendChild(select);
    const note = document.createElement("p");
    note.className = "meta";
    note.textContent = link.linked_email
      ? "Linked to " + link.linked_email + "."
      : (link.suggested_email
        ? "Suggested: " + link.suggested_name + ". Nothing is linked until you confirm."
        : "Nothing is linked until you confirm.");
    block.appendChild(label);
    block.appendChild(note);
    if (data.can_edit) {
      const confirm = document.createElement("button");
      confirm.type = "button";
      confirm.className = "primary";
      confirm.textContent = "Confirm link";
      confirm.addEventListener("click", async () => {
        if (!select.value) {
          ui().setStatus($("employees-status"), "Choose a Workspace account to link.", "error");
          return;
        }
        try {
          await ui().api("/employee/people/" + encodeURIComponent(key) + "/workspace", {
            method: "PUT",
            json: { email: select.value },
          });
          ui().setStatus($("employees-status"), "Saved", "ok");
          await openPerson(key);
        } catch (error) {
          ui().setStatus($("employees-status"), error.message || "Not saved", "error");
        }
      });
      block.appendChild(confirm);
      if (link.linked_email) {
        const unlink = document.createElement("button");
        unlink.type = "button";
        unlink.className = "ghost";
        unlink.textContent = "Unlink";
        unlink.addEventListener("click", async () => {
          try {
            await ui().api("/employee/people/" + encodeURIComponent(key) + "/workspace", {
              method: "PUT",
              json: { email: "" },
            });
            ui().setStatus($("employees-status"), "Saved", "ok");
            await openPerson(key);
          } catch (error) {
            ui().setStatus($("employees-status"), error.message || "Not saved", "error");
          }
        });
        block.appendChild(unlink);
      }
    }
    root.appendChild(block);
  }

  function hrContext(key, data) {
    const email = data.email || "";
    const editable = data.can_edit === true;
    const encoded = encodeURIComponent(key);
    return {
      statusId: "employees-status",
      editable,
      contactUrl: email && editable ? "/employee/people/" + encodeURIComponent(email) + "/contact" : "",
      specialtiesUrl: editable ? "/employee/people/" + encoded + "/specialties" : "",
      trainingsUrl: email && editable ? "/employee/people/" + encoded + "/trainings" : "",
      certificationsUrl: email && editable ? "/employee/people/" + encoded + "/certifications" : "",
      linkUrl: email && editable ? "/employee/people/" + encodeURIComponent(email) + "/profile" : "",
      changesUrl: "",
      revealUrl: email && data.can_reveal ? "/employee/people/" + encodeURIComponent(email) + "/reveal" : "",
      reload: async () => {
        await openPerson(key);
      },
    };
  }

  function section(root, title, hint) {
    const heading = document.createElement("h3");
    heading.textContent = title;
    root.appendChild(heading);
    if (hint) {
      const note = document.createElement("p");
      note.className = "meta";
      note.textContent = hint;
      root.appendChild(note);
    }
    const body = document.createElement("div");
    root.appendChild(body);
    return body;
  }

  async function openPerson(key) {
    const root = $("employees-record");
    root.innerHTML = "";
    try {
      const data = await ui().api("/employee/people/" + encodeURIComponent(key));
      const identity = document.createElement("div");
      identity.className = "file-identity";
      root.appendChild(identity);
      renderWorkspaceLink(root, key, data);
      const contact = section(root, "Contact and emergency contacts");
      const specialties = section(root, "Specialties");
      const trainings = section(root, "Trainings");
      const certifications = section(
        root,
        "Certifications",
        "Examples: CPR/First Aid and mandated reporter. These save on the employee file and do not change credentialing."
      );
      const credentialing = document.createElement("div");
      root.appendChild(credentialing);
      const history = document.createElement("div");
      root.appendChild(history);
      renderFileSections(data, hrContext(key, data), {
        identity,
        contact,
        specialties,
        trainings,
        certifications,
        credentialing,
        history,
      });
      root.scrollIntoView({ block: "nearest" });
    } catch (error) {
      const message = document.createElement("p");
      message.className = "status error";
      message.textContent = error.message || "Could not open that record";
      root.appendChild(message);
      ui().setStatus($("employees-status"), error.message || "Could not open that record", "error");
    }
  }

  window.clyEmployee = { openSelf, openDirectory };
  const home = $("btn-employee-home");
  if (home) home.addEventListener("click", () => { window.location.href = "/"; });
  const directoryHome = $("btn-employees-home");
  if (directoryHome) directoryHome.addEventListener("click", () => { window.location.href = "/"; });
  const directory = $("btn-employee-directory");
  if (directory) {
    directory.addEventListener("click", () => {
      window.location.hash = "#employees";
    });
  }
})();
