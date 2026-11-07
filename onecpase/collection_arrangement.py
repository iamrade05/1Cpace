"""Read-back confirmation from saved PTP amounts, dates and approval evidence."""
from datetime import date


def arrangement_confirmation(db, member_id):
    from .applications import statement_months_analysed
    from .debicheck import normalise_debicheck_status
    from .ptp_grant import has_approved_mandate
    row = db.execute("""SELECT * FROM ptp_agreements WHERE member_id=?
        AND ptp_status NOT IN ('superseded','cancelled','canceled','rejected','declined')
        ORDER BY id DESC LIMIT 1""", (member_id,)).fetchone()
    if row is None:
        return None
    ptp = dict(row)
    statement_ready = statement_months_analysed(db, member_id)
    mandates = db.execute("""SELECT l.mandate_id,d.status FROM ptp_mandate_links l
        LEFT JOIN debicheck_mandates d ON d.id=l.mandate_id WHERE l.ptp_id=?""", (ptp['id'],)).fetchall()
    mandate_ready = all(m['mandate_id'] and normalise_debicheck_status(m['status']) == 'approved'
                        for m in mandates) if mandates else has_approved_mandate(db, member_id)
    approved = (ptp.get('grant_status') == 'granted' and statement_ready and mandate_ready
                and ptp.get('ptp_status') in ('pending', 'partially_paid', 'paid')
                and ptp.get('manager_approval_status') in ('not_required', 'approved')
                and (not float(ptp.get('upfront_amount') or 0) or ptp.get('upfront_verified')))
    saved = db.execute('SELECT due_date,amount FROM payment_plan_items WHERE ptp_id=? ORDER BY instalment_number,id', (ptp['id'],)).fetchall()
    schedule = [(str(r['due_date'])[:10], float(r['amount'])) for r in saved]
    if not schedule and ptp.get('payment_count') and ptp.get('first_collection_date'):
        from .collections_engine import repayment_schedule
        try:
            schedule = [(day, float(value)) for day, value in repayment_schedule(
                ptp['remaining_balance'], ptp['payment_count'], str(ptp['first_collection_date'])[:10])]
        except (ValueError, TypeError):
            schedule = []
    try:
        for day, _ in schedule:
            date.fromisoformat(day)
    except (ValueError, TypeError):
        schedule = []
    arrears = float(ptp.get('arrears_amount') or 0)
    discount = float(ptp.get('discount_amount') or 0)
    upfront = float(ptp.get('upfront_amount') or 0)
    balance = round(sum(amount for _, amount in schedule), 2) if schedule else float(ptp.get('remaining_balance') or 0)
    total = round(arrears-discount, 2)
    method = {'debicheck':'DebiCheck', 'cash':'cash', 'eft':'EFT', 'card':'card'}.get(ptp.get('payment_method'))
    method_text = f'The scheduled arrears repayments are by {method}. ' if method else ''
    outstanding = []
    if not statement_ready:
        outstanding.append('Two months of bank statements must be analysed.')
    if not mandate_ready:
        outstanding.append('The required DebiCheck mandate(s) must be approved.')
    if ptp.get('manager_approval_status') not in ('not_required', 'approved'):
        outstanding.append('Manager approval is required.')
    if upfront > 0 and not ptp.get('upfront_verified'):
        outstanding.append('The upfront payment must be received and verified.')
    proposal_say = None
    if not approved and schedule and ptp.get('ptp_status') in ('pending', 'partially_paid'):
        first = date.fromisoformat(schedule[0][0]).strftime('%d %b %Y')
        last = date.fromisoformat(schedule[-1][0]).strftime('%d %b %Y')
        proposal_say = (f'Let me read back the payment proposal we have recorded. '
                        f'The original arrears are R{arrears:,.2f}, with a proposed discount of R{discount:,.2f}, '
                        f'leaving R{total:,.2f} to repay. The proposed upfront payment is R{upfront:,.2f}. '
                        f'The remaining arrears schedule covers R{balance:,.2f} in {len(schedule)} monthly payment(s), '
                        f'starting on {first}. ')
        if len(schedule) == 1:
            proposal_say += f'The arrears payment is R{schedule[0][1]:,.2f}. '
        else:
            proposal_say += (f'The regular arrears payment is R{schedule[0][1]:,.2f}, '
                             f'with a final arrears payment of R{schedule[-1][1]:,.2f} on {last}. ')
        proposal_say += (method_text + 'Any ongoing membership fees are separate. '
                         'These terms are conditional; the arrangement and any discount are not yet approved. '
                         + ' '.join(outstanding) + ' '
                         'This proposal does not itself restore gym access. '
                         'Do you agree to these proposed amounts and dates, and understand the outstanding conditions?')
    say = None
    if approved and schedule:
        first = date.fromisoformat(schedule[0][0]).strftime('%d %b %Y')
        last = date.fromisoformat(schedule[-1][0]).strftime('%d %b %Y')
        say = (f'Your arrangement has been approved. Your original arrears were R{arrears:,.2f}. '
               f'The agreed discount is R{discount:,.2f}, bringing the total arrears to repay to R{total:,.2f}. '
               f'After the agreed upfront payment of R{upfront:,.2f}, the arrears covered by this schedule are R{balance:,.2f}. '
               f'You will make {len(schedule)} monthly payment(s), starting on {first}. ')
        if len(schedule) == 1:
            say += f'The payment is R{schedule[0][1]:,.2f}. '
        elif schedule[0][1] == schedule[-1][1]:
            say += f'Each arrears payment is R{schedule[0][1]:,.2f}. '
        else:
            say += (f'The regular arrears payment is R{schedule[0][1]:,.2f}, '
                    f'with a final arrears payment of R{schedule[-1][1]:,.2f}. ')
        say += (method_text + f'If every agreed payment is received, these arrears should be cleared by {last}. '
                'This schedule covers the agreed arrears; any ongoing membership fees are separate. '
                'Can you confirm that you understand and agree with these payment amounts and dates?')
    return {'id':ptp['id'], 'approved':approved, 'statement_ready':statement_ready,
            'mandate_ready':mandate_ready, 'grant_status':ptp.get('grant_status') or 'not granted',
            'say':say, 'proposal_say':proposal_say, 'outstanding':outstanding,
            'schedule':[{'date':day,'amount':amount} for day,amount in schedule],
            'arrears':arrears, 'discount':discount, 'total':total, 'upfront':upfront, 'balance':balance}
