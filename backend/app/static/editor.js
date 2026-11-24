/* Tiny code-editor behaviour for textarea.code: Tab indents, Shift+Tab dedents,
   Enter keeps indentation (and adds one level after a trailing colon), Ctrl+Enter runs. */
(function () {
  var IND = "    ";
  document.addEventListener("keydown", function (ev) {
    var el = ev.target;
    if (!el || el.tagName !== "TEXTAREA" || !el.classList.contains("code")) return;
    var v = el.value, s = el.selectionStart, e = el.selectionEnd;
    if (ev.key === "Tab") {
      ev.preventDefault();
      var ls = v.lastIndexOf("\n", s - 1) + 1;
      if (s === e && !ev.shiftKey) { el.setRangeText(IND, s, e, "end"); }
      else {
        var block = v.slice(ls, e), out;
        out = ev.shiftKey ? block.replace(/^( {1,4}|\t)/gm, "") : block.replace(/^/gm, IND);
        el.setRangeText(out, ls, e, "select"); el.selectionStart = ls;
      }
      el.dispatchEvent(new Event("input"));
    } else if (ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey && s === e) {
      var ls2 = v.lastIndexOf("\n", s - 1) + 1, line = v.slice(ls2, s);
      var indent = (line.match(/^[ \t]*/) || [""])[0];
      if (/:\s*$/.test(line)) indent += IND;
      if (indent) { ev.preventDefault(); el.setRangeText("\n" + indent, s, e, "end"); el.dispatchEvent(new Event("input")); }
    } else if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      var btn = el.closest("form, .card, main");
      btn = btn && btn.querySelector("button[hx-post]");
      if (btn) { ev.preventDefault(); htmx.trigger(btn, "click"); }
    }
  });
})();
