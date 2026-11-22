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

  // Auto-grow the <textarea> as the user types multiple lines, up to the
  // max-height set in CSS (after that it scrolls internally instead of
  // pushing the rest of the widget around).
  function autoGrow() {
    input.style.height = "auto";
    input.style.height = input.scrollHeight + "px";
  }

  input.addEventListener("input", autoGrow);

  // Plain Enter sends the message (matching the old single-line input's
  // behavior); Shift+Enter inserts a newline instead, since a <textarea>
  // does not submit on Enter by default.
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.dispatchEvent(new Event("submit", { cancelable: true }));
      }
    }
  });

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // Minimal, safe Markdown-to-HTML for the handful of styles the model
  // actually uses (bold, italic, inline code). Runs AFTER escapeHtml, so
  // every character of the original text is already HTML-safe -- these
  // regexes only ever wrap already-escaped text in <strong>/<em>/<code>,
  // they never introduce raw markup from the message itself. Without
  // this step, a reply like "**Riyadh Season**" showed the literal
  // asterisks in the chat bubble instead of being rendered as bold.
  // Only allow safe-looking URLs into an href -- blocks javascript:/data:/
  // vbscript:/file: schemes and strips anything with whitespace or angle
  // brackets, so a reply's text can never smuggle a script-running link.
  function sanitizeUrl(url) {
    var decoded = url.replace(/&amp;/g, "&");
    if (/^\s*(javascript|data|vbscript|file):/i.test(decoded)) return null;
    if (/[\s<>]/.test(decoded)) return null;
    return decoded.replace(/"/g, "%22");
  }

  function renderInlineMarkdown(escapedText) {
    var html = escapedText
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*(?!\*)(.+?)\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/`(.+?)`/g, "<code>$1</code>");

    // Markdown links: [label](url). AFAQ's event-detail and official-link
    // tool results come back in this form -- without this step they showed
    // up as literal "[label](url)" text instead of a clickable link.
    html = html.replace(/\[([^\[\]]+)\]\(([^()\s]+)\)/g, function (match, label, url) {
      var safeUrl = sanitizeUrl(url);
      if (!safeUrl) return match; // leave unrecognized/unsafe URLs as plain text
      return '<a href="' + safeUrl + '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
    });

    return html;
  }

  function addMessage(text, role, lang) {
    var row = document.createElement("div");
    row.className = "afaq-chat-msg afaq-chat-msg-" + role;
    row.dir = lang === "ar" ? "rtl" : "ltr";
    var escaped = escapeHtml(text).replace(/\n/g, "<br>");
    row.innerHTML = renderInlineMarkdown(escaped);
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

  // Confirms saving ONE specific event to Favorites (distinct from
  // showPreferenceConfirm above, which is for a general category).
  function showFavoriteConfirm(favorite) {
    var row = document.createElement("div");
    row.className = "afaq-chat-pref-confirm";

    var label = document.createElement("span");
    label.textContent = 'Save "' + favorite.title + '" to your favorites?';
    row.appendChild(label);

    var actions = document.createElement("div");
    actions.className = "afaq-chat-pref-actions";

    var yesBtn = document.createElement("button");
    yesBtn.type = "button";
    yesBtn.className = "afaq-chat-pref-yes";
    yesBtn.textContent = "Yes, save";
    yesBtn.addEventListener("click", function () {
      fetch("/chatbot/favorites/confirm", { method: "POST" })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          row.remove();
          if (data.saved) {
            addMessage('Saved "' + data.title + '" to your favorites.', "assistant", "en");
          } else {
            addMessage("Could not save that event right now.", "assistant", "en");
          }
        })
        .catch(function () {
          addMessage("Could not save that event right now.", "assistant", "en");
        });
    });

    var noBtn = document.createElement("button");
    noBtn.type = "button";
    noBtn.className = "afaq-chat-pref-no";
    noBtn.textContent = "No";
    noBtn.addEventListener("click", function () {
      fetch("/chatbot/favorites/decline", { method: "POST" }).catch(function () {});
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
    autoGrow();
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
        if (data.pending_favorite) {
          showFavoriteConfirm(data.pending_favorite);
        }
      })
      .catch(function () {
        loading.hidden = true;
        errorBox.hidden = false;
        errorBox.textContent = "Something went wrong. Please try again.";
      });
  });
})();
