/* Motor reutilizable de subida directa navegador -> Azure Blob Storage vía
 * SAS de escritura. Implementa Put Block + Put Block List a mano (sin
 * dependencias externas).
 *
 * ADVERTENCIA DE MANTENIMIENTO: este archivo es una duplicación deliberada
 * del motor que también vive embebido en
 * files/static/files/js/azure_direct_upload.js (usado por el Admin de
 * Django, que no se modifica). Ambas copias DEBEN mantenerse
 * funcionalmente idénticas en todo lo relativo a reintentos, renovación de
 * SAS y Put Block/Put Block List. Ver
 * files/tests.py::SharedUploadEngineSyncTest, que falla si divergen.
 * Si corriges un bug aquí, cópialo también al otro archivo (y viceversa).
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

  window.HFAzureUploader = { uploadFile: uploadFile, humanBytes: humanBytes, AzureError: AzureError };
})();
