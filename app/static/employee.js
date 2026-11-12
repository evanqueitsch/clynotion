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
    root.appendChild(select);
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
    const input = document.createElement("input");
    input.type = "text";
    input.value = (data.specialties || []).join(", ");
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
    root.appendChild(input);
    root.appendChild(save);
  }

  function renderTrainings(data) {
    const root = $("employee-trainings");
    root.innerHTML = "";
    const rows = [];
    function addRow(item) {
      const name = document.createElement("input");
      name.type = "text";
      name.placeholder = "Training";
      name.value = (item && item.name) || "";
      const on = document.createElement("input");
      on.type = "date";
      on.value = (item && item.completed_on) || "";
      rows.push({ name, on });
      root.appendChild(name);
      root.appendChild(on);
    }
    (data.trainings || []).forEach(addRow);
    if (!rows.length) addRow(null);
    const more = document.createElement("button");
    more.type = "button";
    more.className = "ghost";
    more.textContent = "Add training";
    more.addEventListener("click", () => addRow(null));
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save trainings";
    save.addEventListener("click", async () => {
      const items = rows
        .map((row) => ({ name: row.name.value.trim(), completed_on: row.on.value }))
        .filter((item) => item.name);
      try {
        const saved = await ui().api("/employee/me/trainings", { method: "PUT", json: { items } });
        ui().setStatus($("employee-status"), "Saved", "ok");
        renderSelf(saved);
      } catch (error) {
        ui().setStatus($("employee-status"), error.message || "Not saved", "error");
      }
    });
    root.appendChild(more);
    root.appendChild(save);
  }

  function renderHistory(data) {
    const root = $("employee-history");
    root.innerHTML = "";
    (data.history || []).forEach((item) => {
      const row = document.createElement("p");
      row.className = "meta";
      row.textContent = item.action + " " + item.field;
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
