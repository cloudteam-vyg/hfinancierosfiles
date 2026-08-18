(function () {
  "use strict";

  var utils = window.HFUtils; // ver files/static/files/js/hf_utils.js

  // --- pila de overlays -----------------------------------------------------
  // Los modales se ANIDAN LÓGICAMENTE, nunca en el DOM: cada .modal-overlay es
  // hermano de los demás, al nivel superior de app_content. Si un overlay
  // viviera dentro de otro, tres cosas se rompen a la vez: el
  // querySelectorAll("[data-modal-close]") del padre cablearía los botones del
  // hijo para cerrar al PADRE, resetModalForm() cogería el primer <form> en
  // orden de documento (el del hijo) y openModal() enfocaría un campo del hijo.
  // "Anidado" aquí solo significa "más arriba en esta pila".
  var openStack = []; // [{ id, returnFocus }, ...] de abajo a arriba

  // Hasta dónde llega la escala de z-index de main.css. El dominio tiene
  // exactamente dos niveles (catálogo dentro de cliente dentro de la página);
  // un tercero se tapa al máximo definido en vez de emitir un data-stack-depth
  // sin regla, que caería al 1000 base y pintaría por orden del DOM.
  var MAX_STACK_DEPTH = 2;

  function stackIndexOf(modalId) {
    for (var i = 0; i < openStack.length; i++) {
      if (openStack[i].id === modalId) return i;
    }
    return -1;
  }

  function topModal() {
    return openStack.length ? openStack[openStack.length - 1] : null;
  }

  function resetModalForm(overlay) {
    var form = overlay.querySelector("form");
    if (!form) return;
    form.reset();
    var errorBox = form.querySelector(".hf-modal-errors");
    if (errorBox) { errorBox.hidden = true; errorBox.textContent = ""; }
    var submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = false;
  }

  /**
   * Primer control ENFOCABLE Y VISIBLE del modal.
   *
   * No vale el primero en orden de documento: dentro de un .hf-combo el
   * <select> nativo va antes que el input del combobox y está recortado a 1px
   * (.hf-sr-only), así que el foco se iría a algo que el usuario no ve.
   */
  function firstVisibleField(overlay) {
    var candidatos = overlay.querySelectorAll("form input, form select, form textarea");
    for (var i = 0; i < candidatos.length; i++) {
      var el = candidatos[i];
      if (el.type === "hidden" || el.disabled) continue;
      if (el.classList.contains("hf-sr-only")) continue;
      if (el.offsetParent === null) continue;
      return el;
    }
    return null;
  }

  // openModal/closeModal trabajan por id (no por referencia al elemento) a
  // propósito: así un botón solo necesita conocer el id del modal
  // (data-modal-open) para poder abrirlo, sin acoplarse a su DOM interno.
  function openModal(modalId, opener) {
    var overlay = document.getElementById(modalId);
    if (!overlay) return;
    if (stackIndexOf(modalId) !== -1) return; // ya abierto: idempotente

    // Se guarda quién lo abrió para devolverle el foco al cerrar. Sin esto, al
    // cerrar un modal hijo el foco vuelve a <body> y se pierde tanto el
    // contexto de Escape como la posición del lector de pantalla. Mismo idiom
    // que el drawer de filearchive_split.js.
    openStack.push({ id: modalId, returnFocus: opener || document.activeElement });

    var depth = Math.min(openStack.length - 1, MAX_STACK_DEPTH);
    if (depth > 0) overlay.dataset.stackDepth = String(depth);

    // Contado por la pila y no con un booleano: cerrar el hijo no debe
    // devolver el scroll a la página mientras el padre sigue abierto.
    document.body.classList.add("scroll-locked");

    overlay.classList.remove("hidden");
    // Forzar un reflow entre quitar .hidden y agregar .is-open: si se
    // agregaran las dos clases en el mismo frame, el navegador colapsa
    // "display:none -> flex, opacity:0 -> 1" en un solo paso y la
    // transición de entrada nunca se dispara.
    void overlay.offsetWidth;
    overlay.classList.add("is-open");

    var firstField = firstVisibleField(overlay);
    if (firstField) firstField.focus();
  }

  function closeOne(entry) {
    var overlay = document.getElementById(entry.id);
    if (!overlay) return;

    overlay.classList.remove("is-open");

    var finish = function (e) {
      // La transición de transform de .modal-content también burbujea hasta el
      // overlay; sin esta guarda podía cerrar el modal antes de tiempo (hoy es
      // inocuo solo por coincidencia de duraciones iguales).
      if (e && e.target !== overlay) return;
      overlay.classList.add("hidden");
      resetModalForm(overlay);
      delete overlay.dataset.stackDepth;
      overlay.removeEventListener("transitionend", finish);
    };
    if (utils.prefersReducedMotion()) {
      finish();
    } else {
      overlay.addEventListener("transitionend", finish);
    }

    var volver = entry.returnFocus;
    if (volver && document.contains(volver) && typeof volver.focus === "function") {
      volver.focus();
    }
  }

  function closeModal(modalId) {
    var i = stackIndexOf(modalId);
    // Esta guarda es también la que evita el bug de cerrar un overlay ya
    // cerrado: antes se registraba un listener de transitionend que nadie
    // consumía, sobrevivía hasta el siguiente openModal y su transición de
    // entrada lo disparaba -- el modal se cerraba de golpe justo al abrirse.
    if (i === -1) return;

    // De arriba hacia abajo: un hijo no puede sobrevivir a su padre. Su
    // <select> destino vive dentro del cuerpo del padre, así que la opción
    // recién creada aterrizaría donde el usuario ya no puede verla.
    for (var j = openStack.length - 1; j > i; j--) {
      closeOne(openStack[j]);
    }
    closeOne(openStack[i]);

    openStack.length = i;
    if (!openStack.length) document.body.classList.remove("scroll-locked");
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
    // (entre ellos el del combobox de hf_searchable_select.js, que con esto
    // reetiqueta su input; la lista la reconstruye al abrirse)
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function initModalTriggers() {
    // Los botones "+ Nuevo"/"+ Nueva" son type="button": el navegador nunca
    // los trata como submit del <form> que los contiene, así que no hace
    // falta preventDefault() para evitar que disparen el envío del
    // formulario principal de subida. Eso sigue valiendo para los botones que
    // están DENTRO de otro modal, así que los "+" anidados no necesitan
    // ningún caso especial: se cablean aquí como los demás.
    document.querySelectorAll("[data-modal-open]").forEach(function (btn) {
      btn.addEventListener("click", function () { openModal(btn.dataset.modalOpen, btn); });
    });

    document.querySelectorAll(".modal-overlay").forEach(function (overlay) {
      overlay.addEventListener("click", function (e) {
        // Los overlays son hermanos, así que un clic en el velo del hijo tiene
        // como target al hijo y no burbujea al padre: cierra solo el hijo.
        if (e.target === overlay) closeModal(overlay.id);
      });
      overlay.querySelectorAll("[data-modal-close]").forEach(function (btn) {
        btn.addEventListener("click", function () { closeModal(overlay.id); });
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      var top = topModal();
      if (!top) return;
      // Solo el de arriba: cerrarlos todos descartaría el formulario del padre
      // a medio llenar. stopPropagation() para que la misma tecla no dispare
      // otros cierres (p. ej. el drawer de /archivos/, si algún día coinciden
      // en una página).
      e.stopPropagation();
      closeModal(top.id);
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
          // Antes, un alta correcta no daba ninguna confirmación más allá de
          // que el <select> cambiaba -- fácil de no ver si el modal padre
          // tapa el campo.
          if (window.HFToast) {
            window.HFToast.show('Se creó "' + result.data.label + '".', "success");
          }
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
