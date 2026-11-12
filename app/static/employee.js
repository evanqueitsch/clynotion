/* Employee View. Contact values stay in the form and are not written to the console. */
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

  function $(id) {
    return document.getElementById(id);
  }

  function ui() {
    return window.clynotion;
  }

  function field(label, value) {
    const wrap = document.createElement("label");
    wrap.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.value = value || "";
    wrap.appendChild(input);
    return { wrap, input };
  }

  function renderContact(data) {
    const root = $("employee-contact");
    root.innerHTML = "";
    const inputs = {};
    CONTACT.forEach((pair) => {
      const row = field(pair[1], (data.contact && data.contact[pair[0]]) || "");
      inputs[pair[0]] = row.input;
      root.appendChild(row.wrap);
    });
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save contact";
    save.addEventListener("click", async () => {
      const body = {};
      CONTACT.forEach((pair) => {
        body[pair[0]] = inputs[pair[0]].value.trim();
      });
      try {
        const saved = await ui().api("/employee/me/contact", { method: "PUT", json: body });
        ui().setStatus($("employee-status"), "Saved", "ok");
        renderSelf(saved);
      } catch (error) {
        ui().setStatus($("employee-status"), error.message || "Not saved", "error");
      }
    });
    root.appendChild(save);
  }

  function renderLink(data) {
    const root = $("employee-link");
    root.innerHTML = "";
    if (data.subject_id) {
      const note = document.createElement("p");
      note.className = "meta";
      note.textContent = "Credentialing profile linked.";
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "ghost";
      clear.textContent = "Unlink";
      clear.addEventListener("click", async () => {
        try {
          const saved = await ui().api("/employee/me/link", { method: "PUT", json: { subject_id: "" } });
          renderSelf(saved);
        } catch (error) {
          ui().setStatus($("employee-status"), error.message || "Not saved", "error");
        }
      });
      root.appendChild(note);
      root.appendChild(clear);
      return;
    }
    const note = document.createElement("p");
    note.className = "meta";
    const suggested = data.suggested_subject || {};
    note.textContent = suggested.id
      ? "Suggested profile: " + suggested.name + ". Nothing is linked until you confirm."
      : "Link your credentialing profile if you are a clinician. A suggestion is not saved until you confirm.";
    root.appendChild(note);
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
    if (suggested.id) select.value = suggested.id;
    label.appendChild(select);
    root.appendChild(label);
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Link profile";
    save.addEventListener("click", async () => {
      if (!select.value) {
        ui().setStatus($("employee-status"), "Choose a profile to link.", "error");
        return;
      }
      try {
        const saved = await ui().api("/employee/me/link", { method: "PUT", json: { subject_id: select.value } });
        ui().setStatus($("employee-status"), "Saved", "ok");
        renderSelf(saved);
      } catch (error) {
        ui().setStatus($("employee-status"), error.message || "Not saved", "error");
      }
    });
    root.appendChild(save);
  }

  function renderCredentialing(data) {
    const root = $("employee-credentialing");
    root.innerHTML = "";
    if (!data.is_clinician) return;
    const snapshot = data.credentialing || {};
    const block = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = "Credentialing";
    block.appendChild(title);
    const lines = [
      ["License type", snapshot.license_type || ""],
      ["License expires", snapshot.license_expires_on || ""],
      ["NPI", snapshot.npi_masked || ""],
      ["License number", snapshot.license_number_masked || ""],
      ["CAQH ID", snapshot.caqh_id_masked || ""],
      ["CAQH attested", snapshot.caqh_attested_on || ""],
      ["Hire date", snapshot.hire_date || ""],
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
    Object.keys(clocks).forEach((key) => {
      if (!clocks[key]) return;
      const row = document.createElement("p");
      row.className = "meta";
      row.textContent = key + ": " + clocks[key];
      block.appendChild(row);
    });
    const request = document.createElement("div");
    const field = document.createElement("select");
    ["license_type", "npi", "license_number", "caqh_id", "license_expires_on", "caqh_attested_on", "license_renewal"].forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
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
        const saved = await ui().api("/employee/me/changes", {
          method: "POST",
          json: { field: field.value, value: value.value.trim() },
        });
        ui().setStatus($("employee-status"), "Submitted for approval", "ok");
        renderSelf(saved);
      } catch (error) {
        ui().setStatus($("employee-status"), error.message || "Not saved", "error");
      }
    });
    request.appendChild(field);
    request.appendChild(value);
    request.appendChild(button);
    block.appendChild(request);
    const waiting = document.createElement("div");
    (data.pending || []).forEach((item) => {
      const row = document.createElement("p");
      row.className = "meta";
      row.textContent = "Waiting: " + item.field + (item.overdue ? " (overdue)" : "");
      waiting.appendChild(row);
    });
    block.appendChild(waiting);
    root.appendChild(block);
  }

  function renderSpecialties(data) {
    const root = $("employee-specialties");
    root.innerHTML = "";
    const label = document.createElement("label");
    label.textContent = "Specialties";
    const input = document.createElement("input");
    input.type = "text";
    input.value = (data.specialties || []).join(", ");
    label.appendChild(input);
    const hint = document.createElement("p");
    hint.className = "meta";
    hint.textContent = "Separate specialties with commas.";
    root.appendChild(label);
    root.appendChild(hint);
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save specialties";
    save.addEventListener("click", async () => {
      const names = input.value.split(",").map((item) => item.trim()).filter(Boolean);
      try {
        const saved = await ui().api("/employee/me/specialties", { method: "PUT", json: { names } });
        ui().setStatus($("employee-status"), "Saved", "ok");
        renderSelf(saved);
      } catch (error) {
        ui().setStatus($("employee-status"), error.message || "Not saved", "error");
      }
    });
    root.appendChild(save);
  }

  function renderEditableList(root, options) {
    root.innerHTML = "";
    const items = options.items.slice();
    const list = document.createElement("div");
    function draw() {
      list.innerHTML = "";
      items.forEach((item, index) => {
        const row = document.createElement("div");
        row.className = "due-row";
        const text = document.createElement("span");
        text.textContent = options.rowText(item);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "ghost";
        remove.textContent = "Remove";
        remove.addEventListener("click", () => {
          items.splice(index, 1);
          draw();
        });
        row.appendChild(text);
        row.appendChild(remove);
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
    const draft = options.draft();
    root.appendChild(draft.node);
    const add = document.createElement("button");
    add.type = "button";
    add.className = "ghost";
    add.textContent = options.addLabel;
    add.addEventListener("click", () => {
      const next = draft.read();
      if (!next) {
        ui().setStatus($("employee-status"), options.missingMessage, "error");
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
    save.addEventListener("click", async () => {
      const pending = draft.read();
      const payload = pending ? items.concat([pending]) : items.slice();
      try {
        const saved = await ui().api(options.url, { method: "PUT", json: { items: payload } });
        ui().setStatus($("employee-status"), "Saved", "ok");
        renderSelf(saved);
      } catch (error) {
        ui().setStatus($("employee-status"), error.message || "Not saved", "error");
      }
    });
    root.appendChild(add);
    root.appendChild(save);
  }

  function renderTrainings(data) {
    renderEditableList($("employee-trainings"), {
      items: (data.trainings || []).map((item) => ({ name: item.name, completed_on: item.completed_on || "" })),
      emptyText: "No trainings yet.",
      addLabel: "Add training",
      saveLabel: "Save trainings",
      missingMessage: "Enter a training name.",
      url: "/employee/me/trainings",
      rowText: (item) => item.name + (item.completed_on ? " — " + item.completed_on : ""),
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
    });
  }

  function renderCertifications(data) {
    const root = $("employee-certifications");
    root.innerHTML = "";
    if (!data.is_clinician) return;
    const title = document.createElement("h3");
    title.textContent = "Certifications";
    root.appendChild(title);
    const hint = document.createElement("p");
    hint.className = "meta";
    hint.textContent = "Examples: NCC, CADC, CPR/First Aid, mandated reporter. These save on your record and do not change credentialing.";
    root.appendChild(hint);
    const holder = document.createElement("div");
    root.appendChild(holder);
    renderEditableList(holder, {
      items: (data.certifications || []).map((item) => ({
        name: item.name,
        issued_on: item.issued_on || "",
        expires_on: item.expires_on || "",
      })),
      emptyText: "No certifications yet.",
      addLabel: "Add certification",
      saveLabel: "Save certifications",
      missingMessage: "Enter a certification name.",
      url: "/employee/me/certifications",
      rowText: (item) => item.name,
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
    });
  }

  function renderHistory(data) {
    const root = $("employee-history");
    root.innerHTML = "";
    (data.history || []).forEach((item) => {
      const row = document.createElement("p");
      row.className = "meta";
      row.textContent = item.line || "";
      root.appendChild(row);
    });
    if (!(data.history || []).length) root.textContent = "No changes yet.";
  }

  function renderSelf(data) {
    renderLink(data);
    renderContact(data);
    renderCredentialing(data);
    renderSpecialties(data);
    renderTrainings(data);
    renderCertifications(data);
    renderHistory(data);
    const directory = document.createElement("button");
    directory.type = "button";
    directory.className = "ghost";
    directory.textContent = "Staff directory";
    directory.addEventListener("click", () => {
      window.location.hash = "#employees";
    });
    $("employee-history").appendChild(directory);
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

  function renderDirectoryLink(root, key, data) {
    const block = document.createElement("div");
    if (data.workspace_link) {
      const link = data.workspace_link;
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
          await openDirectory();
        } catch (error) {
          ui().setStatus($("employees-status"), error.message || "Not saved", "error");
        }
      });
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
      block.appendChild(label);
      block.appendChild(note);
      block.appendChild(confirm);
      if (link.linked_email) block.appendChild(unlink);
      root.appendChild(block);
      return;
    }
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
    const suggested = data.suggested_subject || {};
    if (data.subject_id) select.value = data.subject_id;
    else if (suggested.id) select.value = suggested.id;
    label.appendChild(select);
    const note = document.createElement("p");
    note.className = "meta";
    note.textContent = data.subject_id
      ? "Credentialing profile linked."
      : (suggested.id
        ? "Suggested profile: " + suggested.name + ". Nothing is linked until you confirm."
        : "Nothing is linked until you confirm.");
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "primary";
    confirm.textContent = "Confirm link";
    confirm.addEventListener("click", async () => {
      if (!select.value) {
        ui().setStatus($("employees-status"), "Choose a profile to link.", "error");
        return;
      }
      try {
        await ui().api("/employee/people/" + encodeURIComponent(key) + "/profile", {
          method: "PUT",
          json: { subject_id: select.value },
        });
        ui().setStatus($("employees-status"), "Saved", "ok");
        await openPerson(key);
      } catch (error) {
        ui().setStatus($("employees-status"), error.message || "Not saved", "error");
      }
    });
    const unlink = document.createElement("button");
    unlink.type = "button";
    unlink.className = "ghost";
    unlink.textContent = "Unlink";
    unlink.addEventListener("click", async () => {
      try {
        await ui().api("/employee/people/" + encodeURIComponent(key) + "/profile", {
          method: "PUT",
          json: { subject_id: "" },
        });
        ui().setStatus($("employees-status"), "Saved", "ok");
        await openPerson(key);
      } catch (error) {
        ui().setStatus($("employees-status"), error.message || "Not saved", "error");
      }
    });
    block.appendChild(label);
    block.appendChild(note);
    block.appendChild(confirm);
    if (data.subject_id) block.appendChild(unlink);
    root.appendChild(block);
  }

  async function openPerson(key) {
    const root = $("employees-record");
    root.innerHTML = "";
    try {
      const data = await ui().api("/employee/people/" + encodeURIComponent(key));
      const title = document.createElement("h3");
      title.textContent = data.name || data.email || "Record";
      root.appendChild(title);
      const contact = data.contact || {};
      CONTACT.forEach((pair) => {
        const row = document.createElement("p");
        row.className = "meta";
        const value = contact[pair[0]];
        row.textContent = pair[1] + ": " + (value || "empty");
        root.appendChild(row);
      });
      if (data.credentialing) {
        const snap = document.createElement("p");
        snap.className = "meta";
        const license = data.credentialing.license_type || "not set";
        snap.textContent = "Credentialing profile: " + (data.credentialing.name || data.name || "Linked") + ". License type: " + license;
        root.appendChild(snap);
      }
      renderDirectoryLink(root, key, data);
    } catch (error) {
      ui().setStatus($("employees-status"), error.message || "Could not open that record", "error");
    }
  }

  window.clyEmployee = { openSelf, openDirectory };
  const home = $("btn-employee-home");
  if (home) home.addEventListener("click", () => { window.location.href = "/"; });
  const directoryHome = $("btn-employees-home");
  if (directoryHome) directoryHome.addEventListener("click", () => { window.location.href = "/"; });
})();
