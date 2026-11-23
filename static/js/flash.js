/*
 * Green success messages (.afaq-autohide) used to sit at the top of the
 * page until the next reload -- so after an action further down, the
 * page jumped up to show them. Now every one is shown as the same
 * floating green toast, visible wherever you are on the page, and
 * disappears on its own after the same duration as the favorites toast.
 *
 * Also keeps your scroll position across a normal form submit that
 * redirects back to the same page (rating, review, calendar...).
 */
(function () {
  "use strict";

  var DURATION_MS = 4000;
  var SCROLL_KEY = "afaq-scroll-restore";

  function showFlashToasts() {
    var items = document.querySelectorAll(".afaq-autohide");
    if (!items.length) return;

    // Several messages at once: stack them one after another instead of
    // piling them on top of each other.
    var queue = Array.prototype.slice.call(items);
    queue.forEach(function (el, i) {
      el.classList.add("afaq-flash-toast");
      el.style.bottom = 28 + i * 64 + "px";
      requestAnimationFrame(function () {
        el.classList.add("afaq-flash-toast-visible");
      });
      window.setTimeout(function () {
        el.classList.remove("afaq-flash-toast-visible");
        window.setTimeout(function () { el.remove(); }, 250);
      }, DURATION_MS);
    });
  }

  function restoreScroll() {
    try {
      var raw = sessionStorage.getItem(SCROLL_KEY);
      if (!raw) return;
      sessionStorage.removeItem(SCROLL_KEY);
      var saved = JSON.parse(raw);
      if (saved && saved.path === location.pathname && saved.y > 0) {
        window.scrollTo(0, saved.y);
      }
    } catch (e) {}
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.method || form.method.toLowerCase() !== "post") return;
    if (form.classList.contains("fav-form")) return;
    if (form.id === "smart-schedule-form") return;
    // Another handler already cancelled it (AJAX forms) -> nothing to restore.
    window.setTimeout(function () {
      if (event.defaultPrevented) return;
      try {
        sessionStorage.setItem(
          SCROLL_KEY,
          JSON.stringify({ path: location.pathname, y: window.scrollY })
        );
      } catch (e) {}
    }, 0);
  });


  // ---- AFAQ's own toast (replaces the browser's alert() popups) ------
  window.afaqToast = function (message, kind) {
    var old = document.getElementById("afaq-notice-toast");
    if (old) old.remove();
    var t = document.createElement("div");
    t.id = "afaq-notice-toast";
    t.className = "afaq-toast afaq-toast-" + (kind || "warning");
    t.setAttribute("role", "status");
    t.textContent = message;
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.classList.add("afaq-toast-visible"); });
    window.setTimeout(function () {
      t.classList.remove("afaq-toast-visible");
      window.setTimeout(function () { t.remove(); }, 250);
    }, DURATION_MS);
  };

  // ---- AFAQ's own confirm dialog (replaces the browser's confirm()) ---
  // Any <form data-confirm="Question?"> asks first. Optional
  // data-confirm-yes="Label" changes the confirm button text.
  function showConfirm(message, yesLabel, onYes) {
    var overlay = document.createElement("div");
    overlay.className = "afaq-modal-overlay";
    overlay.innerHTML =
      '<div class="afaq-modal" role="dialog" aria-modal="true">' +
      '<p class="afaq-modal-text"></p>' +
      '<div class="afaq-modal-actions">' +
      '<button type="button" class="afaq-modal-cancel">Cancel</button>' +
      '<button type="button" class="afaq-modal-yes"></button>' +
      "</div></div>";
    overlay.querySelector(".afaq-modal-text").textContent = message;
    var yes = overlay.querySelector(".afaq-modal-yes");
    yes.textContent = yesLabel || "Yes, remove";

    function close() {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
    }
    function onKey(e) { if (e.key === "Escape") close(); }

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });
    overlay.querySelector(".afaq-modal-cancel").addEventListener("click", close);
    yes.addEventListener("click", function () { close(); onYes(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    yes.focus();
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.dataset || !form.dataset.confirm) return;
    if (form.dataset.confirmed === "1") return;
    event.preventDefault();
    showConfirm(form.dataset.confirm, form.dataset.confirmYes, function () {
      form.dataset.confirmed = "1";
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.submit();
      }
    });
  });

  // ---- In-page "Back" button ----------------------------------------
  // Goes back to wherever the user came from on AFAQ (e.g. their Smart
  // Schedule), and falls back to the link's own href when the page was
  // opened directly.
  document.addEventListener("click", function (event) {
    var link = event.target.closest("[data-back]");
    if (!link) return;
    var sameSite = false;
    try {
      sameSite = document.referrer &&
        new URL(document.referrer).origin === location.origin;
    } catch (e) {}
    if (sameSite && window.history.length > 1) {
      event.preventDefault();
      window.history.back();
    }
  });

  function init() {
    restoreScroll();
    showFlashToasts();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
