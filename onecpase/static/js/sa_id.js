/* SA ID number -> date of birth, from the first six digits (YYMMDD).
   Same rules as sa_id_birth_date() in leads.py: a real calendar date that is
   not in the future. Shared by the member add/edit and prospect-conversion forms. */
(function () {
  function saIdBirthDate(id, today) {
    const text = String(id || '').replace(/\s/g, '');
    // Letters mean a passport or foreign document - its digits are not a birth date.
    if (!/^\d{6,}$/.test(text)) return null;
    const yy = +text.slice(0, 2), mm = +text.slice(2, 4), dd = +text.slice(4, 6);
    today = today || new Date();
    for (const century of [2000, 1900]) {
      const year = century + yy;
      const born = new Date(year, mm - 1, dd);
      const real = born.getFullYear() === year && born.getMonth() === mm - 1 && born.getDate() === dd;
      if (real && born <= today) {
        return year + '-' + String(mm).padStart(2, '0') + '-' + String(dd).padStart(2, '0');
      }
    }
    return null;
  }

  // Put the ID's birth date in the date-of-birth input. `overwrite` is true when
  // the user is typing the ID, so a corrected ID corrects the date too; it is
  // false on page load, which must never replace a stored date. Either way a date
  // the user typed into the field themselves is left alone.
  function saIdFillDob(dobInput, iso, overwrite) {
    if (!dobInput.dataset.tracked) {
      dobInput.addEventListener('input', function () {
        dobInput.dataset.manual = dobInput.value ? '1' : '';
      });
      dobInput.dataset.tracked = '1';
    }
    if (!dobInput.value || (overwrite && dobInput.dataset.manual !== '1')) dobInput.value = iso;
  }

  window.saIdBirthDate = saIdBirthDate;
  window.saIdFillDob = saIdFillDob;
})();
