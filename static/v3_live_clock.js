/* Live Indianapolis date/time, explicitly separate from a frozen test clock. */
(function () {
  "use strict";
  const nodes = document.querySelectorAll("[data-v3-live-clock]");
  if (!nodes.length) return;
  const clockFormat = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Indiana/Indianapolis",
    weekday: "short", month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true
  });
  function renderClock() {
    const now = new Date();
    const text = clockFormat.format(now);
    for (const node of nodes) {
      node.textContent = text;
      node.dateTime = now.toISOString();
    }
  }
  renderClock();
  window.setInterval(renderClock, 1000);
})();
