"""Cash and debt movement classification shared by operational reports.

The legacy ledger stores gross charges in outstanding_balance and both cash
and discount credits in amount_paid. A discount settles debt but is not cash.
All calculations round once to cents using decimal arithmetic.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation


def money(value):
    try:
        amount = Decimal(str(value or 0))
        return amount if amount.is_finite() else Decimal(0)
    except (ValueError, InvalidOperation):
        return Decimal(0)


def is_discount(row):
    for part in (row['notes'] or '').split('|'):
        key, separator, value = part.partition('=')
        if separator and key.strip().casefold() == 'type':
            return value.strip().casefold() == 'discount'
    return False


def cash_amount(row):
    if str(row['status'] or '').casefold() not in ('paid', 'partial') or is_discount(row):
        return Decimal(0)
    return money(row['amount_paid'])


def totals(rows):
    rows = list(rows)
    billed = cash = credits = Decimal(0)
    balances = defaultdict(Decimal)
    for row in rows:
        charge = money(row['outstanding_balance'])
        receipt = cash_amount(row)
        credit = money(row['amount_paid']) if is_discount(row) and str(row['status'] or '').casefold() in ('paid', 'partial') else Decimal(0)
        billed += charge
        cash += receipt
        credits += credit
        balances[row['member_id']] += charge - receipt - credit
    cents = lambda amount: float(amount.quantize(Decimal('0.01')))
    return dict(total_collected=cents(cash), total_billed=cents(billed),
                total_credits=cents(credits), total_records=len(rows),
                total_outstanding=cents(sum((max(value, Decimal(0)) for value in balances.values()), Decimal(0))))


def cash_received(db, start=None, end=None, user_id=None):
    sql, params = 'SELECT amount_paid,status,notes FROM active_collections WHERE 1=1', []
    if start is not None:
        sql += ' AND substr(collection_date,1,10)>=?'
        params.append(str(start))
    if end is not None:
        sql += ' AND substr(collection_date,1,10)<=?'
        params.append(str(end))
    if user_id is not None:
        sql += ' AND created_by=?'
        params.append(user_id)
    return float(sum((cash_amount(row) for row in db.execute(sql,params)), Decimal(0)).quantize(Decimal('0.01')))


def monthly_totals(rows):
    from .reporting_cycle import cycle_key
    from datetime import date
    groups = defaultdict(list)
    for row in rows:
        try:
            key = cycle_key(date.fromisoformat(str(row['collection_date'])[:10]))
        except (ValueError, TypeError):
            continue
        groups[key].append(row)
    result = []
    for month, entries in sorted(groups.items(), reverse=True):
        summary = totals(entries)
        result.append(dict(month=month, cnt=len(entries), collected=summary['total_collected'],
                           outstanding=summary['total_outstanding'], credits=summary['total_credits'],
                           paid_cnt=sum(r['status']=='paid' for r in entries),
                           partial_cnt=sum(r['status']=='partial' for r in entries),
                           failed_cnt=sum(r['status'] in ('pending','failed') for r in entries)))
    return result
