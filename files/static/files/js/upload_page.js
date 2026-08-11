(function () {
  "use strict";

  function setHidden(name, value) {
    var el = document.getElementById("id_" + name);
    if (el) el.value = value;
  }

  function init() {
    var dropzone = document.getElementById("hf-dropzone");
    if (!dropzone) return;

    var fileInput = document.getElementById("id_upload_widget");
    var submitBtn = document.getElementById("hf-submit-btn");
    var box = dropzone.querySelector(".hf-upload-box");
    var fill = box.querySelector(".hf-upload-fill");
    var statusEl = box.querySelector(".hf-upload-status");

    var cfg = {
      endpoint: dropzone.dataset.sasEndpoint,
      maxMb: parseInt(dropzone.dataset.maxUploadSizeMb || "1024", 10)
    };

    var ui = {
      progress: function (done, total) {
        var pct = total ? Math.floor((done / total) * 100) : 0;
        fill.style.width = pct + "%";
        statusEl.textContent = "Subiendo... " + pct + "% (" +
          window.HFAzureUploader.humanBytes(done) + " / " +
          window.HFAzureUploader.humanBytes(total) + ")";
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
    var controller = new AbortController();

    window.addEventListener("beforeunload", function (e) {
      if (!uploading) return;
      e.preventDefault();
      e.returnValue = "";
      return "";
    }, { signal: controller.signal });

    function startUpload(file) {
      if (!file) return;
      box.classList.remove("hf-upload-error", "hf-upload-ok");

      if (file.size <= 0) { ui.error("El archivo está vacío."); return; }
      if (file.size > cfg.maxMb * 1024 * 1024) {
        ui.error("El archivo pesa " + window.HFAzureUploader.humanBytes(file.size) +
          " y el límite es " + cfg.maxMb + " MB.");
        return;
      }

      uploading = true;
      submitBtn.disabled = true;
      ["file_archive_id", "blob_path", "original_filename", "file_size", "content_type"]
        .forEach(function (n) { setHidden(n, ""); });
      ui.status("Solicitando autorización...");

      window.HFAzureUploader.uploadFile(file, cfg, ui).then(function (session) {
        setHidden("file_archive_id", session.file_archive_id);
        setHidden("blob_path", session.blob_path);
        setHidden("original_filename", file.name);
        setHidden("file_size", String(file.size));
        setHidden("content_type", file.type || "application/octet-stream");

        var nameEl = document.getElementById("id_name");
        if (nameEl && !nameEl.value) nameEl.value = file.name.slice(0, 150);

        ui.done("Archivo subido correctamente. Ya puedes guardar.");
        submitBtn.disabled = false;
      }).catch(function (err) {
        ui.error(err.message || String(err));
        submitBtn.disabled = false;
      }).then(function () { uploading = false; });
    }

    // --- selección clásica ---
    dropzone.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", function () {
      startUpload(fileInput.files && fileInput.files[0]);
    });

    // --- drag & drop ---
    ["dragover", "dragenter"].forEach(function (evt) {
      dropzone.addEventListener(evt, function (e) {
        e.preventDefault();
        dropzone.classList.add("hf-dropzone-dragover");
      });
    });
    ["dragleave", "dragend"].forEach(function (evt) {
      dropzone.addEventListener(evt, function () {
        dropzone.classList.remove("hf-dropzone-dragover");
      });
    });
    dropzone.addEventListener("drop", function (e) {
      e.preventDefault();
      dropzone.classList.remove("hf-dropzone-dragover");
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || files.length === 0) {
        ui.error("El elemento soltado no es un archivo. Arrastra un archivo desde tu equipo.");
        return;
      }
      if (files.length > 1) {
        ui.status("Se detectaron " + files.length + " archivos; solo se " +
          "subirá el primero (" + files[0].name + "). Sube los demás por separado.");
      }
      startUpload(files[0]);
    });

    // --- pegar desde el portapapeles ---
    // Se escucha en document (no solo en el dropzone) para que el usuario
    // no tenga que hacer clic primero -- pero SOLO se actúa si el
    // portapapeles realmente contiene un archivo. Si solo hay texto, se
    // deja pasar el evento sin tocarlo para no romper el pegado normal en
    // los <input> de metadata (nombre, fechas, etc.).
    document.addEventListener("paste", function (e) {
      var cd = e.clipboardData;
      if (!cd) return;

      var file = null;
      if (cd.files && cd.files.length > 0) {
        file = cd.files[0];
      } else if (cd.items) {
        for (var i = 0; i < cd.items.length; i++) {
          if (cd.items[i].kind === "file") { file = cd.items[i].getAsFile(); break; }
        }
      }
      if (!file) return; // portapapeles solo tenía texto: no hacer nada

      e.preventDefault();
      startUpload(file);
    }, { signal: controller.signal });

    window.addEventListener("pagehide", function () { controller.abort(); });

    // El submit real de metadatos no debe disparar mientras la subida a
    // Azure sigue en curso -- el botón ya está disabled durante el
    // fetch/XHR, pero se refuerza aquí como defensa en profundidad.
    document.getElementById("hf-upload-form").addEventListener("submit", function (e) {
      if (uploading) { e.preventDefault(); return; }
      submitBtn.disabled = true; // evita doble POST del formulario visible
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
