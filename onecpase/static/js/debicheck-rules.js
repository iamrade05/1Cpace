document.querySelectorAll('[data-mandate-name], [data-mandate-date]').forEach(input => {
  const message = document.createElement('small');
  message.setAttribute('role', 'status');
  message.className = 'text-muted';
  input.after(message);
  function validate() {
    const form = input.form;
    const payment = form?.querySelector('[name="payment_type"]');
    const applies = !payment || ['Debit Order', 'Third-Party Debit Order'].includes(payment.value);
    const value = input.value.trim();
    let error = '';
    if (applies && value && input.hasAttribute('data-mandate-name')) {
      const identity = form?.querySelector('[name="id_type"], [name="debicheck_id_type"]');
      const words = value.split(/ +/);
      if (!/^[A-Za-z]+(?: +[A-Za-z]+)*$/.test(value) ||
          (identity?.value !== '4' && (words.length < 2 || !/^[A-Z]{1,6}$/.test(words[0])))) {
        error = 'Enter initials and surname, for example J P Smith. Letters and spaces only; no dots, asterisks, dashes or other symbols.';
      }
    }
    if (applies && value && input.hasAttribute('data-mandate-date') && value < input.min) {
      error = input.hasAttribute('data-sales-lead-time')
        ? 'Change the first debit date: it must be more than 7 days from today. Earliest allowed: ' + input.min + '.'
        : 'Change the first debit date: it cannot be in the past. Earliest allowed: ' + input.min + '.';
    }
    input.setCustomValidity(error);
    message.textContent = error;
    input.setAttribute('aria-invalid', error ? 'true' : 'false');
  }
  input.addEventListener('input', validate);
  input.addEventListener('change', validate);
  input.form?.addEventListener('change', validate);
  validate();
});
