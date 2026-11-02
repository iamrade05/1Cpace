/* Keep destructive confirmations in the page and preserve form validation. */
function showInlineConfirmation(form, message, submitter = null) {
  if (form.querySelector('.inline-confirmation')) return;
  const box = document.createElement('div');
  box.className = 'inline-confirmation';
  box.setAttribute('role', 'group');
  const text = document.createElement('p');
  text.textContent = message;
  const proceed = document.createElement('button');
  proceed.type = 'button';
  proceed.className = 'btn btn-danger';
  proceed.textContent = 'Confirm';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-ghost';
  cancel.textContent = 'Cancel';
  proceed.addEventListener('click', () => {
    form.dataset.confirmed = 'true';
    form.requestSubmit(submitter || undefined);
  });
  cancel.addEventListener('click', () => { box.remove(); });
  box.append(text, cancel, proceed);
  form.append(box);
  cancel.focus();
}
document.addEventListener('submit', event => {
  const form = event.target;
  if (!form.matches('form[data-confirm]')) return;
  if (form.dataset.confirmed === 'true') {
    delete form.dataset.confirmed;
    return;
  }
  event.preventDefault();
  showInlineConfirmation(form, form.dataset.confirm, event.submitter);
});
document.addEventListener('click', event => {
  const button = event.target.closest('button[data-confirm]');
  if (!button || !button.form) return;
  event.preventDefault();
  showInlineConfirmation(button.form, button.dataset.confirm, button);
});
function showUiMessage(message, tone = 'info') {
  let box = document.getElementById('ui-message');
  if (!box) {
    box = document.createElement('div');
    box.id = 'ui-message';
    box.setAttribute('role', 'status');
    (document.querySelector('.content') || document.querySelector('main') || document.body).prepend(box);
  }
  box.className = 'callout callout-' + tone;
  box.textContent = message;
  box.scrollIntoView({block:'nearest'});
}
document.querySelectorAll('form').forEach(form => {
  if ((form.getAttribute('method') || 'get').toLowerCase() === 'get' || form.querySelector('[name="csrf_token"]')) return;
  const token = document.querySelector('meta[name="csrf-token"]')?.content;
  if (!token) return;
  const input = document.createElement('input');
  input.type = 'hidden';
  input.name = 'csrf_token';
  input.value = token;
  form.prepend(input);
});
// Refresh staff alerts without reloading the current work page.
document.querySelectorAll('[data-dashboard-period-form]').forEach(form => {
  const choice=form.querySelector('[data-dashboard-period]');
  const update=()=>{
    const group=['day','week'].includes(choice.value)?'date':choice.value==='custom'?'custom':'cycle';
    form.querySelectorAll('[data-period-field]').forEach(label=>{
      const active=label.dataset.periodField===group;
      label.hidden=!active;
      label.querySelectorAll('input').forEach(input=>{input.disabled=!active;});
    });
  };
  choice.addEventListener('change',update);
  update();
});
document.addEventListener('DOMContentLoaded', () => {
  const link = document.querySelector('[data-notification-count-url]');
  if (!link) return;
  let refreshing = false;
  async function refreshNotifications() {
    if (document.hidden || refreshing) return;
    refreshing = true;
    try {
      const response = await fetch(link.dataset.notificationCountUrl, {headers: {'Accept': 'application/json'}});
      if (!response.ok || response.redirected) return;
      const data = await response.json();
      link.querySelector('[data-notification-count]').textContent = data.count;
    } catch (_) { /* Keep the last count if the connection is unavailable. */ }
    finally { refreshing = false; }
  }
  refreshNotifications();
  setInterval(refreshNotifications, 60000);
});
