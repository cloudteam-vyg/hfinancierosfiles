(function () {
  "use strict";
  var STORAGE_KEY = "hf-theme";

  function apply(theme) {
    if (theme === "dark" || theme === "light") {
      document.documentElement.setAttribute("data-theme", theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  }

  function current() {
    return document.documentElement.getAttribute("data-theme") || "light";
  }

  function toggle() {
    var next = current() === "dark" ? "light" : "dark";
    try { localStorage.setItem(STORAGE_KEY, next); } catch (e) { /* almacenamiento no disponible */ }
    apply(next);
    updateLabels();
  }

  function updateLabels() {
    var isDark = current() === "dark";
    document.querySelectorAll("[data-theme-label]").forEach(function (el) {
      el.textContent = isDark ? "Modo claro" : "Modo oscuro";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    updateLabels();
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.addEventListener("click", toggle);
    });
  });
})();
