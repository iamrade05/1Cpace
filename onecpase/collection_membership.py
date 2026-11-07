"""Read membership/cancellation facts for an arrears conversation."""


def membership_payment_context(db, member_id):
    member = db.execute('SELECT member_status,monthly_installment FROM members WHERE id=?', (member_id,)).fetchone()
    cancelled = str(member['member_status'] or '').strip().casefold() in ('cancelled','canceled')
    request = db.execute("""SELECT id,reference,status FROM queries WHERE member_id=?
        AND category='cancellation' AND status NOT IN ('resolved','closed')
        ORDER BY id DESC LIMIT 1""", (member_id,)).fetchone()
    normal = 0.0 if cancelled else max(0, float(member['monthly_installment'] or 0))
    if cancelled:
        say = ('Your membership is recorded as cancelled. This proposal covers the recorded arrears and does not restart your membership. '
               'We must separately check that the normal membership debit order has been stopped; any arrears DebiCheck is a separate repayment arrangement.')
    else:
        say = ('We can discuss the arrears without asking you to return to training. '
               'Not attending or agreeing to repay does not cancel the membership. '
               f'The current calculation still includes the recorded normal membership instalment of R{normal:,.2f}, separately from the arrears repayment. '
               'If you want to end the membership, we must use the cancellation process and confirm its effective date and any final charges before promising that fees will stop.')
    if request and not cancelled:
        say += ' A cancellation request is already open. Check its current quote and progress before agreeing final settlement terms.'
    return {'cancelled':cancelled, 'normal_instalment':normal, 'say':say,
            'open_cancellation':dict(request) if request else None}
