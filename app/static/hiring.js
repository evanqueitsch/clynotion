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

  function valueOf(name) {
    const form = formEl();
    if (!form || !form.elements) return "";
    const el = form.elements.namedItem(name);
    if (!el) return "";
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
      const node = $("hire-" + field[0]);
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

  function render(data, editId) {
    const root = $("hiring-root");
    root.innerHTML = "";
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
      input.id = "hire-" + field[0];
      input.name = field[0];
      input.type = "text";
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
      fillForm(null);
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
    (data.offers || []).forEach(function (offer) {
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
      button("Delete", "ghost", function () {
        const typed = window.prompt("Type the candidate's name to delete this offer");
        if (!typed) return;
        post(offer.id, "/delete", { confirm: typed }, "Deleted.");
      });
      if (offer.status === "sent" || offer.status === "viewed") {
        button("Mark accepted", "primary", function () {
          const typed = (offer.fields || {}).candidate_full_name || offer.display_name;
          post(offer.id, "/accept", { typed_name: typed }, "Marked accepted.");
        });
        button("Mark declined", "ghost", function () {
          post(offer.id, "/decline", { reason: "other", note: "" }, "Marked declined.");
        });
      }
      row.appendChild(left);
      row.appendChild(buttons);
      list.appendChild(row);
    });
    if (!(data.offers || []).length) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "No offers yet.";
      list.appendChild(empty);
    }
    root.appendChild(list);
    if (editId) {
      const match = (data.offers || []).filter(function (offer) { return offer.id === editId; })[0];
      if (match) {
        const editingNow = document.getElementById("hire-editing");
        if (editingNow) editingNow.value = match.id;
        fillForm(match);
      }
    }
  }

  async function saveOffer() {
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
    try {
      if (editingId) {
        await ui().api("/hiring/offers/" + editingId, { method: "PUT", json: payload });
      } else {
        await ui().api("/hiring/offers", { method: "POST", json: payload });
      }
      await window.clyHiring.open({ message: "Saved." });
    } catch (error) {
      showStatus((error && error.message) || "Could not save.", "error");
    }
  }

  async function post(id, suffix, json, ok, keepEdit) {
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
        return res.blob();
      })
      .then(function (blob) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = id + "." + kind;
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
      if (!api) return;
      api.show("hiring-panel");
      try {
        const data = await api.api("/hiring/bootstrap", { cache: "no-store" });
        render(data, options.editId || "");
        showStatus(options.message || "", options.message ? "ok" : "");
      } catch (error) {
        showStatus((error && error.message) || "Could not open hiring.", "error");
      }
    },
  };
})();
