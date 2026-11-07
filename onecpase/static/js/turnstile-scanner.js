(() => {
  const input = document.getElementById('scannerCapture');
  const state = document.getElementById('scannerState');
  const result = document.getElementById('scanResult');
  const events = document.getElementById('scannerEvents');
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  let idleTimer;
  let resultTimer;
  let pending = 0;
  let scanQueue = Promise.resolve();

  function updateState() {
    const focused = document.activeElement === input && document.hasFocus();
    state.textContent = pending ? 'Checking…' : focused ? 'Ready to scan' : 'Click the barcode field';
    state.classList.toggle('badge-success', focused && !pending);
    state.classList.toggle('badge-warning', !focused || pending);
  }

  function showResult(decision, message) {
    clearTimeout(resultTimer);
    result.dataset.decision = decision;
    result.textContent = message;
    resultTimer = setTimeout(() => { result.textContent = ''; }, 6000);
  }

  async function checkScan(credential) {
    try {
      const response = await fetch(input.dataset.scanUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
        body: JSON.stringify({credential}),
        signal: AbortSignal.timeout(15000),
      });
      if (response.redirected) throw new Error('Sign in again to continue scanning.');
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('Reload this page and try again.');
      }
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Please try again.');

      document.getElementById('scannerEmpty')?.remove();
      events.insertAdjacentHTML('afterbegin', data.event_html);
      if (events.rows.length > 100) events.deleteRow(-1);
      const member = data.member?.name || 'Unknown barcode';
      showResult(data.decision, `${data.decision.toUpperCase()}: ${member} — ${data.reason}`);
    } catch (error) {
      const message = ['TimeoutError', 'TypeError'].includes(error.name)
        ? 'Check Recent Turnstile Events before scanning again.'
        : error.message;
      showResult('error', `Scan could not be confirmed. ${message}`);
    } finally {
      pending -= 1;
      updateState();
    }
  }

  function submitScan() {
    clearTimeout(idleTimer);
    const credential = input.value.trim();
    input.value = '';
    if (!credential) return;

    pending += 1;
    updateState();
    scanQueue = scanQueue.then(() => checkScan(credential));
  }

  input.addEventListener('input', () => {
    clearTimeout(idleTimer);
    // Scanners that send only the barcode are detected after a short pause.
    if (input.value) idleTimer = setTimeout(submitScan, 300);
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' || (event.key === 'Tab' && !event.shiftKey && input.value.trim())) {
      event.preventDefault();
      submitScan();
    }
  });
  input.addEventListener('focus', updateState);
  input.addEventListener('blur', updateState);
  window.addEventListener('focus', updateState);
  window.addEventListener('blur', updateState);
  updateState();
})();
