(function () {
  "use strict";

  var bubble = document.getElementById("afaq-chat-bubble");
  var panel = document.getElementById("afaq-chat-panel");
  var closeBtn = document.getElementById("afaq-chat-close");
  var messages = document.getElementById("afaq-chat-messages");
  var loading = document.getElementById("afaq-chat-loading");
  var errorBox = document.getElementById("afaq-chat-error");
  var form = document.getElementById("afaq-chat-form");
  var input = document.getElementById("afaq-chat-input");

  if (!bubble || !panel || !form || !input) return;

  function openPanel() {
    panel.hidden = false;
    bubble.setAttribute("aria-expanded", "true");
    input.focus();
    if (!messages.dataset.greeted) {
      addMessage("Hi! Ask me about events happening in Riyadh.", "assistant", "en");
      messages.dataset.greeted = "true";
    }
  }

  function closePanel() {
    panel.hidden = true;
    bubble.setAttribute("aria-expanded", "false");
  }

  bubble.addEventListener("click", function () {
    if (panel.hidden) {
      openPanel();
    } else {
      closePanel();
    }
  });

  closeBtn.addEventListener("click", closePanel);

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function addMessage(text, role, lang) {
    var row = document.createElement("div");
    row.className = "afaq-chat-msg afaq-chat-msg-" + role;
    row.dir = lang === "ar" ? "rtl" : "ltr";
    row.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
    messages.appendChild(row);
    messages.scrollTop = messages.scrollHeight;
    return row;
  }

  function showPreferenceConfirm(categories) {
    var row = document.createElement("div");
    row.className = "afaq-chat-pref-confirm";

    var label = document.createElement("span");
    label.textContent = "Save as your preferences: " + categories.join(", ") + "?";
    row.appendChild(label);

    var actions = document.createElement("div");
    actions.className = "afaq-chat-pref-actions";

    var yesBtn = document.createElement("button");
    yesBtn.type = "button";
    yesBtn.className = "afaq-chat-pref-yes";
    yesBtn.textContent = "Yes, save";
    yesBtn.addEventListener("click", function () {
      fetch("/chatbot/preferences/confirm", { method: "POST" })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          row.remove();
          if (data.saved && data.saved.length) {
            addMessage("Saved: " + data.saved.join(", ") + ".", "assistant", "en");
          } else {
            addMessage("You need to be logged in to save preferences.", "assistant", "en");
          }
        })
        .catch(function () {
          addMessage("Could not save your preferences right now.", "assistant", "en");
        });
    });

    var noBtn = document.createElement("button");
    noBtn.type = "button";
    noBtn.className = "afaq-chat-pref-no";
    noBtn.textContent = "No";
    noBtn.addEventListener("click", function () {
      fetch("/chatbot/preferences/decline", { method: "POST" }).catch(function () {});
      row.remove();
    });

    actions.appendChild(yesBtn);
    actions.appendChild(noBtn);
    row.appendChild(actions);
    messages.appendChild(row);
    messages.scrollTop = messages.scrollHeight;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text) return;

    addMessage(text, "user", "en");
    input.value = "";
    errorBox.hidden = true;
    loading.hidden = false;

    fetch("/chatbot/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("bad_response");
        return res.json();
      })
      .then(function (data) {
        loading.hidden = true;
        addMessage(data.reply || "", "assistant", data.lang);
        if (data.pending_preferences && data.pending_preferences.length) {
          showPreferenceConfirm(data.pending_preferences);
        }
      })
      .catch(function () {
        loading.hidden = true;
        errorBox.hidden = false;
        errorBox.textContent = "Something went wrong. Please try again.";
      });
  });
})();
