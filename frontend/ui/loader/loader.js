/* Logo loader: SltUi.loader.show(target, {label, size, overlay}) -> handle.
 * handle.hide() removes the DOM it added and restores the target; there are
 * no listeners to leave behind. label must already be localized by the
 * caller. size is "md" (default) or "sm". overlay:true covers the target
 * (it gets position:relative while shown if it was static). */
(function () {
  "use strict";

  // The logo path is resolved from this script, so it also works from a subfolder page.
  var LOGO = (function () {
    var src = (document.currentScript && document.currentScript.src) || "";
    var i = src.indexOf("/ui/");
    return i >= 0 ? src.slice(0, i) + "/assets/mobitel-logo.png" : "assets/mobitel-logo.png";
  })();

  function show(target, opts) {
    opts = opts || {};
    if (!target || !target.appendChild) return { hide: function () {}, setLabel: function () {} };

    var root = document.createElement("div");
    root.className = "slt-loader" + (opts.size === "sm" ? " lod-sm" : "") + (opts.overlay ? " lod-overlay" : "");
    root.setAttribute("role", "status");
    root.setAttribute("aria-live", "polite");

    var mark = document.createElement("div");
    mark.className = "lod-mark";
    mark.setAttribute("aria-hidden", "true");
    var glow = document.createElement("div");
    glow.className = "lod-glow";
    var logo = document.createElement("img");
    logo.className = "lod-logo";
    logo.src = LOGO;
    logo.alt = "";
    mark.appendChild(glow);
    mark.appendChild(logo);
    root.appendChild(mark);

    var label = document.createElement("p");
    label.className = "lod-label";
    label.textContent = opts.label || "";
    root.appendChild(label);

    var restorePosition = null;
    if (opts.overlay && window.getComputedStyle(target).position === "static") {
      restorePosition = target.style.position;
      target.style.position = "relative";
    }
    target.appendChild(root);

    var hidden = false;
    return {
      element: root,
      setLabel: function (text) { if (!hidden) label.textContent = text || ""; },
      hide: function () {
        if (hidden) return;
        hidden = true;
        if (root.parentNode) root.parentNode.removeChild(root);
        if (restorePosition !== null) target.style.position = restorePosition;
      },
    };
  }

  window.SltUi = window.SltUi || {};
  window.SltUi.loader = { show: show };
})();
