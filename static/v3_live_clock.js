/* One real Indianapolis clock for the persistent V3 sidebar.
   Requery the DOM on each tick so HTMX sidebar replacements stay current. */
(function () {
  "use strict";
  if (window.ind3LiveClockInterval) clearInterval(window.ind3LiveClockInterval);
  const zone = "America/Indiana/Indianapolis";
  const timeFormat = new Intl.DateTimeFormat("en-US", {
    timeZone: zone, hour: "numeric", minute: "2-digit", hour12: true
  });
  const dateFormat = new Intl.DateTimeFormat("en-US", {
    timeZone: zone, weekday: "long", month: "long", day: "numeric", year: "numeric"
  });
  function renderClock() {
    const now = new Date();
    document.querySelectorAll("[data-v3-live-time]").forEach(node => {
      node.textContent = timeFormat.format(now);
      node.dateTime = now.toISOString();
    });
    document.querySelectorAll("[data-v3-live-date]").forEach(node => {
      node.textContent = dateFormat.format(now);
    });
  }
  renderClock();
  window.ind3LiveClockInterval = window.setInterval(renderClock, 1000);
})();
