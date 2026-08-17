/* Utilidades compartidas por los scripts de la app (window.HFUtils).
 *
 * Existe solo para lo que estaba duplicado LITERALMENTE en más de un
 * archivo. No es un "framework": si algo lo usa un único script, vive en
 * ese script. Debe cargarse ANTES que cualquier otro script propio.
 *
 * Se mantiene el estilo del resto del proyecto: JS plano, sin build step,
 * sin dependencias.
 */
(function () {
  "use strict";

  /** Lee una cookie por nombre (para el header X-CSRFToken en peticiones AJAX). */
  function getCookie(name) {
    var prefix = name + "=";
    var parts = document.cookie ? document.cookie.split("; ") : [];
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].indexOf(prefix) === 0) {
        return decodeURIComponent(parts[i].slice(prefix.length));
      }
    }
    return null;
  }

  /**
   * Aplana el JSON de errores de un form de Django ({campo: [err, ...]}) en
   * una lista plana de mensajes legibles.
   *
   * Django serializa los errores de dos maneras según cómo se generen
   * (strings simples o dicts con .message), así que se contemplan ambas.
   */
  function formErrorMessages(errors) {
    var messages = [];
    if (errors && typeof errors === "object") {
      Object.keys(errors).forEach(function (field) {
        (errors[field] || []).forEach(function (err) {
          messages.push((err && err.message) || String(err));
        });
      });
    }
    return messages;
  }

  /** true si el usuario pidió reducir animaciones en su sistema operativo. */
  function prefersReducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  window.HFUtils = {
    getCookie: getCookie,
    formErrorMessages: formErrorMessages,
    prefersReducedMotion: prefersReducedMotion,
  };
})();
