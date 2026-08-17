(function () {
  "use strict";

  // Los topes REALES los define el servidor y llegan por data-attribute (ver
  // file_archive_list_view): duplicarlos aquí como números fijos ya causó un
  // desfase que dejó sin preview a PDFs válidos. Estos valores solo son un
  // respaldo si el atributo faltara. El chequeo cliente no es la defensa
  // (el servidor revalida), solo evita pedir una preview condenada a 404.
  // MAX_PREVIEW_BYTES aplica a imágenes y Office (viajan completos); los PDF
  // usan su propio tope (el servidor los trunca a PAGE_LIMIT páginas) y los
  // .txt no lo necesitan (el servidor solo lee los primeros KB).
  var DEFAULT_MAX_PREVIEW_BYTES = 20 * 1024 * 1024;
  var DEFAULT_MAX_PDF_BYTES = 300 * 1024 * 1024;
  var DEFAULT_PAGE_LIMIT = 5; // respaldo de PDF_PREVIEW_PAGE_LIMIT del backend
  var MOBILE_QUERY = "(max-width: 768px)";
  var PREVIEW_DEBOUNCE_MS = 150;
  var LOAD_ERROR_MSG = "No se pudo cargar la previsualización de este archivo.";
  // Tope de densidad del canvas: sin esto, una pantalla 3x genera canvases
  // ~9x más grandes en memoria (y iOS los rechaza por área máxima).
  var MAX_CANVAS_DPR = 2;

  var IMAGE_EXTS = ["png", "jpg", "jpeg", "webp", "gif"];

  var utils = window.HFUtils; // ver files/static/files/js/hf_utils.js

  function isMobile() {
    return window.matchMedia(MOBILE_QUERY).matches;
  }

  function buildNextUrl(fileId) {
    var params = new URLSearchParams(window.location.search);
    params.set("preview", fileId);
    return window.location.pathname + "?" + params.toString();
  }

  function init() {
    var list = document.getElementById("hf-file-list");
    var panel = document.getElementById("preview-panel");
    if (!list || !panel) return;

    var canEdit = list.hasAttribute("data-can-edit");
    var maxPreviewBytes = Number(list.dataset.maxPreviewBytes) || DEFAULT_MAX_PREVIEW_BYTES;
    var maxPdfBytes = Number(list.dataset.maxPdfBytes) || DEFAULT_MAX_PDF_BYTES;
    // El límite de páginas lo manda el servidor (PDF_PREVIEW_PAGE_LIMIT): es
    // él quien realmente trunca el PDF, así que el aviso al usuario nunca
    // debe anunciar un número distinto al que se está sirviendo.
    var pageLimit = Number(list.dataset.pdfPageLimit) || DEFAULT_PAGE_LIMIT;
    var PDF_NOTICE_MSG = "Previsualizando páginas 1 a " + pageLimit +
      ". Para consultar la totalidad del documento, descargue el archivo.";
    var GENERIC_NOTICE_MSG = "Previsualización limitada a las primeras " + pageLimit +
      " páginas. Descarga el archivo para ver el contenido completo.";
    var emptyState = document.getElementById("preview-empty");
    var content = document.getElementById("preview-content");
    var nameEl = document.getElementById("preview-name");
    var metaEl = document.getElementById("preview-meta");
    var downloadBtn = document.getElementById("preview-download-btn");
    var editBtn = document.getElementById("preview-edit-btn");
    var closeBtn = content.querySelector(".preview-drawer-close");
    var body = document.getElementById("preview-body");
    var backdrop = document.getElementById("preview-drawer-backdrop");

    var selectedRow = null;
    var previewDebounceTimer = null;
    var renderToken = 0;
    var lastFocusedBeforeDrawer = null;

    // --- PDF.js (carga diferida) ------------------------------------------
    // Se importa dinámicamente desde este script clásico en vez de un
    // <script type="module">: las URLs llegan ya hasheadas por {% static %}
    // (whitenoise ManifestStaticFilesStorage) y el ~1.8 MB de la librería
    // solo se descarga cuando de verdad se previsualiza un PDF.
    var pdfjsPromise = null;

    function loadPdfjs() {
      if (!pdfjsPromise) {
        var libUrl = list.dataset.pdfjsLib;
        var workerUrl = list.dataset.pdfjsWorker;
        if (!libUrl || !workerUrl) {
          pdfjsPromise = Promise.reject(new Error("pdfjs URLs ausentes"));
        } else {
          pdfjsPromise = import(libUrl).then(function (lib) {
            // El worker DEBE ser same-origin (requisito de new Worker()).
            // Si algún día los estáticos se sirven desde un CDN, hay que
            // cargarlo vía blob-URL en lugar de la URL directa.
            lib.GlobalWorkerOptions.workerSrc = workerUrl;
            return lib;
          });
        }
      }
      return pdfjsPromise;
    }

    function rows() {
      return list.querySelectorAll("[data-file-id]");
    }

    function rowById(id) {
      return list.querySelector('[data-file-id="' + CSS.escape(id) + '"]');
    }

    // --- drawer móvil ---------------------------------------------------

    function openDrawer() {
      lastFocusedBeforeDrawer = document.activeElement;
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
      document.body.classList.add("scroll-locked");

      backdrop.classList.remove("hidden");
      void backdrop.offsetWidth; // fuerza reflow para que la transición dispare
      backdrop.classList.add("is-open");
      panel.classList.add("is-open");

      if (closeBtn) closeBtn.focus();
    }

    function closeDrawer() {
      if (!panel.classList.contains("is-open")) return;
      backdrop.classList.remove("is-open");
      panel.classList.remove("is-open");
      document.body.classList.remove("scroll-locked");
      panel.removeAttribute("role");
      panel.removeAttribute("aria-modal");

      var finish = function () {
        backdrop.classList.add("hidden");
        backdrop.removeEventListener("transitionend", finish);
      };
      if (utils.prefersReducedMotion()) finish();
      else backdrop.addEventListener("transitionend", finish);

      if (lastFocusedBeforeDrawer && typeof lastFocusedBeforeDrawer.focus === "function") {
        lastFocusedBeforeDrawer.focus();
      }
    }

    backdrop.addEventListener("click", closeDrawer);
    if (closeBtn) closeBtn.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && panel.classList.contains("is-open")) closeDrawer();
    });

    // --- selección + panel -----------------------------------------------

    function clearSelection() {
      rows().forEach(function (r) { r.removeAttribute("aria-current"); });
    }

    function populateHeader(row) {
      nameEl.textContent = row.dataset.name || "";
      metaEl.textContent = [
        row.dataset.customer, row.dataset.archiveClass, row.dataset.sizeDisplay,
      ].filter(Boolean).join(" · ");

      downloadBtn.href = row.dataset.downloadUrl;
      if (canEdit) {
        editBtn.href = editUrlFor(row);
        editBtn.hidden = false;
      } else {
        editBtn.hidden = true;
      }
    }

    function selectRow(row, options) {
      options = options || {};
      clearSelection();
      row.setAttribute("aria-current", "true");
      selectedRow = row;

      emptyState.hidden = true;
      content.hidden = false;
      populateHeader(row);

      if (!options.skipHistory) {
        window.history.replaceState(null, "", buildNextUrl(row.dataset.fileId));
      }

      clearTimeout(previewDebounceTimer);
      previewDebounceTimer = setTimeout(function () { renderBodySafe(row); }, PREVIEW_DEBOUNCE_MS);

      if (isMobile()) {
        // openDrawer() enfoca su botón de cerrar: no se le roba el foco.
        openDrawer();
      } else {
        row.focus();
      }
    }

    function editUrlFor(row) {
      return row.dataset.editUrl + "?next=" + encodeURIComponent(buildNextUrl(row.dataset.fileId));
    }

    // --- render del cuerpo del preview según extensión --------------------

    var currentObjectUrl = null;
    var currentPdfTask = null;    // PDFDocumentLoadingTask en curso
    var currentRenderTask = null; // RenderTask de la página que se está pintando

    function releasePdf() {
      if (currentRenderTask) {
        // cancel() rechaza su promesa con RenderingCancelledException, que
        // el .catch() de renderPdf ignora explícitamente.
        try { currentRenderTask.cancel(); } catch (e) { /* ya terminada */ }
        currentRenderTask = null;
      }
      if (currentPdfTask) {
        // destroy() cancela la carga en curso, libera el documento y mata el
        // worker dedicado que creó este getDocument (sin esto, cada preview
        // dejaría un worker vivo).
        try { currentPdfTask.destroy(); } catch (e) { /* ya destruida */ }
        currentPdfTask = null;
      }
    }

    function clearBody() {
      releasePdf();
      if (currentObjectUrl) {
        // Libera el blob del preview anterior; sin esto, cada preview deja el
        // archivo completo retenido en memoria hasta cerrar la pestaña.
        URL.revokeObjectURL(currentObjectUrl);
        currentObjectUrl = null;
      }
      while (body.firstChild) body.removeChild(body.firstChild);
    }

    /** Indicador de carga. Devuelve el elemento para poder quitarlo luego
     *  sin vaciar todo el panel (los visores Office pintan dentro de un
     *  iframe que ya está montado y no debe destruirse). */
    function showLoading() {
      var el = document.createElement("div");
      el.className = "preview-loading";
      el.textContent = "Cargando previsualización…";
      body.appendChild(el);
      return el;
    }

    function buildTruncatedNotice(text) {
      var notice = document.createElement("div");
      notice.className = "preview-truncated-notice";
      notice.textContent = text || GENERIC_NOTICE_MSG;
      return notice;
    }

    function appendOpenNewLink(row) {
      var link = document.createElement("a");
      link.href = row.dataset.previewUrl;
      link.target = "_blank";
      link.rel = "noopener";
      link.className = "preview-open-new";
      link.textContent = "Abrir en pestaña nueva";
      body.appendChild(link);
    }

    function renderFallbackCard(row, note) {
      clearBody();
      var card = document.createElement("div");
      card.className = "preview-fallback-card";

      var ext = document.createElement("div");
      ext.className = "file-ext";
      ext.textContent = (row.dataset.ext || "—").toUpperCase();
      card.appendChild(ext);

      var name = document.createElement("div");
      name.className = "file-name";
      name.textContent = row.dataset.name || "";
      card.appendChild(name);

      // Metadatos del archivo: cuando no hay preview posible, esto es lo
      // único que el usuario puede ver sin descargar.
      var metaLine = document.createElement("p");
      metaLine.className = "preview-fallback-meta";
      metaLine.textContent = [
        row.dataset.customer, row.dataset.sizeDisplay, row.dataset.updated,
      ].filter(Boolean).join(" · ");
      if (metaLine.textContent) card.appendChild(metaLine);

      var meta = document.createElement("p");
      meta.textContent = note || "La previsualización no está disponible para este archivo.";
      card.appendChild(meta);

      var dl = document.createElement("a");
      dl.href = row.dataset.downloadUrl;
      dl.className = "btn btn-primary preview-fallback-download";
      dl.textContent = "Descargar para ver localmente";
      card.appendChild(dl);

      body.appendChild(card);
    }

    function fetchPreview(row, as) {
      // fetch en vez de asignar src directo: un <iframe>/<img> no puede
      // interceptar errores HTTP, así que un 404/500 del servidor pintaba la
      // página de error de Django DENTRO del panel. Con fetch, cualquier
      // fallo cae a la tarjeta elegante y nunca ensucia la consola/panel.
      return fetch(row.dataset.previewUrl, { credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) throw new Error("preview HTTP " + r.status);
          var truncated = r.headers.get("X-Preview-Truncated") === "1";
          return r[as]().then(function (payload) {
            return { payload: payload, truncated: truncated };
          });
        });
    }

    // Camino de respaldo: visor nativo del navegador vía blob. Se usa solo si
    // PDF.js no se pudo cargar (navegador sin import() dinámico, archivo
    // vendorizado corrupto). El servidor ya entregó como mucho PAGE_LIMIT
    // páginas, así que el límite estricto se respeta igual.
    function renderPdfWithIframe(row, res) {
      clearBody();
      if (res.truncated) body.appendChild(buildTruncatedNotice(PDF_NOTICE_MSG));
      currentObjectUrl = URL.createObjectURL(res.payload);
      var iframe = document.createElement("iframe");
      iframe.src = currentObjectUrl;
      iframe.title = row.dataset.name || "Previsualización";
      body.appendChild(iframe);
      appendOpenNewLink(row);
    }

    function renderPdfPages(row, token, lib, res) {
      // El buffer se TRANSFIERE al worker: no volver a usar res.payload.
      // isEvalSupported:false es defensa en profundidad aun en 4.x (donde
      // CVE-2024-4367 ya está corregido) y deja la puerta abierta a una CSP
      // sin unsafe-eval.
      var task = lib.getDocument({
        data: new Uint8Array(res.payload),
        isEvalSupported: false,
        verbosity: 0,
      });
      currentPdfTask = task;

      return task.promise.then(function (pdfDoc) {
        if (token !== renderToken) return null;

        // clearBody() destruiría la task recién creada, así que se
        // desregistra antes de limpiar y se vuelve a registrar después.
        currentPdfTask = null;
        clearBody();
        currentPdfTask = task;

        if (res.truncated || pdfDoc.numPages > pageLimit) {
          body.appendChild(buildTruncatedNotice(PDF_NOTICE_MSG));
        }

        var pageCount = Math.min(pdfDoc.numPages, pageLimit);
        var cssWidth = Math.max(body.clientWidth, 280);
        var dpr = Math.min(window.devicePixelRatio || 1, MAX_CANVAS_DPR);
        var chain = Promise.resolve();

        for (var n = 1; n <= pageCount; n++) {
          (function (pageNum) {
            // Secuencial (no Promise.all): acota la memoria pico a una
            // página renderizándose a la vez.
            chain = chain.then(function () {
              if (token !== renderToken) return null;
              return pdfDoc.getPage(pageNum).then(function (page) {
                if (token !== renderToken) return null;
                var base = page.getViewport({ scale: 1 });
                var viewport = page.getViewport({ scale: cssWidth / base.width });
                var canvas = document.createElement("canvas");
                canvas.className = "preview-pdf-page";
                canvas.width = Math.floor(viewport.width * dpr);
                canvas.height = Math.floor(viewport.height * dpr);
                canvas.setAttribute(
                  "aria-label", "Página " + pageNum + " de " + row.dataset.name
                );
                body.appendChild(canvas);

                var rt = page.render({
                  canvasContext: canvas.getContext("2d", { alpha: false }),
                  viewport: viewport,
                  transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
                });
                currentRenderTask = rt;
                return rt.promise.then(function () {
                  currentRenderTask = null;
                  page.cleanup(); // solo después de que el render terminó
                });
              });
            });
          })(n);
        }

        return chain.then(function () {
          if (token !== renderToken) return null;
          pdfDoc.cleanup(); // libera cachés de fuentes/imágenes del documento
          appendOpenNewLink(row);
          return null;
        });
      });
    }

    function renderPdf(row, token) {
      showLoading();
      fetchPreview(row, "arrayBuffer")
        .then(function (res) {
          return loadPdfjs().then(
            function (lib) { return renderPdfPages(row, token, lib, res); },
            function () {
              // PDF.js no disponible: se degrada al visor nativo. Hay que
              // re-pedir el archivo porque el arrayBuffer ya se consumió.
              if (token !== renderToken) return null;
              return fetchPreview(row, "blob").then(function (blobRes) {
                if (token !== renderToken) return null;
                renderPdfWithIframe(row, blobRes);
                return null;
              });
            }
          );
        })
        .catch(function (err) {
          if (token !== renderToken) return;
          // El usuario cambió de archivo a mitad del render: no es un error.
          if (err && err.name === "RenderingCancelledException") return;
          renderFallbackCard(row, LOAD_ERROR_MSG);
        });
    }

    function renderText(row, token) {
      showLoading();
      fetchPreview(row, "text")
        .then(function (res) {
          if (token !== renderToken) return;
          clearBody();
          var pre = document.createElement("pre");
          pre.className = "preview-text";
          pre.textContent = res.payload; // texto plano, nunca innerHTML
          body.appendChild(pre);
          if (res.truncated) body.appendChild(buildTruncatedNotice());
          appendOpenNewLink(row);
        })
        .catch(function () {
          if (token === renderToken) renderFallbackCard(row, LOAD_ERROR_MSG);
        });
    }

    function renderImage(row, token) {
      var img = document.createElement("img");
      img.alt = row.dataset.name || "";
      img.addEventListener("error", function () {
        if (token === renderToken) renderFallbackCard(row, LOAD_ERROR_MSG);
      });
      img.src = row.dataset.previewUrl;
      body.appendChild(img);
      appendOpenNewLink(row);
    }

    function createSandboxedFrame() {
      var iframe = document.createElement("iframe");
      // allow-same-origin SIN allow-scripts: si docx-preview/SheetJS
      // generaran HTML con un <script> embebido (bug de la librería o
      // documento adversarial), no se ejecutaría -- defensa en profundidad,
      // ver decisión de arquitectura en el plan de esta feature.
      iframe.sandbox = "allow-same-origin";
      body.appendChild(iframe);
      return iframe;
    }

    /**
     * Ciclo de vida común de los visores Office: iframe sandboxed, descarga
     * del archivo, guardas de cancelación y contrato de error. Lo único
     * propio de cada formato es `paint(iframe, arrayBuffer)`.
     */
    function renderInSandboxedFrame(row, token, errorMsg, paint) {
      var loading = showLoading();
      var iframe = createSandboxedFrame();
      iframe.addEventListener("load", function onLoad() {
        iframe.removeEventListener("load", onLoad);
        fetchPreview(row, "arrayBuffer")
          .then(function (res) {
            if (token !== renderToken) return null;
            return paint(iframe, res.payload);
          })
          .then(function () {
            if (token !== renderToken) return;
            loading.remove(); // el iframe ya tiene contenido: sobra el aviso
            appendOpenNewLink(row);
          })
          .catch(function () {
            if (token === renderToken) renderFallbackCard(row, errorMsg);
          });
      });
    }

    function renderDocx(row, token) {
      renderInSandboxedFrame(
        row, token, "No se pudo generar la vista previa del documento.",
        function (iframe, buf) {
          var doc = iframe.contentDocument;
          return window.docx
            .renderAsync(buf, doc.body, doc.head, { className: "docx", inWrapper: true })
            .then(function () {
              // Mismo límite estricto que los PDF: docx-preview genera una
              // <section class="docx"> por página; se podan las que excedan.
              var pages = doc.body.querySelectorAll("section.docx");
              if (pages.length > pageLimit) {
                for (var i = pageLimit; i < pages.length; i++) pages[i].remove();
                body.insertBefore(buildTruncatedNotice(), iframe);
              }
            });
        }
      );
    }

    function renderXlsx(row, token) {
      renderInSandboxedFrame(
        row, token, "No se pudo generar la vista previa de la hoja de cálculo.",
        function (iframe, buf) {
          var wb = window.XLSX.read(buf, { type: "array" });
          var html = window.XLSX.utils.sheet_to_html(wb.Sheets[wb.SheetNames[0]]);
          var doc = iframe.contentDocument;
          doc.open();
          doc.write(html);
          doc.close();
        }
      );
    }

    function renderBody(row) {
      var token = ++renderToken;
      // Punto ÚNICO de limpieza: libera blob/worker de PDF.js del preview
      // anterior y vacía el panel. Ningún visor debe volver a hacerlo (salvo
      // renderText/renderFallbackCard, que limpian su propio indicador de
      // carga antes de pintar).
      clearBody();
      var ext = (row.dataset.ext || "").toLowerCase();
      var sizeBytes = Number(row.dataset.sizeBytes || 0);

      // PDF: el servidor SIEMPRE responde con las primeras PAGE_LIMIT
      // páginas (cacheadas en disco), así que el tamaño del original solo
      // importa contra el techo de parseo. TXT: el servidor solo manda los
      // primeros KB. El resto viaja completo y sí respeta MAX_PREVIEW_BYTES.
      if (ext === "pdf") {
        if (sizeBytes > maxPdfBytes) {
          renderFallbackCard(row, "El archivo es demasiado grande para previsualizar en el navegador.");
          return;
        }
        renderPdf(row, token);
        return;
      }

      if (ext === "txt") {
        renderText(row, token);
        return;
      }

      if (sizeBytes > maxPreviewBytes) {
        renderFallbackCard(row, "El archivo es demasiado grande para previsualizar en el navegador.");
        return;
      }

      if (IMAGE_EXTS.indexOf(ext) !== -1) {
        renderImage(row, token);
      } else if (ext === "docx" && window.docx) {
        renderDocx(row, token);
      } else if (ext === "xlsx" && window.XLSX) {
        renderXlsx(row, token);
      } else if (ext === "doc") {
        // .doc binario antiguo: no hay renderizador en el navegador (el
        // servidor tampoco lo sirve inline, ver PREVIEWABLE_EXTENSIONS).
        renderFallbackCard(row, "Los documentos .doc no se pueden previsualizar en el navegador.");
      } else {
        renderFallbackCard(row);
      }
    }

    function renderBodySafe(row) {
      // Cinturón: ninguna excepción síncrona inesperada debe romper el
      // script ni dejar el panel en blanco.
      try {
        renderBody(row);
      } catch (e) {
        renderFallbackCard(row, LOAD_ERROR_MSG);
      }
    }

    // --- eventos de fila ---------------------------------------------------

    function isInteractiveTarget(target) {
      return !!target.closest(".row-menu, a, button, summary");
    }

    list.addEventListener("click", function (e) {
      var row = e.target.closest("[data-file-id]");
      if (!row || isInteractiveTarget(e.target)) return;
      selectRow(row);
    });

    list.addEventListener("dblclick", function (e) {
      var row = e.target.closest("[data-file-id]");
      if (!row || isInteractiveTarget(e.target)) return;
      if (!canEdit) return; // no-op: sin permiso, el doble clic no hace nada
      window.location.href = editUrlFor(row);
    });

    list.addEventListener("keydown", function (e) {
      var row = e.target.closest("[data-file-id]");
      if (!row) return;

      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectRow(row);
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        var all = Array.prototype.slice.call(rows());
        var idx = all.indexOf(row);
        var next = e.key === "ArrowDown" ? all[idx + 1] : all[idx - 1];
        if (next) selectRow(next);
      }
    });

    // --- deep link ?preview=<uuid> -----------------------------------------

    var initialId = new URLSearchParams(window.location.search).get("preview");
    if (initialId) {
      var initialRow = rowById(initialId);
      if (initialRow) selectRow(initialRow, { skipHistory: true });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
