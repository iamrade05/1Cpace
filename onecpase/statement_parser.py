"""
South African bank statement PDF analyser — Standard Bank aware.

Standard Bank format quirk: the description ("Salary") appears on its own line,
then "IB PAYMENT FROM" on the next, then the deposit + balance on the line after.
We use a 3-line look-ahead window and always take the FIRST large amount (deposit)
not the last (balance), because columns are: Payments | Deposits | Balance.
"""
import re
from collections import Counter
from pathlib import Path

try:
    import pdfplumber
    _PDF_OK = True
except ImportError:
    _PDF_OK = False

# SA rand amount: 7,500.00 / R 64.82 / 1 234.56
_AMT_RE = re.compile(r'(?:R\s*)?(\d{1,3}(?:[,\s]\d{3})*\.\d{2})')

# Salary / income keywords
_SALARY_KW = re.compile(r'\bsalary\b', re.IGNORECASE)

_INCOME_KW = re.compile(
    r'\b(payroll|remuneration|wages|stipend|pay\s*cr|'
    r'ib\s*payment\s*from|internet\s*trf|internet\s*transfer|'
    r'trf\s*credit|transfer\s*credit|'
    r'nsfas|sassa|uif|pension|annuity)\b',
    re.IGNORECASE,
)

# Declined / returned / stopped payments — risk signal
_RISK_KW = re.compile(
    r'\b(return(?:ed)?|unpaid|dishonour(?:ed)?|r/d|nco|'
    r'insufficient|insuff|pos\s*declin|stop\s*payment|'
    r'i/f|bounced?)\b',
    re.IGNORECASE,
)

_MONTH_MAP = {m: i+1 for i, m in enumerate(
    ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']
)}
_MONTH_RE = re.compile(r'\b(' + '|'.join(_MONTH_MAP) + r')\b', re.IGNORECASE)

# Secondary guard against malformed amounts. Balance extraction is primarily
# protected by requiring a dated transaction row; this cap is not used to infer
# the customer's account type.
_MAX_SANE_BALANCE = 200_000.0

_TRANSACTION_RE = re.compile(
    r'^\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{2}\b',
    re.IGNORECASE,
)


def _parse_amounts(line: str) -> list[float]:
    """Return all monetary amounts ≥ R10 found on a line."""
    out = []
    for m in _AMT_RE.findall(line):
        try:
            v = float(m.replace(',', '').replace(' ', ''))
            if v >= 10:
                out.append(v)
        except ValueError:
            pass
    return out


def _is_password_error(exc: BaseException) -> bool:
    """Return True when pdfminer/pdfplumber rejected an encrypted PDF password."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == 'PDFPasswordIncorrect':
            return True
        for arg in current.args:
            if isinstance(arg, BaseException) and _is_password_error(arg):
                return True
        current = current.__cause__ or current.__context__
    return False


def analyse_pdf(pdf_path: str, monthly_installment: float = 0.0, pdf_password: str | None = None) -> dict:
    """
    Analyse a PDF bank statement.

    Returns a dict with:
        income_detected, estimated_income, salary_payments, income_months,
        max_balance, avg_balance, closing_balance,
        returned_debits, months_seen,
        can_afford, rating, summary, error
    """
    result = {
        'income_detected':  False,
        'estimated_income': 0.0,
        'salary_payments':  [],
        'income_months':    0,
        'max_balance':      0.0,
        'avg_balance':      0.0,
        'closing_balance':  0.0,
        'returned_debits':  0,
        'months_seen':      0,
        'monthly_balances': {},
        'verification_qualified': False,
        'can_afford':       None,
        'rating':           'unknown',
        'summary':          '',
        'error':            None,
    }

    if not _PDF_OK:
        result['error'] = 'pdfplumber is not installed on this server.'
        return result

    path = Path(pdf_path)
    if not path.exists():
        result['error'] = 'File not found on disk.'
        return result

    if path.suffix.lower() != '.pdf':
        result['error'] = 'Analysis only supports PDF files.'
        return result

    # ── 1. Extract text ───────────────────────────────────────────────────────
    try:
        text = ''
        open_kwargs = {'password': pdf_password} if pdf_password is not None else {}
        with pdfplumber.open(str(path), **open_kwargs) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + '\n'
    except Exception as exc:
        if _is_password_error(exc):
            if pdf_password is None:
                result['error'] = (
                    'This PDF is password-protected. Enter the PDF password next to '
                    'the Analyse button and try again.'
                )
            else:
                result['error'] = 'The PDF password is incorrect. Check it and try again.'
        else:
            detail = str(exc).strip() or type(exc).__name__
            result['error'] = f'Could not read PDF: {detail}'
        return result

    if not text.strip():
        result['error'] = (
            'No text could be extracted. The statement is likely scanned (image-only). '
            'Please request a digital/e-statement PDF from the bank.'
        )
        return result

    # ── 2. Line scan ──────────────────────────────────────────────────────────
    lines = [ln.strip() for ln in text.splitlines()]

    salary_amounts:  list[float] = []  # confirmed salary credits
    income_amounts:  list[float] = []  # other income credits
    balance_amounts: list[float] = []  # running balance column values
    monthly_balances: dict[int, list[float]] = {}
    risk_lines_seen: set[int]    = set()
    months_seen:     set[int]    = set()

    # State machine: after seeing "Salary" label, look ahead for the deposit amount
    awaiting_salary_amount = 0   # countdown (lines remaining to find the amount)

    for i, ln in enumerate(lines):
        if not ln:
            continue

        # Month detection
        for m in _MONTH_RE.findall(ln):
            months_seen.add(_MONTH_MAP[m.lower()])

        # Risk events (only count each unique occurrence once)
        if _RISK_KW.search(ln):
            # Exclude pure-fee header lines that are just charge notifications
            is_pure_fee = bool(re.match(r'^FEE[:\-\s]', ln, re.IGNORECASE))
            has_decline_keyword = bool(re.search(
                r'declined?|insuff|stop\s*pay|return(?:ed)?|dishon', ln, re.IGNORECASE
            ))
            if not is_pure_fee or has_decline_keyword:
                risk_lines_seen.add(i)

        amts = _parse_amounts(ln)
        if not amts:
            # Still check for "Salary" keyword even on amount-less lines
            if _SALARY_KW.search(ln):
                awaiting_salary_amount = 4
            continue

        # ── Salary look-ahead ─────────────────────────────────────────────────
        if _SALARY_KW.search(ln):
            # Salary label and amount might be on the same line
            big = [a for a in amts if a >= 500]
            if big:
                # First large amount = the deposit; last = balance
                salary_amounts.append(big[0])
            else:
                awaiting_salary_amount = 4
        elif awaiting_salary_amount > 0:
            awaiting_salary_amount -= 1
            big = [a for a in amts if a >= 500]
            if big:
                # Take the FIRST large amount (deposit column), not the max (which is balance)
                salary_amounts.append(big[0])
                awaiting_salary_amount = 0

        # ── Other income lines ────────────────────────────────────────────────
        elif _INCOME_KW.search(ln):
            big = [a for a in amts if a >= 500]
            if big:
                income_amounts.append(big[0])

        # ── Balance column tracking ───────────────────────────────────────────
        # Standard Bank: the final amount on a DATED transaction row is the
        # running balance. Requiring the date prevents page headers, account
        # numbers and legal/footer text from becoming fake balances.
        is_tx_line = bool(_TRANSACTION_RE.match(ln))
        if is_tx_line and amts[-1] < _MAX_SANE_BALANCE:
            balance_amounts.append(amts[-1])
            month_match = _MONTH_RE.search(ln)
            if month_match:
                month = _MONTH_MAP[month_match.group(1).lower()]
                monthly_balances.setdefault(month, []).append(amts[-1])

    # ── 3. Balance stats ──────────────────────────────────────────────────────
    sane_balances = [b for b in balance_amounts if b < _MAX_SANE_BALANCE]
    if sane_balances:
        result['max_balance']     = round(max(sane_balances), 2)
        upper_half                = sorted(sane_balances)[len(sane_balances)//2:]
        result['avg_balance']     = round(sum(upper_half)/len(upper_half), 2)
        result['closing_balance'] = round(sane_balances[-1], 2)

    # ── 4. Income detection ───────────────────────────────────────────────────
    if salary_amounts:
        result['income_detected']  = True
        result['salary_payments']  = sorted(salary_amounts, reverse=True)
        result['income_months']    = len(salary_amounts)
        # Most common salary bucket (round to nearest R500)
        bucketed = [round(a / 500) * 500 for a in salary_amounts]
        result['estimated_income'] = float(Counter(bucketed).most_common(1)[0][0])
    elif income_amounts:
        result['income_detected']  = True
        result['salary_payments']  = sorted(income_amounts, reverse=True)
        result['income_months']    = len(income_amounts)
        bucketed = [round(a / 500) * 500 for a in income_amounts]
        result['estimated_income'] = float(Counter(bucketed).most_common(1)[0][0])

    result['returned_debits'] = len(risk_lines_seen)
    result['months_seen']     = len(months_seen)
    result['monthly_balances'] = {
        str(month): round(min(values), 2)
        for month, values in monthly_balances.items()
        if values
    }

    # ── 5. Affordability verdict ──────────────────────────────────────────────
    income  = result['estimated_income']
    rd      = result['returned_debits']
    closing = result['closing_balance']
    mi      = monthly_installment

    if mi > 0 and income > 0:
        ratio = mi / income                         # e.g. 302.25 / 7500 = 0.0403

        if ratio <= 0.15:
            # Affordability is based on installment-to-income. Declined events
            # remain visible as separate cash-flow warnings, but do not turn an
            # otherwise affordable 4% installment into a false RISK verdict.
            result['rating']     = 'good'
            result['can_afford'] = True
        elif ratio <= 0.30:
            if rd <= 2:
                result['rating']     = 'moderate'
                result['can_afford'] = True
            else:
                result['rating']     = 'risk'
                result['can_afford'] = False
        else:
            result['rating']     = 'risk'
            result['can_afford'] = False

    elif income > 0:
        result['rating']     = 'moderate'
        result['can_afford'] = True

    balance_months = list(result['monthly_balances'].values())
    balance_qualified = (
        mi > 0 and bool(balance_months)
        and all(mi <= balance * 0.30 for balance in balance_months)
    )
    result['verification_qualified'] = (
        result['income_detected'] and result['income_months'] >= 2
    ) or balance_qualified

    # ── 6. Human-readable summary ─────────────────────────────────────────────
    parts = []

    if result['income_detected']:
        pmt_str = ', '.join(f"R{p:,.2f}" for p in result['salary_payments'][:4])
        parts.append(
            f"<strong>Salary detected: R{income:,.2f}/month</strong> "
            f"&mdash; {result['income_months']} payment(s) found: {pmt_str}."
        )
    else:
        parts.append("No clear salary/income credits detected in the statement.")

    if result['max_balance'] > 0:
        parts.append(
            f"Closing balance: <strong>R{closing:,.2f}</strong> &nbsp;|&nbsp; "
            f"Highest balance: R{result['max_balance']:,.2f}."
        )

    if rd > 0:
        parts.append(
            f"<span style='color:#ef4444;'>"
            f"⚠ {rd} declined/returned/stopped payment event(s) detected — review cash-flow.</span>"
        )
    else:
        parts.append("No declined or returned debits detected.")

    parts.append(
        f"Statement covers <strong>{result['months_seen']} month(s)</strong>."
    )

    if mi > 0:
        pct = (mi / income * 100) if income > 0 else 0
        mi_fmt = f"R{mi:,.2f}"
        r = result['rating']
        if r == 'good':
            parts.append(
                f"<strong style='color:#059669;'>✓ Installment of {mi_fmt} "
                f"({pct:.1f}% of income) — client can manage payments.</strong>"
            )
        elif r == 'moderate':
            parts.append(
                f"<strong style='color:#d97706;'>~ Installment of {mi_fmt} "
                f"({pct:.1f}% of income) — affordable but monitor cash flow.</strong>"
            )
        else:
            parts.append(
                f"<strong style='color:#ef4444;'>✗ Installment of {mi_fmt} "
                f"({pct:.1f}% of income) — financial strain likely, review carefully.</strong>"
            )

    result['summary'] = ' '.join(parts)
    return result
