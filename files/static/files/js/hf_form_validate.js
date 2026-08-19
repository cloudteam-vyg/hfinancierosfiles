/* Validación en cliente de los formularios marcados con data-hf-validate.
 *
 * Requiere hf_utils.js; usa hf_toast.js si está cargado (con guarda, para que
 * una página pueda no incluirlo).
 *
 * Se apoya en la Constraint Validation API del navegador en vez de
 * reimplementar reglas: el HTML que renderiza Django ya trae required,
 * type=email, type=url y maxlength.
 */
(function () {
  "use strict";

  // el.validationMessage viene en el idioma del NAVEGADOR, no en el es-mx de
  // la app, así que los mensajes se escriben aquí.
  var MESSAGES = {
    valueMissing: "Este campo es obligatorio.",
    typeMismatch: "El formato no es válido.",
    tooLong: "El texto es demasiado largo.",
    tooShort: "El texto es demasiado corto.",
    rangeUnderflow: "El valor es demasiado pequeño.",
    rangeOverflow: "El valor es demasiado grande.",
    patternMismatch: "El formato no es válido.",
    badInput: "El valor no se puede interpretar.",
    stepMismatch: "El valor no es válido."
  };
  var SUMMARY_MSG = "Revisa los campos marcados.";
  var ERROR_CLASS = "form-error";

  function mensajePara(control) {
    var v = control.validity;
    for (var clave in MESSAGES) {
      if (v[clave]) return MESSAGES[clave];
    }
    return "El valor no es válido.";
  }

  // --- pintado de errores por campo ----------------------------------------

  /**
   * Ancla donde colgar el mensaje. Un control puede no estar renderizado: el
   * input de archivo de /archivos/subir/ es <input type="file" hidden> dentro
   * del dropzone y sin envoltorio .form-field (sus attrs viven en
   * files/frontend_forms.py, así que la plantilla no puede marcarlo). En ese
   * caso se sube al primer ancestro que sí se ve -- para el archivo, el propio
   * #hf-dropzone, que ya trae tabindex="0".
   */
  function anclaPara(control) {
    var visible = control;
    while (visible && visible.offsetParent === null && visible !== document.body) {
      visible = visible.parentElement;
    }
    visible = visible || control;
    return (visible.closest && visible.closest(".form-field")) || visible;
  }

  function idDeError(control) {
    return (control.id || control.name || "campo") + "-hf-error";
  }

  function limpiarError(control) {
    control.removeAttribute("aria-invalid");

    var id = idDeError(control);
    var descrito = (control.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter(function (t) { return t && t !== id; });
    if (descrito.length) control.setAttribute("aria-describedby", descrito.join(" "));
    else control.removeAttribute("aria-describedby");

    var previo = document.getElementById(id);
    if (previo && previo.parentNode) previo.parentNode.removeChild(previo);
  }

  function pintarError(control, mensaje) {
    limpiarError(control);

    var id = idDeError(control);
    var p = document.createElement("p");
    p.className = ERROR_CLASS;
    p.id = id;
    p.textContent = mensaje; // texto plano vía textContent -- nunca innerHTML
    anclaPara(control).appendChild(p);

    control.setAttribute("aria-invalid", "true");
    // Se AÑADE al aria-describedby existente, no se sobrescribe: el help_text
    // de un campo puede acabar referenciado ahí.
    var descrito = control.getAttribute("aria-describedby");
    control.setAttribute("aria-describedby", descrito ? descrito + " " + id : id);
  }

  // --- recorrida del formulario --------------------------------------------

  function controlesDe(form) {
    return Array.prototype.filter.call(
      form.querySelectorAll("input, select, textarea"),
      function (c) {
        return !c.disabled && c.type !== "hidden" && c.type !== "submit" && c.type !== "button"
          && c.name && typeof c.checkValidity === "function";
      }
    );
  }

  function invalidosDe(form) {
    return controlesDe(form).filter(function (control) {
      // checkValidity() sigue funcionando con form.noValidate: noValidate solo
      // apaga la validación AL ENVIAR, no la API.
      if (control.checkValidity()) {
        limpiarError(control);
        return false;
      }
      return true;
    });
  }

  function reportar(invalidos) {
    invalidos.forEach(function (control) {
      pintarError(control, mensajePara(control));
    });

    // Un solo aviso de resumen. En un modal también se ve: los toasts están en
    // z-index 1100, por encima de toda la pila de modales.
    //
    // Los errores del SERVIDOR usan el mismo pintado por campo, vía la API que
    // se expone al final de este archivo: un error de "campo obligatorio" se ve
    // igual venga del navegador o de Django. .hf-modal-errors queda entonces
    // para lo que NO se puede colgar de un campo (errores de formulario
    // completo, o una clave que el modal no muestra) -- ver showErrors() en
    // quick_create_modals.js.
    if (window.HFToast) window.HFToast.show(SUMMARY_MSG, "error");

    var primero = invalidos[0];
    var foco = primero;
    if (foco.offsetParent === null) {
      // No se puede enfocar lo que no se ve; se enfoca el ancestro visible si
      // acepta foco, y si no, al menos se trae a la vista.
      var visible = anclaPara(primero);
      foco = (visible.tabIndex >= 0) ? visible : null;
      if (!foco && visible.scrollIntoView) visible.scrollIntoView({ block: "center" });
    }
    if (foco) {
      foco.focus();
      if (foco.scrollIntoView) foco.scrollIntoView({ block: "center" });
    }
  }

  // --- envío ---------------------------------------------------------------

  function onSubmit(e) {
    var form = e.target;
    if (!form || !form.hasAttribute || !form.hasAttribute("data-hf-validate")) return;

    var invalidos = invalidosDe(form);
    if (!invalidos.length) return; // válido: el evento sigue su curso

    e.preventDefault();
    // stopPropagation() es la llamada que carga el peso: en fase de CAPTURA
    // impide que el evento llegue nunca al <form>, así que ni el XHR de
    // upload_page.js ni el fetch de quick_create_modals.js se ejecutan. Y esto
    // vale sin importar el orden de carga de los <script>.
    e.stopPropagation();
    e.stopImmediatePropagation(); // por si algún día hay otro listener de captura
    reportar(invalidos);
  }

  function init() {
    var forms = document.querySelectorAll("form[data-hf-validate]");
    if (!forms.length) return;

    forms.forEach(function (form) {
      // noValidate se pone desde JS y NO en la plantilla a propósito: sin JS,
      // la validación nativa del navegador sigue siendo la red de seguridad de
      // estos formularios (solo #hf-upload-form y los modales llevan novalidate
      // en el HTML). Es también lo que hace seguro recortar el <select> nativo
      // en hf_searchable_select.js.
      form.noValidate = true;

      controlesDe(form).forEach(function (control) {
        // Explícito aunque el atributo required ya esté: ClearableFileInput
        // omite required cuando hay valor inicial, así que un form de edición
        // puede tener un campo obligatorio sin el atributo.
        if (control.required) control.setAttribute("aria-required", "true");

        var limpiar = function () { limpiarError(control); };
        control.addEventListener("input", limpiar);
        control.addEventListener("change", limpiar);
      });
    });

    // Un único listener en document y en fase de captura. Ver onSubmit().
    document.addEventListener("submit", onSubmit, true);
  }

  /* Pintado por campo, para quien tenga errores que no vienen de la Constraint
   * Validation API -- hoy, los que devuelve el servidor en JSON al enviar un
   * modal de alta rápida (quick_create_modals.js::showErrors).
   *
   * Se expone el pintor y NO se reimplementa allí: un mismo error tiene que
   * verse igual venga de donde venga, y los listeners de input/change que
   * registra init() ya limpian estos nodos cuando el usuario corrige el campo,
   * sin importar quién los escribió.
   */
  window.HFFormValidate = {
    paintFieldError: pintarError,
    clearFieldError: limpiarError,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
