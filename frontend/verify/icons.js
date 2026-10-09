// Inline SVG icons for the verification wizard. Same style as the icons in
// index.html: 24 unit viewBox, no fill, 2 unit round stroke, color from
// currentColor. Each function returns a fresh SVG element so it can be
// appended anywhere. Nothing here touches the network or storage.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";

  var PATHS = {
    // Paper rocket used on the "Send OTP" button.
    rocket: [
      "M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z",
      "M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z",
      "M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0",
      "M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5",
    ],
    // Speech bubble for the SMS option.
    sms: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
    // Envelope for the email option.
    mail: [
      "M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
      "M22 6l-10 7L2 6",
    ],
    back: ["M19 12H5", "M12 19l-7-7 7-7"],
    check: ["M20 6L9 17l-5-5"],
  };

  function icon(name, size) {
    var paths = PATHS[name];
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", String(size || 20));
    svg.setAttribute("height", String(size || 20));
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.setAttribute("class", "slt-v-icon");
    (paths || []).forEach(function (d) {
      var p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      svg.appendChild(p);
    });
    return svg;
  }

  window.SltVerifyIcons = { icon: icon, names: Object.keys(PATHS) };
})();
