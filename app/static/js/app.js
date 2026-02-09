// Bibliotheca Oratorii Sacratissimorum Cordium — main application script
document.addEventListener("DOMContentLoaded", function () {
    // ── Mobile nav toggle ──────────────────────────────────────────
    var toggle = document.querySelector(".nav-toggle");
    var navList = document.querySelector(".nav-list");
    if (toggle && navList) {
        toggle.addEventListener("click", function () {
            var expanded = toggle.getAttribute("aria-expanded") === "true";
            toggle.setAttribute("aria-expanded", String(!expanded));
            navList.classList.toggle("nav-list--open");
        });
    }

    // ── Flash message dismiss ──────────────────────────────────────
    document.querySelectorAll(".flash-dismiss").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var el = btn.parentElement;
            el.style.opacity = "0";
            el.style.transform = "translateY(-10px)";
            setTimeout(function () { el.remove(); }, 300);
        });
    });

    // ── Confirmation dialogs via data-confirm ──────────────────────
    document.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-confirm]");
        if (btn) {
            if (!confirm(btn.getAttribute("data-confirm"))) {
                e.preventDefault();
            }
        }
    });

    // ── Form submission via data-submit-form ───────────────────────
    document.addEventListener("click", function (e) {
        var trigger = e.target.closest("[data-submit-form]");
        if (trigger) {
            e.preventDefault();
            var form = document.getElementById(trigger.getAttribute("data-submit-form"));
            if (form) form.submit();
        }
    });

    // ── Splash page enter transition ───────────────────────────────
    var enterBtn = document.getElementById("enter-btn");
    if (enterBtn && enterBtn.classList.contains("splash-enter")) {
        enterBtn.addEventListener("click", function (e) {
            e.preventDefault();
            var dest = enterBtn.href;
            document.body.style.transition = "opacity 1.2s ease";
            document.body.style.opacity = "0";
            setTimeout(function () { window.location.href = dest; }, 1200);
        });
    }

    // ── Service worker registration ────────────────────────────────
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/static/sw.js").catch(function () {});
    }
});
