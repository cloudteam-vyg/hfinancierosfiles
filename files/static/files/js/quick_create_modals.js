(function () {
  "use strict";

  var utils = window.HFUtils; // ver files/static/files/js/hf_utils.js

  function resetModalForm(overlay) {
    var form = overlay.querySelector("form");
    if (!form) return;
    form.reset();
    var errorBox = form.querySelector(".hf-modal-errors");
    if (errorBox) { errorBox.hidden = true; errorBox.textContent = ""; }
    var submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = false;
  }

  // openModal/closeModal trabajan por id (no por referencia al elemento) a
  // propósito: así un botón solo necesita conocer el id del modal
  // (data-modal-open) para poder abrirlo, sin acoplarse a su DOM interno.
  function openModal(modalId) {
    var overlay = document.getElementById(modalId);
    if (!overlay) return;

    overlay.classList.remove("hidden");
    // Forzar un reflow entre quitar .hidden y agregar .is-open: si se
    // agregaran las dos clases en el mismo frame, el navegador colapsa
    // "display:none -> flex, opacity:0 -> 1" en un solo paso y la
    // transición de entrada nunca se dispara.
    void overlay.offsetWidth;
    overlay.classList.add("is-open");

    var firstField = overlay.querySelector("form input, form select");
    if (firstField) firstField.focus();
  }

  function closeModal(modalId) {
    var overlay = document.getElementById(modalId);
    if (!overlay) return;

    overlay.classList.remove("is-open");

    var finish = function () {
      overlay.classList.add("hidden");
      resetModalForm(overlay);
      overlay.removeEventListener("transitionend", finish);
    };
    if (utils.prefersReducedMotion()) {
      finish();
    } else {
      overlay.addEventListener("transitionend", finish);
    }
  }

  function showErrors(form, errors) {
    var errorBox = form.querySelector(".hf-modal-errors");
    if (!errorBox) return;
    var messages = utils.formErrorMessages(errors);
    if (!messages.length) messages.push("No se pudo guardar. Revisa los datos e inténtalo de nuevo.");

    errorBox.textContent = "";
    messages.forEach(function (m) {
      var li = document.createElement("li");
      li.textContent = m; // texto plano vía textContent -- nunca innerHTML con datos del servidor
      errorBox.appendChild(li);
    });
    errorBox.hidden = false;
  }

  function addAndSelectOption(select, id, label) {
    if (!select) return;
    var option = document.createElement("option");
    option.value = id;
    option.textContent = label;
    option.selected = true;
    select.appendChild(option);
    // dispara listeners que pudieran depender de un "change" real del select
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function initModalTriggers() {
    // Los botones "+ Nuevo"/"+ Nueva" son type="button": el navegador nunca
    // los trata como submit del <form> que los contiene, así que no hace
    // falta preventDefault() para evitar que disparen el envío del
    // formulario principal de subida.
    document.querySelectorAll("[data-modal-open]").forEach(function (btn) {
      btn.addEventListener("click", function () { openModal(btn.dataset.modalOpen); });
    });

    document.querySelectorAll(".modal-overlay").forEach(function (overlay) {
      overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeModal(overlay.id);
      });
      overlay.querySelectorAll("[data-modal-close]").forEach(function (btn) {
        btn.addEventListener("click", function () { closeModal(overlay.id); });
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      document.querySelectorAll(".modal-overlay.is-open").forEach(function (overlay) {
        closeModal(overlay.id);
      });
    });
  }

  function initQuickCreateForm(form) {
    var url = form.dataset.quickCreateUrl;
    var targetSelect = document.getElementById(form.dataset.targetSelect);
    var overlay = form.closest(".modal-overlay");

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var submitBtn = form.querySelector('button[type="submit"]');
      submitBtn.disabled = true;

      fetch(url, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-CSRFToken": utils.getCookie("csrftoken") },
        credentials: "same-origin",
      }).then(function (resp) {
        return resp.json().then(function (data) { return { ok: resp.ok, data: data }; });
      }).then(function (result) {
        submitBtn.disabled = false;
        if (result.ok) {
          addAndSelectOption(targetSelect, result.data.id, result.data.label);
          if (overlay) closeModal(overlay.id);
        } else {
          showErrors(form, result.data.errors);
        }
      }).catch(function () {
        submitBtn.disabled = false;
        showErrors(form, null);
      });
    });
  }

  function init() {
    initModalTriggers();
    document.querySelectorAll("form[data-quick-create-url]").forEach(initQuickCreateForm);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
