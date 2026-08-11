(function () {
  "use strict";
  var POLL_INTERVAL_MS = 7000;
  var ACTIVE_STATUSES = ["PENDING", "PROCESSING"];

  function init() {
    var table = document.getElementById("hf-file-list");
    if (!table) return;
    var endpoint = table.dataset.statusEndpoint;

    function trackedRows() {
      return Array.prototype.filter.call(
        table.querySelectorAll("tr[data-file-id]"),
        function (row) {
          var badge = row.querySelector(".badge");
          return ACTIVE_STATUSES.indexOf(badge.dataset.status) !== -1;
        }
      );
    }

    function applyUpdate(row, info) {
      var badge = row.querySelector(".badge");
      badge.className = "badge badge-" + info.status.toLowerCase();
      badge.dataset.status = info.status;
      badge.textContent = info.status_display;
    }

    var timer = null;

    function poll() {
      var rows = trackedRows();
      if (rows.length === 0) {
        if (timer) { clearInterval(timer); timer = null; }
        return;
      }
      // La paginación server-side (25/página) ya acota esto a un número
      // manejable; el endpoint además trunca a 50 ids por su cuenta.
      var ids = rows.map(function (r) { return r.dataset.fileId; }).join(",");
      fetch(endpoint + "?ids=" + encodeURIComponent(ids), { credentials: "same-origin" })
        .then(function (res) { return res.ok ? res.json() : {}; })
        .then(function (data) {
          rows.forEach(function (row) {
            var info = data[row.dataset.fileId];
            if (info) applyUpdate(row, info);
          });
        })
        .catch(function () { /* un poll fallido no es crítico: se reintenta en el siguiente ciclo */ });
    }

    if (trackedRows().length > 0) {
      timer = setInterval(poll, POLL_INTERVAL_MS);
    }

    // Pausa el sondeo si la pestaña está en segundo plano.
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        if (timer) { clearInterval(timer); timer = null; }
      } else if (!timer && trackedRows().length > 0) {
        timer = setInterval(poll, POLL_INTERVAL_MS);
      }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
