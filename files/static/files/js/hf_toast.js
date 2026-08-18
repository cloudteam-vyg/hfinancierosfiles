/* Avisos flotantes (window.HFToast).
 *
 * El canal de CLIENTE para algo que pasó sin navegación: un alta rápida que
 * salió bien, un formulario que no pasó la validación. El canal de SERVIDOR
 * tras una navegación siguen siendo los messages de Django (ver
 * templates/base.html); no se convierten unos en otros, porque hacerlo
 * rompería la ruta sin JS, que es justo lo que los messages sirven bien.
 *
 * Requiere files/static/files/js/hf_utils.js cargado antes.
 * El CSS vive en static/css/main.css (sección "Toasts").
 */
(function () {
  "use strict";

  var utils = window.HFUtils; // ver files/static/files/js/hf_utils.js

  // Los errores NO se auto-cierran: un mensaje de error perdido es un fallo
  // real, y es el único caso en el que el usuario puede necesitar leerlo con
  // calma o copiarlo. Se cierran con el botón o con Escape.
  var DISMISS_MS = { success: 5000, info: 5000, warning: 8000 };
  var MAX_VISIBLE = 4;
  var TYPES = ["success", "error", "warning", "info"];

  var containers = {}; // { polite: Element, assertive: Element }

  // --- contenedores ---------------------------------------------------------

  function buildContainers() {
    // Dos regiones y no una sola con el aria-live conmutado: cambiar aria-live
    // en caliente es poco fiable entre lectores de pantalla. Y se crean en
    // init() (no en una plantilla) porque una live region tiene que existir en
    // el DOM ANTES de insertarle contenido, o el primer anuncio se pierde --
    // así ninguna página puede olvidarse el contenedor.
    containers.polite = makeContainer("hf-toasts-polite", "status", "polite");
    containers.assertive = makeContainer("hf-toasts-assertive", "alert", "assertive");
  }

  function makeContainer(id, role, live) {
    var existing = document.getElementById(id);
    if (existing) return existing;

    var el = document.createElement("div");
    el.className = "hf-toasts";
    el.id = id;
    el.setAttribute("role", role);
    el.setAttribute("aria-live", live);
    el.setAttribute("aria-atomic", "false");
    document.body.appendChild(el);
    return el;
  }

  // --- ciclo de vida de un toast -------------------------------------------

  function removeNow(toast) {
    if (toast && toast.parentNode) toast.parentNode.removeChild(toast);
  }

  function dismiss(toast) {
    if (!toast || !toast.classList.contains("is-open")) return;

    clearTimeout(toast._hfTimer);
    toast.classList.remove("is-open");

    // Mismo patrón que closeModal() en quick_create_modals.js: esperar el
    // transitionend, salvo con movimiento reducido, donde no hay transición y
    // el listener nunca se consumiría.
    if (utils.prefersReducedMotion()) {
      removeNow(toast);
      return;
    }
    var finish = function (e) {
      // La transición de los hijos también burbujea hasta aquí; sin esta
      // guarda, cualquiera de ellas borraría el toast antes de tiempo.
      if (e.target !== toast) return;
      toast.removeEventListener("transitionend", finish);
      removeNow(toast);
    };
    toast.addEventListener("transitionend", finish);
  }

  function armTimer(toast, ms) {
    if (!ms) return; // error: sin auto-cierre
    clearTimeout(toast._hfTimer);
    toast._hfTimer = setTimeout(function () { dismiss(toast); }, ms);
  }

  function trim(container) {
    // Solo se sacrifican los que se iban a cerrar solos: un error nunca se
    // descarta para hacer sitio.
    var toasts = container.querySelectorAll(".hf-toast");
    var sobran = toasts.length - MAX_VISIBLE;
    for (var i = 0; i < toasts.length && sobran > 0; i++) {
      if (toasts[i]._hfDismissMs) {
        removeNow(toasts[i]);
        sobran--;
      }
    }
  }

  function show(message, type) {
    if (!message) return null;
    if (TYPES.indexOf(type) === -1) type = "info";

    var container = type === "error" ? containers.assertive : containers.polite;
    if (!container) return null; // init() no ha corrido todavía

    var toast = document.createElement("div");
    toast.className = "hf-toast hf-toast-" + type;

    var texto = document.createElement("span");
    texto.className = "hf-toast-message";
    texto.textContent = message; // texto plano vía textContent -- nunca innerHTML
    toast.appendChild(texto);

    var cerrar = document.createElement("button");
    cerrar.type = "button";
    cerrar.className = "hf-toast-close";
    cerrar.setAttribute("aria-label", "Cerrar aviso");
    cerrar.textContent = "×";
    cerrar.addEventListener("click", function () { dismiss(toast); });
    toast.appendChild(cerrar);

    var ms = DISMISS_MS[type] || 0;
    toast._hfDismissMs = ms;

    // WCAG 2.2.1: el auto-cierre se pausa mientras el usuario está encima o
    // dentro con el teclado, y se rearma al salir.
    toast.addEventListener("mouseenter", function () { clearTimeout(toast._hfTimer); });
    toast.addEventListener("focusin", function () { clearTimeout(toast._hfTimer); });
    toast.addEventListener("mouseleave", function () { armTimer(toast, ms); });
    toast.addEventListener("focusout", function () { armTimer(toast, ms); });

    container.appendChild(toast);
    trim(container);

    // Mismo reflow forzado que openModal(): sin él, quitar el estado inicial y
    // aplicar el final en el mismo frame colapsa la transición de entrada.
    void toast.offsetWidth;
    toast.classList.add("is-open");

    armTimer(toast, ms);
    return toast;
  }

  // --- init ----------------------------------------------------------------

  function init() {
    buildContainers();

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      // Si hay un modal abierto, Escape es suyo: cerrar un aviso en su lugar
      // dejaría al usuario pulsando Escape sin que el modal se vaya. Se
      // comprueba por el DOM y no importando el estado del motor de modales,
      // para que este archivo no dependa de él.
      if (document.querySelector(".modal-overlay.is-open")) return;

      var abiertos = document.querySelectorAll(".hf-toast.is-open");
      if (abiertos.length) dismiss(abiertos[abiertos.length - 1]);
    });
  }

  window.HFToast = {
    show: show,
    dismiss: dismiss,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
