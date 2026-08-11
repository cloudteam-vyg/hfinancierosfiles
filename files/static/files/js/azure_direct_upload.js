/* Subida directa navegador -> Azure Blob Storage vía SAS de escritura.
 * Implementa Put Block + Put Block List a mano (sin dependencias).
 * El archivo NO pasa por Django/Gunicorn en ningún momento.
 */
(function () {
  "use strict";

  var DEFAULT_BLOCK_SIZE = 8 * 1024 * 1024;   // 8 MiB
  var DEFAULT_CONCURRENCY = 4;
  var MAX_ATTEMPTS = 5;
  var BASE_BACKOFF_MS = 1000;
  var MAX_BACKOFF_MS = 30000;
  var BLOCK_TIMEOUT_MS = 180000;              // 3 min por bloque
  var AZURE_MAX_BLOCKS = 50000;

  // ---------- utilidades ----------

  function sleep(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }

  function csrfToken() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  function humanBytes(n) {
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(i === 0 ? 0 : 1) + " " + u[i];
  }

  /* Requisitos de blockid (doc de Put Block):
   *  - Base64 de una cadena de <= 64 bytes.
   *  - TODOS los bloques del mismo blob deben tener block IDs de la MISMA
   *    longitud -> por eso padStart a ancho fijo.
   */
  function makeBlockId(index) {
    return btoa("blk-" + String(index).padStart(8, "0"));
  }

  function AzureError(status, body) {
    this.name = "AzureError";
    this.status = status;
    this.body = body || "";
    this.message = "HTTP " + status + " " + this.body.slice(0, 400);
  }
  AzureError.prototype = Object.create(Error.prototype);

  function isRetriable(err) {
    if (!(err instanceof AzureError)) return false;
    // 0 = fallo de red / conexión cortada / timeout.
    return err.status === 0 || err.status === 408 ||
           err.status === 429 || err.status >= 500;
  }

  function isSasExpired(err) {
    if (!(err instanceof AzureError)) return false;
    if (err.status !== 403) return false;
    return /AuthenticationFailed|AuthorizationFailure|Signature not valid|expired/i.test(err.body);
  }

  // ---------- llamadas REST a Azure ----------

  function putBlock(uploadUrl, rawBlockId, chunk, onProgress, signal) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("PUT", uploadUrl + "&comp=block&blockid=" + encodeURIComponent(rawBlockId), true);
      xhr.timeout = BLOCK_TIMEOUT_MS;
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable && onProgress) onProgress(e.loaded);
      };
      xhr.onload = function () {
        if (xhr.status === 201) resolve();
        else reject(new AzureError(xhr.status, xhr.responseText));
      };
      xhr.onerror = function () { reject(new AzureError(0, "Error de red")); };
      xhr.ontimeout = function () { reject(new AzureError(0, "Timeout")); };
      xhr.onabort = function () { reject(new AzureError(-1, "Cancelado")); };
      if (signal) {
        signal.addEventListener("abort", function () { xhr.abort(); }, { once: true });
      }
      xhr.send(chunk);
    });
  }

  function commitBlockList(uploadUrl, rawBlockIds, contentType) {
    var xml =
      '<?xml version="1.0" encoding="utf-8"?><BlockList>' +
      rawBlockIds.map(function (id) { return "<Latest>" + id + "</Latest>"; }).join("") +
      "</BlockList>";

    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("PUT", uploadUrl + "&comp=blocklist", true);
      xhr.timeout = BLOCK_TIMEOUT_MS;
      xhr.setRequestHeader("Content-Type", "application/xml; charset=utf-8");
      if (contentType) {
        xhr.setRequestHeader("x-ms-blob-content-type", contentType);
      }
      xhr.onload = function () {
        if (xhr.status === 201) resolve();
        else reject(new AzureError(xhr.status, xhr.responseText));
      };
      xhr.onerror = function () { reject(new AzureError(0, "Error de red")); };
      xhr.ontimeout = function () { reject(new AzureError(0, "Timeout")); };
      xhr.send(xml);
    });
  }

  // ---------- endpoint Django de SAS ----------

  function requestSas(endpoint, payload) {
    return fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "XMLHttpRequest"
      },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.error || "Error " + res.status);
        return data;
      });
    });
  }

  // ---------- reintentos ----------

  /* Estrategia por bloque:
   *  - Hasta MAX_ATTEMPTS intentos, backoff exponencial con jitter.
   *  - Reintentables: fallo de red (status 0), 408, 429, 5xx.
   *  - 403 por SAS expirada -> renueva la SAS (single-flight) y reintenta
   *    SIN consumir intento.
   *  - Otros 4xx -> fallo definitivo, reintentar no arregla nada.
   * Un bloque que falla NO invalida los ya subidos: Azure los retiene 7
   * días como bloques sin commitear.
   */
  function withRetry(fn, hooks) {
    var attempt = 0;
    function run() {
      return fn().catch(function (err) {
        if (hooks && hooks.onAttemptFail) hooks.onAttemptFail();

        if (isSasExpired(err) && hooks && hooks.renewSas) {
          return hooks.renewSas().then(run);
        }
        attempt++;
        if (attempt >= MAX_ATTEMPTS || !isRetriable(err)) throw err;

        var delay = Math.min(BASE_BACKOFF_MS * Math.pow(2, attempt - 1), MAX_BACKOFF_MS);
        delay = delay * (0.5 + Math.random() * 0.5);
        if (hooks && hooks.onRetry) hooks.onRetry(attempt, delay, err);
        return sleep(delay).then(run);
      });
    }
    return run();
  }

  // ---------- orquestador ----------

  function uploadFile(file, cfg, ui) {
    var session = null;
    var renewInFlight = null;

    function renewSas() {
      if (renewInFlight) return renewInFlight;
      renewInFlight = requestSas(cfg.endpoint, {
        filename: file.name,
        content_type: file.type,
        size: file.size,
        renew_blob_path: session.blob_path,
        renew_file_archive_id: session.file_archive_id
      }).then(function (fresh) {
        session = fresh;
        renewInFlight = null;
        return fresh;
      }).catch(function (e) {
        renewInFlight = null;
        throw e;
      });
      return renewInFlight;
    }

    return requestSas(cfg.endpoint, {
      filename: file.name,
      content_type: file.type,
      size: file.size
    }).then(function (first) {
      session = first;

      var blockSize = session.block_size || DEFAULT_BLOCK_SIZE;
      var concurrency = session.max_concurrency || DEFAULT_CONCURRENCY;
      var total = Math.max(1, Math.ceil(file.size / blockSize));

      if (total > AZURE_MAX_BLOCKS) {
        throw new Error("Archivo demasiado fragmentado; aumenta el tamaño de bloque.");
      }

      var ids = [];
      for (var i = 0; i < total; i++) ids.push(makeBlockId(i));

      var doneBytes = new Array(total).fill(0);
      function report() {
        var sum = 0;
        for (var k = 0; k < total; k++) sum += doneBytes[k];
        ui.progress(sum, file.size);
      }

      var nextIndex = 0;
      var abort = new AbortController();

      function worker() {
        var i = nextIndex++;
        if (i >= total) return Promise.resolve();
        var start = i * blockSize;
        var chunk = file.slice(start, Math.min(start + blockSize, file.size));

        return withRetry(
          function () {
            return putBlock(session.upload_url, ids[i], chunk, function (loaded) {
              doneBytes[i] = loaded;
              report();
            }, abort.signal);
          },
          {
            renewSas: renewSas,
            onAttemptFail: function () { doneBytes[i] = 0; report(); },
            onRetry: function (n, ms) {
              ui.status("Reintentando bloque " + (i + 1) + "/" + total +
                        " (intento " + (n + 1) + ") en " + Math.round(ms / 1000) + "s...");
            }
          }
        ).then(function () {
          doneBytes[i] = chunk.size;
          report();
          return worker();
        });
      }

      var workers = [];
      for (var w = 0; w < Math.min(concurrency, total); w++) workers.push(worker());

      return Promise.all(workers).then(function () {
        ui.status("Confirmando archivo en Azure...");
        return withRetry(
          function () {
            return commitBlockList(session.upload_url, ids, file.type || "application/octet-stream");
          },
          { renewSas: renewSas }
        );
      }).then(function () { return session; });
    });
  }

  // ---------- integración con el formulario del Admin ----------

  function setHidden(name, value) {
    var el = document.getElementById("id_" + name);
    if (el) el.value = value;
  }

  function submitButtons() {
    return Array.prototype.slice.call(
      document.querySelectorAll(".submit-row input[type=submit], .submit-row button[type=submit]")
    );
  }

  function init() {
    var input = document.querySelector("input.hf-direct-upload");
    if (!input) return;

    var cfg = {
      endpoint: input.dataset.sasEndpoint,
      maxMb: parseInt(input.dataset.maxUploadSizeMb || "1024", 10)
    };

    var box = document.createElement("div");
    box.className = "hf-upload-box";
    box.innerHTML =
      '<div class="hf-upload-bar"><div class="hf-upload-fill"></div></div>' +
      '<div class="hf-upload-status"></div>';
    input.parentNode.insertBefore(box, input.nextSibling);

    var fill = box.querySelector(".hf-upload-fill");
    var statusEl = box.querySelector(".hf-upload-status");

    var ui = {
      progress: function (done, total) {
        var pct = total ? Math.floor((done / total) * 100) : 0;
        fill.style.width = pct + "%";
        statusEl.textContent = "Subiendo... " + pct + "% (" +
          humanBytes(done) + " / " + humanBytes(total) + ")";
      },
      status: function (msg) { statusEl.textContent = msg; },
      error: function (msg) {
        statusEl.textContent = "Error: " + msg;
        box.classList.add("hf-upload-error");
      },
      done: function (msg) {
        fill.style.width = "100%";
        statusEl.textContent = msg;
        box.classList.add("hf-upload-ok");
      }
    };

    var uploading = false;

    window.addEventListener("beforeunload", function (e) {
      if (!uploading) return;
      e.preventDefault();
      e.returnValue = "";
      return "";
    });

    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) return;

      box.classList.remove("hf-upload-error", "hf-upload-ok");

      if (file.size <= 0) { ui.error("El archivo está vacío."); return; }
      if (file.size > cfg.maxMb * 1024 * 1024) {
        ui.error("El archivo pesa " + humanBytes(file.size) + " y el límite es " + cfg.maxMb + " MB.");
        return;
      }

      uploading = true;
      submitButtons().forEach(function (b) { b.disabled = true; });
      ["file_archive_id", "blob_path", "original_filename", "file_size", "content_type"]
        .forEach(function (n) { setHidden(n, ""); });
      ui.status("Solicitando autorización...");

      uploadFile(file, cfg, ui).then(function (session) {
        setHidden("file_archive_id", session.file_archive_id);
        setHidden("blob_path", session.blob_path);
        setHidden("original_filename", file.name);
        setHidden("file_size", String(file.size));
        setHidden("content_type", file.type || "application/octet-stream");

        var nameEl = document.getElementById("id_name");
        if (nameEl && !nameEl.value) nameEl.value = file.name.slice(0, 150);

        ui.done("Archivo subido correctamente. Ya puedes guardar.");
      }).catch(function (err) {
        ui.error(err.message || String(err));
      }).then(function () {
        uploading = false;
        submitButtons().forEach(function (b) { b.disabled = false; });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
