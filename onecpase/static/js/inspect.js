// Inspect mode: click any part of the page to see which template file/block
// (and which Python view function) rendered it. Only loaded when the server
// has INSPECT_MODE on (see onecpase/inspect_mode.py) — never ships to real users.
(function () {
  'use strict';

  var STORAGE_KEY = 'inspect:active';
  var active = sessionStorage.getItem(STORAGE_KEY) === '1';

  var style = document.createElement('style');
  style.textContent = [
    '.inspect-toggle{position:fixed;bottom:16px;right:16px;z-index:2147483000;',
    'font:13px/1.4 system-ui,sans-serif;background:#1f2937;color:#fff;border:1px solid #374151;',
    'border-radius:999px;padding:8px 14px;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.3);}',
    '.inspect-toggle.on{background:#2563eb;border-color:#1d4ed8;}',
    '.inspect-hover{outline:2px solid #2563eb!important;outline-offset:-2px;cursor:crosshair!important;}',
    '.inspect-panel{position:fixed;z-index:2147483000;max-width:420px;font:12px/1.5 ui-monospace,Consolas,monospace;',
    'background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:8px;padding:10px 12px;',
    'box-shadow:0 8px 24px rgba(0,0,0,.4);}',
    '.inspect-panel .row{display:flex;align-items:baseline;gap:8px;padding:3px 0;border-bottom:1px solid #27303f;}',
    '.inspect-panel .row:last-child{border-bottom:none;}',
    '.inspect-panel .tag{color:#9ca3af;font-size:10px;text-transform:uppercase;flex:0 0 auto;}',
    '.inspect-panel .path{flex:1 1 auto;word-break:break-all;}',
    '.inspect-panel .copy{flex:0 0 auto;background:#1f2937;color:#9ca3af;border:1px solid #374151;',
    'border-radius:4px;cursor:pointer;font-size:11px;padding:1px 6px;}',
    '.inspect-panel .copy:hover{color:#fff;}',
    '.inspect-panel .close{position:absolute;top:4px;right:8px;cursor:pointer;color:#6b7280;}'
  ].join('');
  document.head.appendChild(style);

  var toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'inspect-toggle';
  toggle.textContent = '🔎 Inspect';
  document.body.appendChild(toggle);

  var panel = null;
  var hovered = null;

  function setActive(v) {
    active = v;
    sessionStorage.setItem(STORAGE_KEY, v ? '1' : '0');
    toggle.classList.toggle('on', v);
    toggle.textContent = v ? '🔎 Inspecting…' : '🔎 Inspect';
    if (!v) {
      closePanel();
      if (hovered) { hovered.classList.remove('inspect-hover'); hovered = null; }
    }
  }
  setActive(active);

  toggle.addEventListener('click', function () { setActive(!active); });

  function closePanel() {
    if (panel) { panel.remove(); panel = null; }
  }

  // Build the region list once per click (DOM can change between clicks, so
  // we don't cache it): every {% block %}/{% include %} leaves a matched
  // <!--@inspect:BEGIN label--> ... <!--@inspect:END--> pair of comment
  // nodes; a stack over comments in document order recovers nesting.
  function collectRegions() {
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_COMMENT);
    var stack = [];
    var regions = [];
    var node;
    while ((node = walker.nextNode())) {
      var text = node.nodeValue || '';
      var beginMatch = /^@inspect:BEGIN (.*)$/.exec(text.trim());
      if (beginMatch) {
        stack.push({ label: beginMatch[1], start: node, depth: stack.length });
        continue;
      }
      if (text.trim() === '@inspect:END' && stack.length) {
        var open = stack.pop();
        regions.push({ label: open.label, start: open.start, end: node, depth: open.depth });
      }
    }
    return regions;
  }

  function isBetween(start, el, end) {
    var afterStart = start.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING;
    var beforeEnd = el.compareDocumentPosition(end) & Node.DOCUMENT_POSITION_FOLLOWING;
    return !!afterStart && !!beforeEnd;
  }

  function regionsFor(el) {
    var matches = collectRegions().filter(function (r) { return isBetween(r.start, el, r.end); });
    matches.sort(function (a, b) { return b.depth - a.depth; });
    return matches;
  }

  function showPanel(x, y, rows) {
    closePanel();
    panel = document.createElement('div');
    panel.className = 'inspect-panel';

    var close = document.createElement('span');
    close.className = 'close';
    close.textContent = '✕';
    close.addEventListener('click', closePanel);
    panel.appendChild(close);

    rows.forEach(function (row) {
      var r = document.createElement('div');
      r.className = 'row';

      var tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = row.tag;
      r.appendChild(tag);

      var path = document.createElement('span');
      path.className = 'path';
      path.textContent = row.text;
      r.appendChild(path);

      var copy = document.createElement('button');
      copy.type = 'button';
      copy.className = 'copy';
      copy.textContent = 'copy';
      copy.addEventListener('click', function () {
        navigator.clipboard.writeText(row.text).catch(function () {});
        copy.textContent = '✓';
        setTimeout(function () { copy.textContent = 'copy'; }, 900);
      });
      r.appendChild(copy);

      panel.appendChild(r);
    });

    document.body.appendChild(panel);
    var vw = window.innerWidth, vh = window.innerHeight;
    var pw = panel.offsetWidth, ph = panel.offsetHeight;
    panel.style.left = Math.min(x + 8, vw - pw - 8) + 'px';
    panel.style.top = Math.min(y + 8, vh - ph - 8) + 'px';
  }

  document.addEventListener('mouseover', function (e) {
    if (!active) return;
    if (e.target.closest('.inspect-toggle, .inspect-panel')) return;
    if (hovered) hovered.classList.remove('inspect-hover');
    hovered = e.target;
    hovered.classList.add('inspect-hover');
  }, true);

  document.addEventListener('click', function (e) {
    if (!active) return;
    if (e.target.closest('.inspect-toggle, .inspect-panel')) return;
    e.preventDefault();
    e.stopPropagation();

    var rows = regionsFor(e.target).map(function (r) {
      return { tag: 'template', text: r.label };
    });

    var meta = window.__INSPECT_META__;
    if (meta) {
      var view = meta.file
        ? meta.file + ':' + meta.line + (meta.name ? ' (' + meta.name + ')' : '')
        : '(no view function — static/error page)';
      rows.push({ tag: 'view fn', text: view });
      rows.push({ tag: 'route', text: (meta.blueprint ? meta.blueprint + '.' : '') + (meta.endpoint || '?') });
    }

    if (!rows.length) rows.push({ tag: 'info', text: 'No template markers found here.' });

    showPanel(e.clientX, e.clientY, rows);
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closePanel();
  });
})();
