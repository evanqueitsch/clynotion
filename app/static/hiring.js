/* Hiring offers. Pay stays in the form response and is not written to the console. */
(function () {
  const FIELDS = [
    ["candidate_full_name", "Full legal name", "text", "Test Candidate QA"],
    ["candidate_first_name", "First name", "text", "Test"],
    ["candidate_credentials", "Credentials (blank if none)", "text", ""],
    ["personal_email", "Personal email", "email", "qa.candidate@example.com"],
    ["position_title", "Position (blank uses the default)", "text", ""],
    ["supervisor_title", "Supervisor title", "text", "Director"],
    ["supervisor_name", "Supervisor name", "text", "Alex Supervisor"],
    ["hourly_rate", "Hourly rate", "text", "$45.00"],
    ["licensed_rate", "Licensed rate (optional)", "text", ""],
    ["caseload_range", "Caseload range", "text", "25-30"],
    ["qsehra_monthly_amount", "QSEHRA monthly amount", "text", "$250"],
    ["qsehra_wait_days", "QSEHRA wait days", "text", "60"],
    ["offer_date", "Offer date (MM/DD/YYYY)", "text", ""],
    ["start_date", "Start date (MM/DD/YYYY)", "text", ""],
    ["response_deadline", "Response deadline (blank uses the setting)", "text", ""],
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

  function readForm() {
    const body = {
      employment_type: fieldValue("hire-type"),
      direct_client_access: $("hire-client") ? $("hire-client").checked : true,
      display_name: fieldValue("hire-candidate_full_name"),
    };
    FIELDS.forEach(function (field) {
      body[field[0]] = fieldValue("hire-" + field[0]);
    });
    return body;
  }

  function fillForm(offer) {
    const fields = (offer && offer.fields) || {};
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
      } else {
        node.value = field[3] || "";
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
    if (caseload && !caseload.value) caseload.placeholder = part ? "12-18" : "25-30";
  }

  function render(data) {
    const root = $("hiring-root");
    root.innerHTML = "";
    const settings = data.settings || {};
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
        ui().setStatus($("hiring-status"), "Saved.", "ok");
        await window.clyHiring.open();
      } catch (error) {
        ui().setStatus($("hiring-status"), error.message, "error");
      }
    };
    settingsBox.appendChild(label);
    settingsBox.appendChild(saveDays);
    root.appendChild(settingsBox);

    const form = document.createElement("div");
    form.style.marginTop = "1rem";
    const typeLabel = document.createElement("label");
    typeLabel.textContent = "Employment type";
    const type = document.createElement("select");
    type.id = "hire-type";
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
    client.type = "checkbox";
    client.checked = true;
    clientLabel.appendChild(client);
    form.appendChild(clientLabel);
    FIELDS.forEach(function (field) {
      const row = document.createElement("label");
      row.textContent = field[1];
      const input = document.createElement("input");
      input.id = "hire-" + field[0];
      input.type = field[2];
      row.appendChild(input);
      form.appendChild(row);
    });
    const editing = document.createElement("input");
    editing.id = "hire-editing";
    editing.type = "hidden";
    form.appendChild(editing);
    const actions = document.createElement("div");
    actions.className = "row";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save offer";
    save.onclick = saveOffer;
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "ghost";
    clear.textContent = "New offer";
    clear.onclick = function () {
      editing.value = "";
      fillForm(null);
      ui().setStatus($("hiring-status"), "");
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
  }

  async function saveOffer() {
    const body = readForm();
    const id = fieldValue("hire-editing");
    try {
      if (id) {
        await ui().api("/hiring/offers/" + id, { method: "PUT", json: body });
      } else {
        await ui().api("/hiring/offers", { method: "POST", json: body });
      }
      ui().setStatus($("hiring-status"), "Saved.", "ok");
      await window.clyHiring.open();
    } catch (error) {
      ui().setStatus($("hiring-status"), error.message, "error");
    }
  }

  async function post(id, suffix, json, ok) {
    try {
      await ui().api("/hiring/offers/" + id + suffix, { method: "POST", json: json });
      ui().setStatus($("hiring-status"), ok, "ok");
      await window.clyHiring.open();
    } catch (error) {
      ui().setStatus($("hiring-status"), error.message, "error");
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
    open: async function () {
      ui().show("hiring-panel");
      ui().setStatus($("hiring-status"), "Loading…");
      try {
        const data = await ui().api("/hiring/bootstrap");
        ui().setStatus($("hiring-status"), "");
        render(data);
      } catch (error) {
        ui().setStatus($("hiring-status"), error.message || "Could not open hiring", "error");
      }
    },
  };
})();
