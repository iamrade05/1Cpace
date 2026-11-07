/* Table tools: search, column filters, sorting, show/hide columns, rows per page,
 * totals for amount columns, remembered settings, and CSV/Excel export.
 *
 * Applies to every <table> with a header row and at least three data rows.
 * Opt out with data-table-tools="off". A cell can set data-sort="..." to sort by
 * a different value than it shows. Only the rows on the page are covered: a
 * list the server splits into pages is filtered and exported page by page.
 * Every export is recorded on the server first; nothing downloads if that fails.
 */
(function () {
  'use strict';

  const MIN_ROWS = 3;
  const PAGE_SIZES = [25, 50, 100, 0]; // 0 = all
  const LOG_URL = '/table-export-log';

  const clean = (s) => String(s || '').replace(/\s+/g, ' ').replace(/^[•·]\s*/, '').trim();

  function rowWidth(row) {
    let w = 0;
    for (const c of row.cells) w += c.colSpan || 1;
    return w;
  }

  function cellValues(cell) {
    if (!cell) return [''];
    const field = cell.querySelector('input:not([type=hidden]):not([type=checkbox]), select, textarea');
    if (field && clean(cell.textContent) === '') {
      return [clean(field.tagName === 'SELECT' ? (field.selectedOptions[0] || {}).text : field.value)];
    }
    const leaves = [...cell.querySelectorAll('span, a, .badge')].filter(
      (el) => el.children.length === 0 && clean(el.textContent) !== ''
    );
    if (leaves.length >= 2) {
      const joined = clean(leaves.map((el) => el.textContent).join(' '));
      if (joined.length >= clean(cell.textContent).length - 2) return leaves.map((el) => clean(el.textContent));
    }
    return [clean(cell.textContent)];
  }

  const NUMBER_RE = /^(R\s?)?-?[\d\s,]*\d(\.\d+)?%?$/i;
  function parseNumber(text) {
    const t = clean(text).replace(/^#/, '');
    if (!t || !NUMBER_RE.test(t)) return null;
    const n = Number(t.replace(/^R\s?/i, '').replace(/[\s,%]/g, ''));
    return Number.isFinite(n) ? n : null;
  }
  function parseDate(text) {
    const t = clean(text);
    if (!/\d{4}/.test(t) || !/^\d{1,4}[-/ ]/.test(t)) return null;
    const n = Date.parse(/^\d{4}-\d{2}-\d{2} \d/.test(t) ? t.replace(' ', 'T') : t);
    return Number.isFinite(n) ? n : null;
  }
  function isMoney(text) {
    const t = clean(text);
    return /^R\s?-?[\d\s,]*\d/i.test(t) || /^-?[\d,]+\.\d{2}$/.test(t);
  }
  function formatMoney(n, withR) {
    const [whole, cents] = Math.abs(n).toFixed(2).split('.');
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (n < 0 ? '-' : '') + (withR ? 'R' : '') + grouped + '.' + cents;
  }

  function store(key, value) {
    try {
      if (value === undefined) return JSON.parse(localStorage.getItem(key) || 'null');
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) { return null; }
    return null;
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const child of children || []) node.append(child);
    return node;
  }

  function enhance(table, index) {
    const headRow = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
    const body = table.tBodies[0];
    if (!headRow || !body) return;
    const headers = [...headRow.cells];
    const width = headers.length;
    if (headers.some((th) => (th.colSpan || 1) > 1)) return;
    const rows = [...body.rows].filter((r) => rowWidth(r) === width && r.cells.length === width);
    if (rows.length < MIN_ROWS) return;
    const others = [...body.rows].filter((r) => !rows.includes(r));

    const columns = headers.map((th, i) => {
      const label = clean(th.textContent);
      const values = rows.map((r) => cellValues(r.cells[i]));
      const blank = (v) => !v || /^[—–-]$/.test(v); // "—" means no value
      const flat = values.map((v) => v.join(' ')).filter((v) => !blank(v));
      const real = values.flat().filter((v) => !blank(v));
      const distinctReal = new Set(real).size;
      const numbers = flat.filter((v) => parseNumber(v) !== null).length;
      const dates = flat.filter((v) => parseDate(v) !== null).length;
      const type = flat.length && numbers / flat.length >= 0.8 ? 'number'
        : flat.length && dates / flat.length >= 0.8 ? 'date' : 'text';
      const distinct = [...new Set(values.flat().filter(Boolean))].sort((a, b) => a.localeCompare(b));
      const money = type === 'number' && flat.filter(isMoney).length / Math.max(flat.length, 1) >= 0.6
        && !/^(#|id|no\.?|ref)/i.test(label);
      return {
        index: i, label, type, money, withR: flat.some((v) => /^R/i.test(v)),
        multi: values.some((v) => v.length > 1),
        // A drop-down for columns with a short list of values. A long table may have
        // more (e.g. 40 consultants across 2,000 members) as long as values repeat.
        // Blanks don't count as repeats, so unique references never get a list.
        options: label && type === 'text' && distinct.length >= 2 && distinctReal >= 1
          && ((distinct.length <= 25 && distinctReal < real.length)
              || (distinct.length <= 80 && distinctReal * 2 <= real.length)) ? distinct : null,
      };
    });
    const data = rows.map((row, order) => ({
      row, order,
      values: columns.map((c) => cellValues(row.cells[c.index])),
      sortKeys: columns.map((c) => {
        const cell = row.cells[c.index];
        const raw = cell && cell.dataset.sort !== undefined ? cell.dataset.sort : cellValues(cell).join(' ');
        if (c.type === 'number') { const n = parseNumber(raw); return n === null ? -Infinity : n; }
        if (c.type === 'date') { const n = parseDate(raw); return n === null ? -Infinity : n; }
        return raw.toLowerCase();
      }),
      text: clean(row.textContent).toLowerCase(),
    }));

    const key = 'table-tools:' + location.pathname + ':' + (table.id || index);
    const saved = store(key) || {};
    const state = {
      q: saved.q || '',
      filters: saved.filters || {},
      sort: saved.sort || null,
      hidden: new Set(saved.hidden || []),
      size: PAGE_SIZES.includes(saved.size) ? saved.size : (rows.length > 100 ? 50 : 0),
      page: 1,
    };
    // A remembered filter whose value is no longer in the table would hide every row.
    Object.keys(state.filters).forEach((i) => {
      const c = columns[i];
      if (!c || !c.options || !c.options.includes(state.filters[i])) delete state.filters[i];
    });
    if (state.sort && !(columns[state.sort.col] && columns[state.sort.col].label)) state.sort = null;
    const save = () => store(key, {
      q: state.q, filters: state.filters, sort: state.sort, hidden: [...state.hidden], size: state.size,
    });

    // ── Toolbar ──────────────────────────────────────────────────────────────
    const search = el('input', { type: 'search', class: 'tt-search', placeholder: 'Filter rows…', 'aria-label': 'Filter rows' });
    search.value = state.q;
    const filterSelects = columns.filter((c) => c.options).map((c) => {
      const select = el('select', { class: 'tt-filter', 'aria-label': 'Filter by ' + c.label },
        [el('option', { value: '', text: c.label + ': all' }), ...c.options.map((o) => el('option', { value: o, text: o }))]);
      select.value = c.options.includes(state.filters[c.index]) ? state.filters[c.index] : '';
      select.addEventListener('change', () => {
        if (select.value) state.filters[c.index] = select.value; else delete state.filters[c.index];
        state.page = 1; save(); render();
      });
      return select;
    });

    const columnMenu = el('details', { class: 'tt-menu' }, [el('summary', { class: 'btn btn-ghost btn-sm', text: 'Columns' })]);
    const columnList = el('div', { class: 'tt-menu-panel' });
    columns.filter((c) => c.label).forEach((c) => {
      const box = el('input', { type: 'checkbox' });
      box.checked = !state.hidden.has(c.index);
      box.addEventListener('change', () => {
        if (box.checked) state.hidden.delete(c.index); else state.hidden.add(c.index);
        save(); render();
      });
      columnList.append(el('label', {}, [box, ' ' + c.label]));
    });
    columnMenu.append(columnList);

    const sizeSelect = el('select', { class: 'tt-size', 'aria-label': 'Rows per page' },
      PAGE_SIZES.map((n) => el('option', { value: String(n), text: n ? n + ' rows' : 'All rows' })));
    sizeSelect.value = String(state.size);
    sizeSelect.addEventListener('change', () => { state.size = Number(sizeSelect.value); state.page = 1; save(); render(); });

    const csvButton = el('button', { type: 'button', class: 'btn btn-ghost btn-sm', text: 'Export CSV' });
    const xlsxButton = el('button', { type: 'button', class: 'btn btn-ghost btn-sm', text: 'Export Excel' });
    const reset = el('button', { type: 'button', class: 'btn btn-ghost btn-sm tt-reset', text: 'Clear filters' });
    const count = el('span', { class: 'tt-count', 'aria-live': 'polite' });
    const pager = el('div', { class: 'tt-pager' });

    const toolbar = el('div', { class: 'tt-toolbar' }, [
      search, ...filterSelects, reset, el('span', { class: 'tt-spacer' }), columnMenu, sizeSelect, csvButton, xlsxButton,
    ]);
    const anchor = table.closest('.table-wrap, .table-card, .table-responsive') || table;
    anchor.before(toolbar);
    anchor.after(el('div', { class: 'tt-footer' }, [count, pager]));

    let timer = null;
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.q = search.value; state.page = 1; save(); render(); }, 150);
    });
    reset.addEventListener('click', () => {
      state.q = ''; search.value = ''; state.filters = {}; filterSelects.forEach((s) => { s.value = ''; });
      state.page = 1; save(); render();
    });

    // ── Sorting ──────────────────────────────────────────────────────────────
    columns.filter((c) => c.label).forEach((c) => {
      const th = headers[c.index];
      th.classList.add('tt-sortable');
      th.tabIndex = 0;
      th.title = 'Sort by ' + c.label;
      const toggle = () => {
        state.sort = state.sort && state.sort.col === c.index
          ? (state.sort.dir === 'asc' ? { col: c.index, dir: 'desc' } : null)
          : { col: c.index, dir: c.type === 'text' ? 'asc' : 'desc' };
        save(); render();
      };
      th.addEventListener('click', (e) => { if (!e.target.closest('a, button, input, select')) toggle(); });
      th.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    });

    // ── Totals ───────────────────────────────────────────────────────────────
    const moneyColumns = columns.filter((c) => c.money);
    let totalCells = null;
    if (moneyColumns.length && !table.tFoot) {
      const foot = table.createTFoot();
      const tr = foot.insertRow();
      tr.className = 'tt-totals';
      totalCells = headers.map(() => tr.insertCell());
    }

    const emptyRow = el('tr', { class: 'tt-empty' }, [el('td', { colspan: String(width), text: 'No rows match these filters.' })]);

    function matching() {
      const q = clean(state.q).toLowerCase();
      const terms = q ? q.split(' ') : [];
      let list = data.filter((d) => terms.every((t) => d.text.includes(t))
        && Object.entries(state.filters).every(([i, v]) => d.values[i].includes(v)));
      if (state.sort) {
        const { col, dir } = state.sort;
        const sign = dir === 'asc' ? 1 : -1;
        list = list.slice().sort((a, b) => {
          const x = a.sortKeys[col], y = b.sortKeys[col];
          const cmp = typeof x === 'string' ? x.localeCompare(y, undefined, { numeric: true }) : (x - y);
          return cmp * sign || a.order - b.order;
        });
      }
      return list;
    }

    function render() {
      const list = matching();
      const size = state.size || list.length || 1;
      const pages = Math.max(1, Math.ceil(list.length / size));
      state.page = Math.min(state.page, pages);
      const start = (state.page - 1) * size;
      const shown = new Set(list.slice(start, start + size));

      const frag = document.createDocumentFragment();
      list.forEach((d) => { d.row.hidden = !shown.has(d); frag.append(d.row); });
      data.filter((d) => !list.includes(d)).forEach((d) => { d.row.hidden = true; frag.append(d.row); });
      others.forEach((r) => frag.append(r));
      body.append(frag);
      if (!list.length) body.prepend(emptyRow); else emptyRow.remove();

      const hide = (cell, i) => { if (cell) cell.classList.toggle('tt-hidden', state.hidden.has(i)); };
      headers.forEach((th, i) => {
        hide(th, i);
        th.setAttribute('aria-sort', state.sort && state.sort.col === i ? (state.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
      });
      data.forEach((d) => [...d.row.cells].forEach(hide));

      if (totalCells) {
        totalCells.forEach((cell, i) => { cell.textContent = ''; hide(cell, i); });
        const firstVisible = headers.findIndex((_, i) => !state.hidden.has(i) && !columns[i].money);
        if (firstVisible >= 0) totalCells[firstVisible].textContent = 'Total (' + list.length + ')';
        moneyColumns.forEach((c) => {
          const sum = list.reduce((acc, d) => acc + (parseNumber(d.values[c.index].join(' ')) || 0), 0);
          totalCells[c.index].textContent = formatMoney(sum, c.withR);
        });
      }

      const filtered = list.length !== data.length;
      count.textContent = (list.length ? `Showing ${start + 1}–${start + shown.size} of ${list.length}` : 'Showing 0')
        + (filtered ? ` (filtered from ${data.length})` : '');
      reset.hidden = !state.q && !Object.keys(state.filters).length;
      pager.replaceChildren();
      if (pages > 1) {
        const prev = el('button', { type: 'button', class: 'btn btn-ghost btn-sm', text: '‹ Previous' });
        const next = el('button', { type: 'button', class: 'btn btn-ghost btn-sm', text: 'Next ›' });
        prev.disabled = state.page <= 1; next.disabled = state.page >= pages;
        prev.addEventListener('click', () => { state.page -= 1; render(); });
        next.addEventListener('click', () => { state.page += 1; render(); });
        pager.append(prev, el('span', { text: `Page ${state.page} of ${pages}` }), next);
      }
    }

    // ── Export ───────────────────────────────────────────────────────────────
    function exportRows() {
      const cols = columns.filter((c) => c.label && !state.hidden.has(c.index));
      return { cols, rows: matching().map((d) => cols.map((c) => d.values[c.index].join('; '))) };
    }
    function fileName(ext) {
      const base = clean(document.title.split('|')[0]).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'table';
      return `${base}-${new Date().toISOString().slice(0, 10)}.${ext}`;
    }
    async function recordExport(format, rowCount, cols) {
      const token = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch(LOG_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token ? token.content : '' },
        body: JSON.stringify({
          page: location.pathname + location.search, title: document.title, format,
          rows: rowCount, columns: cols.map((c) => c.label),
        }),
      });
      if (!response.ok || response.redirected) throw new Error('not recorded');
    }
    function download(blob, name) {
      const link = el('a', { href: URL.createObjectURL(blob), download: name });
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }
    async function runExport(format, button) {
      const { cols, rows: out } = exportRows();
      button.disabled = true;
      try {
        await recordExport(format, out.length, cols);
      } catch (_) {
        window.alert('The export could not be recorded, so nothing was downloaded. Please try again.');
        button.disabled = false;
        return;
      }
      if (format === 'csv') {
        const quote = (v) => (/[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v);
        const text = [cols.map((c) => c.label), ...out].map((r) => r.map(quote).join(',')).join('\r\n');
        download(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }), fileName('csv'));
      } else {
        download(buildXlsx(cols, out), fileName('xlsx'));
      }
      button.disabled = false;
    }
    csvButton.addEventListener('click', () => runExport('csv', csvButton));
    xlsxButton.addEventListener('click', () => runExport('xlsx', xlsxButton));

    render();
  }

  // ── Minimal .xlsx writer (one sheet, bold header, stored zip) ──────────────
  const xml = (s) => String(s).replace(/[<>&"]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]))
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '');
  function excelNumber(value) {
    const t = clean(value);
    if (!/^(R\s?)?-?[\d,]+(\.\d+)?%?$/i.test(t)) return null;
    const digits = t.replace(/\D/g, '');
    if (digits.length > 12 || (/^0\d/.test(digits) && !t.includes('.'))) return null; // IDs, phone numbers
    const n = Number(t.replace(/^R\s?/i, '').replace(/[,%]/g, ''));
    return Number.isFinite(n) ? n : null;
  }
  function columnName(i) {
    let s = ''; i += 1;
    while (i > 0) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); }
    return s;
  }
  function buildXlsx(cols, rows) {
    const sheetRows = [cols.map((c) => c.label), ...rows].map((r, ri) => {
      const cells = r.map((v, ci) => {
        const ref = columnName(ci) + (ri + 1);
        const n = ri > 0 ? excelNumber(v) : null;
        if (n !== null && /%$/.test(clean(v))) return `<c r="${ref}" s="2"><v>${n / 100}</v></c>`;
        if (n !== null) return `<c r="${ref}"><v>${n}</v></c>`;
        return `<c r="${ref}" t="inlineStr"${ri === 0 ? ' s="1"' : ''}><is><t xml:space="preserve">${xml(v)}</t></is></c>`;
      }).join('');
      return `<row r="${ri + 1}">${cells}</row>`;
    }).join('');
    const files = {
      '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
      '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      'xl/workbook.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Export" sheetId="1" r:id="rId1"/></sheets></workbook>',
      'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
      'xl/styles.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="9" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
      'xl/worksheets/sheet1.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>${sheetRows}</sheetData></worksheet>`,
    };
    return new Blob([zipStore(files)], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  }

  const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    return t;
  })();
  function crc32(bytes) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  function zipStore(files) {
    const enc = new TextEncoder();
    const parts = [], central = [];
    let offset = 0;
    for (const [name, content] of Object.entries(files)) {
      const nameBytes = enc.encode(name), data = enc.encode(content), crc = crc32(data);
      const local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
      local.setUint16(12, 33, true); // 1 Jan 1980: the zip format needs a valid date
      local.setUint32(14, crc, true); local.setUint32(18, data.length, true); local.setUint32(22, data.length, true);
      local.setUint16(26, nameBytes.length, true);
      parts.push(new Uint8Array(local.buffer), nameBytes, data);
      const dir = new DataView(new ArrayBuffer(46));
      dir.setUint32(0, 0x02014b50, true); dir.setUint16(4, 20, true); dir.setUint16(6, 20, true); dir.setUint16(8, 0x0800, true);
      dir.setUint16(14, 33, true);
      dir.setUint32(16, crc, true); dir.setUint32(20, data.length, true); dir.setUint32(24, data.length, true);
      dir.setUint16(28, nameBytes.length, true); dir.setUint32(42, offset, true);
      central.push(new Uint8Array(dir.buffer), nameBytes);
      offset += 30 + nameBytes.length + data.length;
    }
    const size = central.reduce((n, p) => n + p.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, Object.keys(files).length, true);
    end.setUint16(10, Object.keys(files).length, true); end.setUint32(12, size, true); end.setUint32(16, offset, true);
    return new Blob([...parts, ...central, new Uint8Array(end.buffer)]);
  }

  // ── Pinned headings: the rows scroll in their own box, the heading stays ──
  function pinHeader(table) {
    if (!table.tHead || table.parentElement.classList.contains('tt-scroll')) return;
    const box = el('div', { class: 'tt-scroll' });
    table.before(box);
    box.append(table);
    // Tables with more than one heading row stack each row under the one above.
    const offsets = () => {
      let top = 0;
      for (const row of table.tHead.rows) {
        for (const th of row.cells) th.style.top = top + 'px';
        top += row.getBoundingClientRect().height;
      }
    };
    if (table.tHead.rows.length > 1) { offsets(); window.addEventListener('resize', offsets); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    let index = 0;
    document.querySelectorAll('table').forEach((table) => {
      if (table.dataset.tableTools === 'off' || table.closest('[data-table-tools="off"], .tt-enhanced table, dialog')) return;
      try {
        table.classList.add('tt-enhanced');
        enhance(table, index++);
      } catch (err) {
        if (window.console) console.warn('table tools skipped a table', err);
      }
      try {
        pinHeader(table); // after enhance(), so the toolbar sits outside the scrolling box
      } catch (err) {
        if (window.console) console.warn('table heading not pinned', err);
      }
    });
  });
})();
