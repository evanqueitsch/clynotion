/* Billing home. No claims submission and no mail send. */
(function () {
  function $(id) {
    return document.getElementById(id);
  }

  function bridge() {
    return window.clynotion;
  }

  async function openBilling() {
    const ui = bridge();
    ui.show("billing-panel");
    window.history.replaceState({}, "", "/#billing");
    const status = $("billing-status");
    ui.setStatus(status, "Loading…");
    try {
      const data = await ui.api("/billing");
      const links = $("billing-links");
      links.innerHTML = "";
      (data.links || []).forEach((link) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ghost";
        button.textContent = link.name;
        button.addEventListener("click", () => {
          const href = link.href || "";
          const hash = href.indexOf("#");
          if (hash !== -1) window.location.hash = href.slice(hash);
        });
        links.appendChild(button);
      });
      const actions = $("billing-actions");
      actions.innerHTML = "";
      (data.action_items || []).forEach((item) => {
        const row = document.createElement("div");
        row.className = "due-row";
        const badge = document.createElement("span");
        badge.className = "meta";
        badge.textContent = item.band === "overdue" ? "Overdue" : "This week";
        const label = document.createElement("button");
        label.type = "button";
        label.className = "ghost";
        label.textContent = item.title;
        label.addEventListener("click", () => {
          const href = item.href || "";
          const hash = href.indexOf("#");
          if (hash !== -1) window.location.hash = href.slice(hash);
        });
        row.appendChild(badge);
        row.appendChild(label);
        actions.appendChild(row);
      });
      if (!(data.action_items || []).length) {
        actions.textContent = "Nothing due this week.";
      }
      const obligations = $("billing-obligations");
      obligations.innerHTML = "";
      (data.obligations || []).forEach((item) => {
        const row = document.createElement("div");
        row.className = "due-row";
        const label = document.createElement("span");
        label.textContent = item.title;
        const done = document.createElement("button");
        done.type = "button";
        done.className = "ghost";
        done.textContent = "Mark done";
        done.addEventListener("click", async () => {
          try {
            await ui.api("/billing/obligations/" + item.obligation_id + "/complete", { method: "POST" });
            ui.setStatus(status, "Saved", "ok");
            await openBilling();
          } catch (error) {
            ui.setStatus(status, error.message || "Not saved", "error");
          }
        });
        row.appendChild(label);
        row.appendChild(done);
        obligations.appendChild(row);
      });
      const changes = $("billing-employee-changes");
      if (changes) {
        changes.innerHTML = "";
        (data.employee_changes || []).forEach((item) => {
          const row = document.createElement("div");
          row.className = "due-row";
          const label = document.createElement("span");
          label.textContent = item.summary + " (" + (item.actor || "") + ")";
          const done = document.createElement("button");
          done.type = "button";
          done.className = "ghost";
          done.textContent = "Mark reviewed";
          done.addEventListener("click", async () => {
            try {
              await ui.api("/billing/employee-changes/" + item.id + "/review", { method: "POST" });
              ui.setStatus(status, "Saved", "ok");
              await openBilling();
            } catch (error) {
              ui.setStatus(status, error.message || "Not saved", "error");
            }
          });
          row.appendChild(label);
          row.appendChild(done);
          changes.appendChild(row);
        });
        if (!(data.employee_changes || []).length) changes.textContent = "No unreviewed employee changes.";
      }
      const pending = $("billing-employee-pending");
      if (pending) {
        pending.innerHTML = "";
        (data.employee_pending || []).forEach((item) => {
          const row = document.createElement("div");
          row.className = "due-row";
          const label = document.createElement("span");
          label.textContent = item.field + (item.proposed ? ": " + item.proposed : "");
          if (item.overdue) {
            const badge = document.createElement("span");
            badge.className = "badge";
            badge.textContent = "Overdue";
            row.appendChild(badge);
          }
          const note = document.createElement("input");
          note.type = "text";
          note.placeholder = "Note to return";
          const approve = document.createElement("button");
          approve.type = "button";
          approve.className = "primary";
          approve.textContent = "Approve";
          approve.addEventListener("click", async () => {
            try {
              await ui.api("/billing/employee-pending/" + item.id + "/approve", { method: "POST" });
              ui.setStatus(status, "Saved", "ok");
              await openBilling();
            } catch (error) {
              ui.setStatus(status, error.message || "Not saved", "error");
            }
          });
          const sendBack = document.createElement("button");
          sendBack.type = "button";
          sendBack.className = "ghost";
          sendBack.textContent = "Return";
          sendBack.addEventListener("click", async () => {
            try {
              await ui.api("/billing/employee-pending/" + item.id + "/return", {
                method: "POST",
                json: { note: note.value },
              });
              ui.setStatus(status, "Saved", "ok");
              await openBilling();
            } catch (error) {
              ui.setStatus(status, error.message || "Not saved", "error");
            }
          });
          row.appendChild(label);
          row.appendChild(note);
          row.appendChild(approve);
          row.appendChild(sendBack);
          pending.appendChild(row);
        });
        if (!(data.employee_pending || []).length) pending.textContent = "No credentialing changes waiting.";
      }
      const box = $("billing-digest");
      box.checked = !!data.digest_enabled;
      box.disabled = !data.can_toggle_digest;
      box.onchange = async () => {
        try {
          await ui.api("/billing/digest", { method: "PUT", json: { enabled: box.checked } });
          ui.setStatus(status, "Saved", "ok");
        } catch (error) {
          box.checked = !box.checked;
          ui.setStatus(status, error.message || "Not saved", "error");
        }
      };
      ui.setStatus(status, "");
    } catch (error) {
      ui.setStatus(status, error.message || "Could not open Billing", "error");
    }
  }

  window.openBilling = openBilling;
  const home = $("btn-billing-home");
  if (home) {
    home.addEventListener("click", () => {
      window.location.href = "/";
    });
  }
})();
