/*
 * AFAQ - AJAX Favorite toggling (Final Sprint Priority 2).
 *
 * Progressive enhancement only: every ".fav-form" still works as a
 * normal POST+redirect if JavaScript is disabled or this file fails
 * to load, because we only intercept submit() -- we never remove the
 * underlying <form>/<button>.
 *
 * Behavior:
 *  - No full page reload, no scroll jump.
 *  - Heart icon flips immediately to the new saved state on success.
 *  - A small floating toast reports success/error without navigating.
 *  - Duplicate clicks are ignored while a request is in flight.
 *  - Button shows a disabled/loading state during the request.
 *  - A session/login expiration (redirect to the login page instead of
 *    JSON) falls back to a normal navigation to that login page --
 *    the one case where leaving the page is actually correct.
 *  - A network error degrades to a toast; it never fakes a toggle.
 */

(function () {
  "use strict";

  function showToast(message, kind) {
    var existing = document.getElementById("afaq-toast");
    if (existing) {
      existing.remove();
    }

    var toast = document.createElement("div");
    toast.id = "afaq-toast";
    toast.className = "afaq-toast afaq-toast-" + (kind || "success");
    toast.setAttribute("role", "status");
    toast.textContent = message;

    document.body.appendChild(toast);

    requestAnimationFrame(function () {
      toast.classList.add("afaq-toast-visible");
    });

    window.setTimeout(function () {
      toast.classList.remove("afaq-toast-visible");
      window.setTimeout(function () {
        toast.remove();
      }, 220);
    }, 4000);
  }

  function setButtonState(button, isFavorited) {
    button.classList.toggle("is-active", isFavorited);
    button.innerHTML = isFavorited ? "♥" : "♡";
    button.setAttribute(
      "aria-label",
      isFavorited ? "Remove from favorites" : "Add to favorites"
    );
    button.title = button.getAttribute("aria-label");
  }

  function handleSubmit(event) {
    var form = event.target.closest(".fav-form");
    if (!form) {
      return;
    }

    // Only intercept when fetch is actually available; otherwise let
    // the browser do a normal form submission.
    if (typeof window.fetch !== "function") {
      return;
    }

    event.preventDefault();

    var button = form.querySelector(".fav-btn");
    if (!button || button.disabled) {
      // Already in flight -- prevent duplicate requests.
      return;
    }

    var scrollY = window.scrollY;
    button.disabled = true;
    button.classList.add("fav-btn-loading");

    var formData = new FormData(form);

    fetch(form.action, {
      method: "POST",
      body: formData,
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
    })
      .then(function (response) {
        var contentType = response.headers.get("content-type") || "";

        if (!contentType.includes("application/json")) {
          // Most likely an expired/anonymous session redirected to the
          // login page. A toast can't recover a lost session, so this
          // is the one case where navigating away is correct.
          window.location.href = response.url || form.action;
          return null;
        }

        return response.json();
      })
      .then(function (data) {
        if (!data) {
          return;
        }

        if (data.ok) {
          setButtonState(button, data.is_favorited);
        }

        showToast(data.message, data.ok ? "success" : "warning");

        // Keep scroll position exactly where the user was.
        window.scrollTo(0, scrollY);
      })
      .catch(function () {
        showToast(
          "Could not reach AFAQ right now. Please try again.",
          "warning"
        );
      })
      .finally(function () {
        button.disabled = false;
        button.classList.remove("fav-btn-loading");
      });
  }

  document.addEventListener("submit", function (event) {
    if (event.target && event.target.classList && event.target.classList.contains("fav-form")) {
      handleSubmit(event);
    }
  });
})();
