document.querySelectorAll('.inline-document-upload').forEach(widget => {
  if (widget.dataset.initialized) return;
  widget.dataset.initialized = 'true';
  const type = widget.querySelector('.inline-document-type');
  const files = widget.querySelector('.inline-document-files');
  const button = widget.querySelector('.inline-document-submit');
  const result = widget.querySelector('.inline-document-result');
  const member = widget.dataset.memberSelector ? document.querySelector(widget.dataset.memberSelector) : null;
  // The page reloads after an upload to refresh its checklist, so the result is
  // kept for that reload or it would vanish the moment it appeared.
  const savedKey = 'documentUploadResult:' + window.location.pathname;
  function showResult(message, category) {
    result.textContent = message;
    result.classList.add('callout');
    result.classList.toggle('callout-error', category === 'error');
  }
  function saveResult(message, category) {
    try { sessionStorage.setItem(savedKey, JSON.stringify({ message, category })); } catch (error) { /* storage blocked */ }
  }
  function clearSaved() {
    try { sessionStorage.removeItem(savedKey); } catch (error) { /* storage blocked */ }
  }
  function restoreResult() {
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(savedKey)); } catch (error) { return; }
    if (!saved || !saved.message) return;
    showResult(saved.message, saved.category);
    result.scrollIntoView({ block: 'center' });
  }
  function updateType() {
    const bank = type.value === 'Bank Statement';
    files.accept = bank ? '.pdf' : '.pdf,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx';
    widget.querySelector('.inline-document-password').hidden = !bank;
    widget.querySelector('.inline-document-help').textContent = bank ?
      'Bank statements: PDF only. Select files covering at least 2 months. Income, cash flow and a suggested debit day are analysed automatically.' :
      'Accepted files: PDF, JPG, PNG, DOC, DOCX, XLS, XLSX.';
    button.textContent = bank ? 'Upload and analyse' : 'Upload';
    files.value = '';
  }
  type.addEventListener('change', updateType);
  if (member) member.addEventListener('change', () => { files.value = ''; result.textContent = ''; });
  button.addEventListener('click', async () => {
    const id = member ? member.value : widget.dataset.memberId;
    if (!id) { result.textContent = 'Select a member before uploading.'; return; }
    if (!files.files.length) { result.textContent = 'Select at least one file.'; return; }
    if (type.value === 'Bank Statement' && [...files.files].some(f => !f.name.toLowerCase().endsWith('.pdf'))) {
      result.textContent = 'Bank statements must be PDF files.'; return;
    }
    if ([...files.files].reduce((sum,f) => sum+f.size, 0) > 15*1024*1024) {
      result.textContent = 'Keep the total upload below 15 MB.'; return;
    }
    const data = new FormData();
    data.append('document_type', type.value);
    data.append('pdf_password', widget.querySelector('.inline-pdf-password').value);
    for (const file of files.files) data.append('document', file);
    button.disabled = true;
    if (member) member.disabled = true;
    clearSaved();
    result.classList.remove('callout', 'callout-error');
    result.textContent = 'Uploading and analysing…';
    try {
      const response = await fetch(widget.dataset.uploadUrl.replace('/0/', '/'+id+'/'), {
        method: 'POST', body: data, headers: { 'Accept': 'application/json',
          'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '' }
      });
      if (response.redirected) throw new Error('Your session expired. Sign in again before uploading.');
      const payload = await response.json().catch(() => null);
      if (!payload) throw new Error(response.status === 413 ?
        'Keep the total upload below 15 MB.' : 'Unable to upload. Please try again.');
      const message = payload.message || payload.error || 'Upload failed. Please try again.';
      showResult(message, response.ok ? payload.category : 'error');
      if (response.ok) {
        files.value = '';
        widget.querySelector('.inline-pdf-password').value = '';
        widget.dispatchEvent(new CustomEvent('documents:uploaded', { bubbles: true, detail: {memberId: id} }));
        if (widget.dataset.reload === 'true') {
          saveResult(message, payload.category);
          window.location.reload();
        }
      }
    } catch (error) { showResult(error.message || 'Upload failed. Please try again.', 'error'); }
    finally { button.disabled = false; if (member) member.disabled = false; }
  });
  restoreResult();
});
