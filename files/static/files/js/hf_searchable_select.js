/* Select con búsqueda (combobox) para los <select> largos.
 *
 * Se activa en los formularios marcados con data-hf-searchable; un
 * .form-field con data-hf-no-searchable queda fuera. El umbral de opciones
 * viaja en data-hf-searchable-min-options, no en una constante de este
 * archivo (misma regla que los topes de /archivos/: los números del servidor
 * llegan por data-*, nunca duplicados en el JS).
 *
 * El <select> nativo NO se elimina ni se oculta con display:none: se queda
 * recortado con .hf-sr-only y sigue siendo la fuente de verdad del valor, así
 * que los POST no cambian y la página funciona igual sin JS.
 *
 * Requiere hf_utils.js. El CSS vive en static/css/main.css.
 */
(function () {
  "use strict";

  var DEFAULT_MIN_OPTIONS = 8;
  var BLANK_LABEL = "Sin seleccionar";
  var PAGE_STEP = 10;
  var GAP = 4;            // px entre el input y la lista
  var MIN_SPACE_BELOW = 160; // px por debajo para no voltear la lista hacia arriba

  var utils = window.HFUtils; // ver files/static/files/js/hf_utils.js
  var combos = []; // para reposicionar en scroll/resize

  /** Normaliza para comparar sin acentos ni mayúsculas ("Órgano" ~ "organo"). */
  function fold(s) {
    return String(s || "")
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase();
  }

  // --- construcción --------------------------------------------------------

  function enhance(select) {
    if (!select || select.dataset.hfCombo === "1") return; // idempotente
    if (select.multiple || select.disabled) return;

    var field = select.closest(".form-field");
    var label = field ? field.querySelector("label") : null;
    var baseId = select.id || select.name || ("combo-" + combos.length);

    select.dataset.hfCombo = "1";

    var wrapper = document.createElement("div");
    wrapper.className = "hf-combo";
    wrapper.setAttribute("data-hf-combo", "");
    wrapper.setAttribute("data-expanded", "false");
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);

    // Recortado, no display:none: un control required que no se puede enfocar
    // hace que el navegador se niegue a enviar el formulario (ver
    // .hf-sr-only en main.css y hf_form_validate.js).
    select.classList.add("hf-sr-only");
    select.setAttribute("tabindex", "-1");

    var input = document.createElement("input");
    input.type = "text";
    input.className = "hf-combo-input";
    input.id = baseId + "__input";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", baseId + "__list");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.placeholder = "Buscar o seleccionar…";
    if (select.required) input.setAttribute("aria-required", "true");
    wrapper.appendChild(input);

    // El <label for> sigue apuntando al <select> (lo generó Django, y
    // field.errors / id_for_label se mantienen coherentes). El input toma su
    // nombre accesible del propio label vía aria-labelledby. No se pone
    // aria-hidden en el select: es enfocable, y aria-hidden sobre algo
    // enfocable es un error de accesibilidad.
    if (label) {
      if (!label.id) label.id = baseId + "__label";
      input.setAttribute("aria-labelledby", label.id);
    }

    // Hija de <body>: .modal-content la recortaría (overflow-y) y su transform
    // la reposicionaría (un transform distinto de none es containing block de
    // los descendientes position:fixed). Ver el comentario de .hf-combo-list.
    var list = document.createElement("ul");
    list.className = "hf-combo-list";
    list.id = baseId + "__list";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    if (label) list.setAttribute("aria-label", label.textContent.replace("*", "").trim());
    document.body.appendChild(list);

    var combo = {
      select: select, input: input, list: list, wrapper: wrapper,
      baseId: baseId, options: [], activeIndex: -1, open: false
    };
    combos.push(combo);

    wireCombo(combo);
    syncLabel(combo);
    return combo;
  }

  // --- estado / etiqueta ---------------------------------------------------

  function selectedOption(combo) {
    return combo.select.selectedOptions && combo.select.selectedOptions.length
      ? combo.select.selectedOptions[0]
      : null;
  }

  function labelOf(option) {
    if (!option) return "";
    // La opción vacía de Django ("---------") no es una etiqueta útil; en el
    // input se representa con el placeholder.
    if (!option.value) return "";
    return option.textContent.trim();
  }

  function syncLabel(combo) {
    combo.input.value = labelOf(selectedOption(combo));
  }

  /**
   * Reconstruye la lista LEYENDO el <select> en cada apertura.
   *
   * Esto es lo que hace que no haga falta ningún evento ni observer para
   * enterarse de las opciones que añade addAndSelectOption() desde un modal de
   * alta rápida: si la lista se arma al abrirse, una opción agregada mientras
   * estaba cerrada se recoge sola. El problema de sincronización no se
   * resuelve, deja de existir.
   */
  function rebuild(combo, filtro) {
    var seleccionada = selectedOption(combo);
    combo.list.textContent = "";
    combo.options = [];

    var buscado = fold(filtro);
    var nativas = combo.select.options;

    for (var i = 0; i < nativas.length; i++) {
      var nativa = nativas[i];
      var texto = nativa.value ? nativa.textContent.trim() : BLANK_LABEL;
      if (buscado && fold(texto).indexOf(buscado) === -1) continue;

      var li = document.createElement("li");
      li.className = "hf-combo-option";
      li.id = combo.baseId + "__opt-" + i;
      li.setAttribute("role", "option");
      li.setAttribute("data-value", nativa.value);
      li.textContent = texto; // texto plano vía textContent -- nunca innerHTML
      if (nativa === seleccionada) li.setAttribute("aria-selected", "true");
      combo.list.appendChild(li);
      combo.options.push({ li: li, nativa: nativa });
    }

    if (!combo.options.length) {
      var vacio = document.createElement("li");
      vacio.className = "hf-combo-empty";
      vacio.textContent = "Sin resultados";
      combo.list.appendChild(vacio);
    }

    setActive(combo, combo.options.length ? 0 : -1);
  }

  function setActive(combo, index) {
    combo.options.forEach(function (o) { o.li.classList.remove("is-active"); });
    combo.activeIndex = index;

    if (index < 0 || index >= combo.options.length) {
      combo.input.removeAttribute("aria-activedescendant");
      return;
    }
    var activa = combo.options[index].li;
    activa.classList.add("is-active");
    combo.input.setAttribute("aria-activedescendant", activa.id);

    // El foco NUNCA entra en la lista (eso es lo que compra
    // aria-activedescendant), así que hay que traer la opción a la vista a mano.
    var lr = combo.list.getBoundingClientRect();
    var ar = activa.getBoundingClientRect();
    if (ar.bottom > lr.bottom) combo.list.scrollTop += ar.bottom - lr.bottom;
    else if (ar.top < lr.top) combo.list.scrollTop -= lr.top - ar.top;
  }

  // --- posición ------------------------------------------------------------

  function position(combo) {
    if (!combo.open) return;

    var r = combo.input.getBoundingClientRect();
    var alto = window.innerHeight;
    var debajo = alto - r.bottom;
    var encima = r.top;

    combo.list.style.left = r.left + "px";
    combo.list.style.width = r.width + "px";

    if (debajo < MIN_SPACE_BELOW && encima > debajo) {
      combo.list.style.top = "auto";
      combo.list.style.bottom = (alto - r.top + GAP) + "px";
      combo.list.style.maxHeight = (encima - GAP * 2) + "px";
    } else {
      combo.list.style.bottom = "auto";
      combo.list.style.top = (r.bottom + GAP) + "px";
      combo.list.style.maxHeight = (debajo - GAP * 2) + "px";
    }
  }

  function openList(combo, filtro) {
    rebuild(combo, filtro || "");
    combo.open = true;
    combo.list.hidden = false;
    combo.wrapper.setAttribute("data-expanded", "true");
    combo.input.setAttribute("aria-expanded", "true");
    position(combo);
  }

  function closeList(combo, revertir) {
    if (!combo.open) return;
    combo.open = false;
    combo.list.hidden = true;
    combo.wrapper.setAttribute("data-expanded", "false");
    combo.input.setAttribute("aria-expanded", "false");
    combo.input.removeAttribute("aria-activedescendant");
    if (revertir) syncLabel(combo);
  }

  function commit(combo, index) {
    var elegida = combo.options[index];
    if (!elegida) return;
    combo.select.value = elegida.nativa.value;
    closeList(combo, false);
    syncLabel(combo);
    // change real para que lo vean los listeners del <select> (incluido el de
    // limpieza de errores de hf_form_validate.js).
    combo.select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // --- eventos -------------------------------------------------------------

  function wireCombo(combo) {
    var input = combo.input;

    // Clicar el <label> enfoca el <select> recortado, que no se ve: se
    // reenvía al input.
    combo.select.addEventListener("focus", function () { input.focus(); });

    // Solo reetiquetar. Es idempotente y no dispara nada, así que confirmar
    // desde el combobox (que sí emite change) no puede entrar en bucle.
    combo.select.addEventListener("change", function () {
      if (!combo.open) syncLabel(combo);
    });

    // form.reset() se dispara ANTES de restaurar los valores, así que hay que
    // resincronizar en el tick siguiente; sin esto, un modal cancelado vuelve a
    // abrirse mostrando la etiqueta vieja.
    if (combo.select.form) {
      combo.select.form.addEventListener("reset", function () {
        setTimeout(function () { closeList(combo, false); syncLabel(combo); }, 0);
      });
    }

    input.addEventListener("focus", function () { input.select(); });

    input.addEventListener("input", function () {
      openList(combo, input.value);
    });

    input.addEventListener("click", function () {
      if (!combo.open) openList(combo, "");
    });

    input.addEventListener("keydown", function (e) {
      var k = e.key;

      if (k === "ArrowDown" || k === "ArrowUp") {
        e.preventDefault();
        if (!combo.open) {
          openList(combo, "");
          setActive(combo, k === "ArrowDown" ? 0 : combo.options.length - 1);
        } else if (e.altKey) {
          closeList(combo, true);
        } else {
          var paso = k === "ArrowDown" ? 1 : -1;
          // Clamp sin wrap: dar la vuelta desde el final hasta el principio
          // desorienta en una lista que se acaba de filtrar.
          var i = Math.min(Math.max(combo.activeIndex + paso, 0), combo.options.length - 1);
          setActive(combo, i);
        }
        return;
      }

      if (!combo.open) {
        // Enter y Escape con la lista cerrada NO se interceptan: el input se
        // comporta como cualquier campo de texto (Enter envía el formulario).
        return;
      }

      if (k === "Enter") {
        // preventDefault o el Enter envía el formulario; stopPropagation o lo
        // ven además los handlers del modal.
        e.preventDefault();
        e.stopPropagation();
        commit(combo, combo.activeIndex);
      } else if (k === "Escape") {
        // stopPropagation para que el modal NO se cierre: Escape con la lista
        // abierta es de la lista.
        e.stopPropagation();
        closeList(combo, true);
      } else if (k === "Home") {
        e.preventDefault();
        setActive(combo, 0);
      } else if (k === "End") {
        e.preventDefault();
        setActive(combo, combo.options.length - 1);
      } else if (k === "PageDown" || k === "PageUp") {
        e.preventDefault();
        var salto = k === "PageDown" ? PAGE_STEP : -PAGE_STEP;
        setActive(combo, Math.min(Math.max(combo.activeIndex + salto, 0), combo.options.length - 1));
      } else if (k === "Tab") {
        // Se cierra revirtiendo el texto y SIN confirmar la opción meramente
        // resaltada: una selección sorpresa silenciosa es peor que una
        // pulsación perdida. Sin preventDefault, el foco sigue su camino.
        closeList(combo, true);
      }
    });

    // preventDefault en mousedown: sin él el input pierde el foco, la lista se
    // cierra por focusout y el click nunca llega a la opción.
    combo.list.addEventListener("mousedown", function (e) { e.preventDefault(); });

    combo.list.addEventListener("click", function (e) {
      var li = e.target.closest ? e.target.closest("[role=option]") : null;
      if (!li) return;
      for (var i = 0; i < combo.options.length; i++) {
        if (combo.options[i].li === li) { commit(combo, i); return; }
      }
    });

    input.addEventListener("focusout", function (e) {
      var hacia = e.relatedTarget;
      if (hacia && (combo.wrapper.contains(hacia) || combo.list.contains(hacia))) return;
      closeList(combo, true);
    });
  }

  // --- API pública ---------------------------------------------------------

  /**
   * Relee opciones y etiqueta. Hoy no hace falta para el alta rápida (la lista
   * se reconstruye al abrirse, ver rebuild()); queda expuesta como escape
   * hatch para una futura carga de opciones desde el servidor.
   */
  function refresh(selectOrId) {
    var select = typeof selectOrId === "string" ? document.getElementById(selectOrId) : selectOrId;
    for (var i = 0; i < combos.length; i++) {
      if (combos[i].select === select) {
        if (combos[i].open) rebuild(combos[i], combos[i].input.value);
        else syncLabel(combos[i]);
        return;
      }
    }
  }

  // --- init ----------------------------------------------------------------

  function init() {
    var forms = document.querySelectorAll("form[data-hf-searchable]");
    if (!forms.length) return;

    forms.forEach(function (form) {
      // No dejar NUNCA un formulario en el estado "select nativo recortado +
      // validación nativa encendida + sin validación JS": el navegador se
      // negaría a enviarlo sin decir nada. Si no hay novalidate ni validador,
      // se renuncia a mejorar este formulario.
      var tieneRed = form.noValidate || form.hasAttribute("novalidate")
        || form.hasAttribute("data-hf-validate");
      if (!tieneRed) return;

      var minimo = Number(form.dataset.hfSearchableMinOptions) || DEFAULT_MIN_OPTIONS;

      form.querySelectorAll(".form-field select").forEach(function (select) {
        if (select.closest("[data-hf-no-searchable]")) return;
        if (select.options.length < minimo) return;
        enhance(select);
      });
    });

    if (!combos.length) return;

    // capture:true es lo que atrapa el scroll DENTRO de .modal-content: un
    // scroll en un contenedor anidado no burbujea hasta window.
    var pendiente = false;
    var reposicionar = function () {
      if (pendiente) return;
      pendiente = true;
      requestAnimationFrame(function () {
        pendiente = false;
        combos.forEach(position);
      });
    };
    window.addEventListener("resize", reposicionar, { passive: true });
    window.addEventListener("scroll", reposicionar, { capture: true, passive: true });
  }

  window.HFSearchableSelect = {
    enhance: enhance,
    refresh: refresh,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
