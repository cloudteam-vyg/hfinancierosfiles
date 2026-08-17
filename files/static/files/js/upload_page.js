(function () {
  "use strict";

  var utils = window.HFUtils; // ver files/static/files/js/hf_utils.js

  function humanBytes(n) {
    if (!n && n !== 0) return "";
    var units = ["B", "KB", "MB", "GB"];
    var i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return n.toFixed(i === 0 ? 0 : 1) + " " + units[i];
  }

  function init() {
    var dropzone = document.getElementById("hf-dropzone");
    if (!dropzone) return;

    var form = document.getElementById("hf-upload-form");
    var fileInput = document.getElementById("id_file");
    var submitBtn = document.getElementById("hf-submit-btn");
    var box = dropzone.querySelector(".hf-upload-box");
    var fill = box.querySelector(".hf-upload-fill");
    var statusEl = box.querySelector(".hf-upload-status");
    var maxMb = parseInt(dropzone.dataset.maxUploadSizeMb || "300", 10);

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

    function setFile(file) {
      if (!file) return;
      box.classList.remove("hf-upload-error", "hf-upload-ok");

      if (file.size <= 0) { ui.error("El archivo está vacío."); return; }
      if (file.size > maxMb * 1024 * 1024) {
        ui.error("El archivo pesa " + humanBytes(file.size) + " y el límite es " + maxMb + " MB.");
        return;
      }

      var dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;

      var nameEl = document.getElementById("id_name");
      if (nameEl && !nameEl.value) nameEl.value = file.name.slice(0, 150);

      ui.status("Listo: " + file.name + " (" + humanBytes(file.size) + ")");
    }

    // --- selección clásica ---
    dropzone.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", function () {
      setFile(fileInput.files && fileInput.files[0]);
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
      setFile(files[0]);
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
      setFile(file);
    });

    // --- envío por XHR: única forma de exponer progreso real de subida ---
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (submitBtn.disabled) return;
      submitBtn.disabled = true;
      box.classList.remove("hf-upload-error", "hf-upload-ok");
      fill.style.width = "0%";

      var xhr = new XMLHttpRequest();
      xhr.open("POST", form.action || window.location.href);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");

      xhr.upload.addEventListener("progress", function (evt) {
        if (evt.lengthComputable) ui.progress(evt.loaded, evt.total);
      });

      xhr.addEventListener("load", function () {
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (err) { /* no-op */ }

        if (xhr.status >= 200 && xhr.status < 300 && data && data.success) {
          ui.done("Archivo subido correctamente.");
          window.location.href = data.redirect_url;
          return;
        }

        submitBtn.disabled = false;
        var messages = data ? utils.formErrorMessages(data.errors) : [];
        ui.error(messages.join(" ") || "No se pudo guardar el archivo (error del servidor).");
      });

      xhr.addEventListener("error", function () {
        submitBtn.disabled = false;
        ui.error("Fallo de red durante la subida. Verifica tu conexión e inténtalo de nuevo.");
      });

      xhr.send(new FormData(form));
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
