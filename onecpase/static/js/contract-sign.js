// Signing on the contract link a member opens on their phone: unlocks once they
// have scrolled through the agreement, then captures finger signatures.
(function () {
  const form = document.getElementById('contractSignForm');
  const end = document.getElementById('contractReadEnd');
  if (!end) return;
  const hint = document.getElementById('contractScrollHint');

  function revealWhenRead(reveal) {
    const observer = new IntersectionObserver(function (entries) {
      if (!entries.some(function (entry) { return entry.isIntersecting; })) return;
      observer.disconnect();
      hint.hidden = true;
      reveal();
    });
    observer.observe(end);
  }

  if (!form) {
    revealWhenRead(function () {});
    return;
  }

  function createPad(canvasId, outputId, errorId, clearId) {
    const canvas = document.getElementById(canvasId);
    const output = document.getElementById(outputId);
    const error = document.getElementById(errorId);
    const ctx = canvas.getContext('2d');
    let drawing = false;
    let hasStroke = false;
    let sizedWidth = 0;

    function size() {
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = rect.width * ratio;
      canvas.height = rect.height * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.lineWidth = 2.4;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.strokeStyle = '#1F2933';
      sizedWidth = rect.width;
      hasStroke = false;
    }

    function point(evt) {
      const rect = canvas.getBoundingClientRect();
      return { x: evt.clientX - rect.left, y: evt.clientY - rect.top };
    }

    canvas.addEventListener('pointerdown', function (evt) {
      evt.preventDefault();
      canvas.setPointerCapture(evt.pointerId);
      drawing = true;
      const p = point(evt);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
    });
    canvas.addEventListener('pointermove', function (evt) {
      if (!drawing) return;
      evt.preventDefault();
      const p = point(evt);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
      hasStroke = true;
      error.hidden = true;
    });
    function stop() { drawing = false; }
    canvas.addEventListener('pointerup', stop);
    canvas.addEventListener('pointercancel', stop);
    // A signature is not a scroll: left as a gesture, a quick final stroke can make
    // the browser swallow the next tap on the Sign button.
    function noGesture(evt) { evt.preventDefault(); }
    canvas.addEventListener('touchstart', noGesture, { passive: false });
    canvas.addEventListener('touchmove', noGesture, { passive: false });

    document.getElementById(clearId).addEventListener('click', size);

    // Scrolling a phone browser fires resize as its address bar hides, so only a
    // real width change (rotating the phone) may reset the pad.
    window.addEventListener('resize', function () {
      if (!form.hidden && canvas.getBoundingClientRect().width !== sizedWidth) size();
    });

    return {
      size: size,
      // Copies the signature into the form, or shows the message if there is none.
      capture: function () {
        if (!hasStroke) {
          error.hidden = false;
          return false;
        }
        output.value = canvas.toDataURL('image/png');
        return true;
      },
    };
  }

  const pads = [createPad('contractSignPad', 'signatureData', 'contractSignError', 'contractSignClear')];
  if (document.getElementById('contractDebitPad')) {
    pads.unshift(createPad('contractDebitPad', 'debitSignatureData', 'contractDebitError', 'contractDebitClear'));
  }

  const payerToggle = document.getElementById('thirdPartyPayer');
  if (payerToggle) {
    const payerFields = document.getElementById('thirdPartyFields');
    const syncPayer = function () {
      payerFields.hidden = !payerToggle.checked;
      payerFields.querySelectorAll('input').forEach(function (input) { input.required = payerToggle.checked; });
    };
    payerToggle.addEventListener('change', syncPayer);
    syncPayer();
  }

  form.addEventListener('submit', function (evt) {
    const captured = pads.map(function (pad) { return pad.capture(); });
    if (captured.includes(false)) {
      evt.preventDefault();
      return;
    }
    form.querySelector('button[type=submit]').disabled = true;
  });

  revealWhenRead(function () {
    form.hidden = false;
    document.getElementById('contractReadToEnd').value = '1';
    pads.forEach(function (pad) { pad.size(); });
  });
})();
