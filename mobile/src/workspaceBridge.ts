// This bridge only captures the currently displayed, already-authorized page.
// Staff sign-in, CSRF, permissions and file access remain with the web server.
export const workspaceBridge = String.raw`
(function () {
  if (window.__onecpaseWorkspaceBridge) return true;
  window.__onecpaseWorkspaceBridge = true;
  window.print = async function () {
    try {
      window.dispatchEvent(new Event('beforeprint'));
      var copy = document.documentElement.cloneNode(true);
      copy.querySelectorAll('script, iframe, object, embed').forEach(function (node) { node.remove(); });
      var base = document.createElement('base');
      base.href = document.baseURI;
      copy.querySelector('head').prepend(base);
      var css = '';
      Array.from(document.styleSheets).forEach(function (sheet) {
        try { css += Array.from(sheet.cssRules).map(function (rule) { return rule.cssText; }).join('\n'); }
        catch (_) { /* Cross-origin font styles keep their original link. */ }
      });
      var style = document.createElement('style');
      style.textContent = css;
      copy.querySelector('head').appendChild(style);
      var fields = copy.querySelectorAll('input, textarea, select');
      document.querySelectorAll('input, textarea, select').forEach(function (field, index) {
        var target = fields[index];
        if (!target) return;
        if (field.tagName === 'TEXTAREA') target.textContent = field.value;
        else if (field.tagName === 'SELECT') Array.from(target.options).forEach(function (option, i) {
          if (field.options[i].selected) option.setAttribute('selected', ''); else option.removeAttribute('selected');
        });
        else {
          target.setAttribute('value', field.type === 'password' ? '' : field.value);
          if (field.checked) target.setAttribute('checked', ''); else target.removeAttribute('checked');
        }
      });
      var canvases = copy.querySelectorAll('canvas');
      document.querySelectorAll('canvas').forEach(function (canvas, index) {
        try {
          var image = document.createElement('img');
          image.src = canvas.toDataURL('image/png');
          image.style.cssText = canvas.style.cssText;
          image.width = canvas.width; image.height = canvas.height;
          canvases[index].replaceWith(image);
        } catch (_) { /* A cross-origin canvas cannot be serialized. */ }
      });
      await Promise.all(Array.from(copy.querySelectorAll('img')).map(async function (image) {
        try {
          var url = new URL(image.getAttribute('src'), document.baseURI);
          if (url.origin !== location.origin) return;
          var response = await fetch(url.href, { credentials: 'same-origin' });
          if (!response.ok) return;
          var blob = await response.blob();
          image.src = await new Promise(function (resolve, reject) {
            var reader = new FileReader(); reader.onload = function () { resolve(reader.result); };
            reader.onerror = reject; reader.readAsDataURL(blob);
          });
        } catch (_) { /* Keep the original image if it cannot be inlined. */ }
      }));
      window.ReactNativeWebView.postMessage(JSON.stringify({
        type: 'print', url: location.href, html: '<!doctype html>' + copy.outerHTML
      }));
    } catch (_) {
      window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'printError', url: location.href }));
    } finally { window.dispatchEvent(new Event('afterprint')); }
  };
  true;
})();
true;
`;
