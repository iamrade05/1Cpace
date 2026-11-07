"""Read-aloud reception prompts built from the open collection task's facts."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .reporting_cycle import today as business_today

CONVERSATION_PATHS = {
    'return': {
        'label': 'Wants to return / keep training',
        'say': 'We would like to support you in achieving your fitness goals. What would you like to work towards, and what is the biggest difficulty you are facing at the moment? If we can find a manageable way forward, would you like to return to training with us?',
        'cue': 'Listen for the client’s goal and their own decision to return. Record the support they need before discussing a payment offer.',
    },
    'moved': {
        'label': 'Has moved away',
        'say': 'Thank you for letting me know that you have moved. I understand that returning may not be practical. How can we work together to settle the current arrears in a way you can manage?',
        'cue': 'Focus on settling the existing account. Record any request to cancel separately; discussing settlement does not change the membership.',
    },
    'not_returning': {
        'label': 'Does not want to return',
        'say': 'Thank you for being clear about your decision. Let’s focus on how we can settle the current arrears. What would be a manageable way for you to deal with the outstanding amount?',
        'cue': 'Respect the client’s decision. Discuss settlement without asking them to commit to returning.',
    },
    'undecided': {
        'label': 'Unsure / needs more support',
        'say': 'I understand that you are not ready to decide yet. What is making the decision difficult, and what support would help? We can also discuss a manageable way to deal with the current arrears while you consider your training plans.',
        'cue': 'Record the struggle and support requested. Do not record a commitment to return unless the client confirms it.',
    },
}


def conversation_notes(form):
    """Record the client’s training decision alongside the existing call audit."""
    path = str(form.get('conversation_intention') or '').strip()
    if not path:
        return ''
    if path not in CONVERSATION_PATHS:
        raise ValueError('Select a valid training intention.')
    goal = str(form.get('client_goal') or '').strip()
    barrier = str(form.get('training_barrier') or '').strip()
    if len(goal) > 2000 or len(barrier) > 2000:
        raise ValueError('Keep the goal and training response to 2,000 characters each.')
    parts = ['Training intention: ' + CONVERSATION_PATHS[path]['label'] + '.']
    if barrier:
        parts.append('Client’s response / current struggle: ' + barrier)
    if goal and path in ('return', 'undecided'):
        parts.append('Fitness goal / support requested: ' + goal)
    return ' '.join(parts)


def _recorded_date(value, today):
    """Use confirmed local dates only; missing history is not proof of absence."""
    try:
        if isinstance(value, datetime):
            stamp = value
        elif isinstance(value, date):
            stamp = datetime.combine(value, datetime.min.time())
        else:
            stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(ZoneInfo('Africa/Johannesburg'))
        return stamp.date() if stamp.date() <= today else None
    except (ValueError, TypeError):
        return None


def build_return_guidance(member, arrears, access=None):
    """Explain repayment and return conditions without granting either."""
    from .collections_engine import reactivation_upfront_required, REACTIVATION_SAME_PREMIUM_PERCENT
    member = dict(member)
    access = access or {}
    owing = max(0, float(arrears.get('total_arrears') or 0))
    minimum = reactivation_upfront_required(owing, same_premium=True)
    conditions = []
    status = str(member.get('member_status') or '').strip()
    if status.casefold() != 'active':
        conditions.append(f'Membership status is {status or "not confirmed"}. Confirm the membership can be reactivated before promising entry.')
    if str(member.get('gym_access_status') or '').casefold() == 'blocked':
        conditions.append('The gym access record is blocked and must be reviewed separately.')
    if access.get('status') == 'INACTIVE':
        conditions.append('The collections rules require manager review or legal referral for this account.')
    elif access.get('access') == 'BLOCKED' and access.get('upfront_required'):
        conditions.append(f'The current collections access rule requires verified upfront payment of R{float(access.get("required_upfront_amount") or 0):,.2f}. This is an access-rule requirement, separate from the reactivation minimum below.')
    elif access.get('access') == 'BLOCKED' and access.get('debicheck_required'):
        conditions.append('The current collections access rule requires a qualifying approved DebiCheck arrangement or verified full settlement.')
    elif access.get('access') == 'ALLOWED':
        conditions.append('The current collections check permits access; membership status and the gym access record must also permit entry.')
    else:
        conditions.append('The collections access position has not been confirmed. Check it before promising entry.')
    say = 'We can agree a repayment proposal now. Returning to training is a separate check; an agreement to repay does not itself reactivate your membership or restore access.'
    if owing:
        say += (f' If reactivation is needed and you keep the same premium, the standard upfront requirement is '
                f'R{minimum:,.2f} ({REACTIVATION_SAME_PREMIUM_PERCENT}% of your current arrears). '
                'That payment must be received and verified, and the other return conditions must be satisfied before we confirm gym access.')
    return {'say':say, 'conditions':conditions, 'same_premium_minimum':minimum,
            'cue':'A lower upfront proposal may still be discussed for debt repayment; do not describe it as meeting the standard same-premium reactivation rule. Changed premiums and authorised campaigns have separate conditions that must be confirmed before quoting. Confirm access through the existing access workflow; this conversation changes no access state.'}


def build_call_script(member, caller, last_visit, arrears, *, today=None,
                      payment_stats=None, arrangements=(), queries=(), previous_calls=(), return_guidance=None,
                      membership_context=None):
    today = today or business_today()
    member = dict(member)
    caller = dict(caller) if caller else {}
    client_name = ' '.join(str(member.get(key) or '').strip() for key in ('first_name', 'last_name')).strip()
    caller_name = str(caller.get('full_name') or '').strip() or str(caller.get('username') or '').strip() or 'the reception team'
    visit_date = _recorded_date(dict(last_visit).get('last_visited') if last_visit else None, today)
    joined = _recorded_date(member.get('join_date'), today)
    payment_stats = payment_stats or {}
    paid_date = _recorded_date(payment_stats.get('last_payment_date'), today)
    paid_amount = payment_stats.get('last_payment_amount')
    payment_label = paid_date.strftime('%d %b %Y') if paid_date else 'No confirmed payment date'
    if paid_date and paid_amount is not None:
        payment_label = f'R{float(paid_amount):,.2f} on {payment_label}'
    visit_label = visit_date.strftime('%d %b %Y') if visit_date else 'No confirmed visit record'
    owing = max(0, float(arrears.get('total_arrears') or 0))
    amount = f'R{owing:,.2f}'
    greeting = f'Good day, am I speaking to {client_name}?' if client_name else 'Good day, may I speak to the account holder?'
    greeting += f' You’re speaking to {caller_name} from Elevate Health and Fitness. Is now a good time to talk?'
    if visit_date is None:
        reason = 'I’m calling to check in on your training. When were you last able to visit the gym, and is anything making it difficult to come in regularly?'
    elif (today - visit_date).days <= 14:
        reason = f'I’m calling to check how your training is going. I can see your last recorded gym visit was on {visit_label}. How has your training been, and is anything making it difficult to come in regularly?'
    else:
        reason = f'I’m calling because I can see your last recorded gym visit was on {visit_label}, and we’ve missed seeing you. What has been keeping you away from the gym?'
    if joined:
        reason = f'Our records show you joined us on {joined:%d %b %Y}. ' + reason
    if owing:
        account = f'I can also see an overdue balance of {amount} on your account. Does this match your understanding of the account?'
        help_text = 'We would like to help you get back to training and find a more manageable way to pay your instalments. What amount could you comfortably afford, and which payment date would work best for you?'
        help_cue = 'Listen to what the client can afford. Check the existing payment-arrangement options below before confirming any terms.'
    else:
        account = 'Our records currently show no overdue balance on your account. Is there anything else we can help with so you can enjoy your training?'
        help_text = 'We would love to help you keep training with us. What support would make it easier for you to come to the gym?'
        help_cue = 'Agree on the next step with the client and record it in the call notes.'
    if paid_date:
        account = f'The latest successful payment recorded is {payment_label}. ' + account
    else:
        account += ' When did you last make a payment? We can check any payment that is not reflected here.'
    context = []
    if str(member.get('member_status') or '').casefold() == 'blocked':
        context.append({'say': 'Your profile currently shows your membership as blocked. We would like to check the membership and access conditions with you.',
                        'cue': 'Check the access conditions before promising an entry date. An arrangement alone does not restore access.'})
    open_queries = [dict(q) for q in queries if str(q['status'] or '').casefold() not in ('resolved', 'closed')]
    if open_queries:
        context.append({'say': 'I can see an unresolved query on your profile. Has that been addressed, or is it still affecting your account or training?',
                        'cue': 'Review the query with the client before discussing a new offer. Record disputed payments for reconciliation.'})
    live_plans = [dict(p) for p in arrangements if str(p['ptp_status'] or '').casefold() in ('pending', 'partially_paid')]
    if live_plans and owing:
        context.append({'say': 'There is already a payment arrangement recorded on your profile. Is that arrangement still manageable, and have you made any payments that we need to check?',
                        'cue': 'Review its saved schedule and approval status first. Do not replace it or describe a payment as missed without verifying the ledger.'})
    calls = [dict(c) for c in previous_calls]
    if calls and str(calls[0].get('outcome') or '') == 'asked_callback':
        context.append({'say': 'You previously asked us to call back. Is this a better time to talk through your training and account?',
                        'cue': 'Check the previous call notes and avoid making the client repeat information already supplied.'})
    monthly = max(0, float(member.get('monthly_installment') or 0))
    fee_cue = (f'The recorded normal monthly membership fee is R{monthly:,.2f}; explain it separately from arrears repayments.'
               if monthly else 'Confirm the current membership fee before quoting a combined monthly payment.')
    return {
        'client_name': client_name or 'Account holder', 'caller_name': caller_name,
        'visit_label': visit_label, 'overdue_label': amount,
        'joined_label': joined.strftime('%d %b %Y') if joined else 'Membership start not confirmed',
        'payment_label': payment_label, 'context': context,
        'return_guidance': return_guidance or build_return_guidance(member, arrears),
        'membership_context': membership_context,
        'has_arrears': owing > 0, 'paths': CONVERSATION_PATHS,
        'steps': [
            {'title': 'Greeting & introduction', 'say': greeting,
             'cue': 'Pause. Let the client confirm who they are and whether they can talk.'},
            {'title': 'Reason for the call', 'say': reason,
             'cue': 'Pause and listen. Let the client explain before moving to the account discussion. The membership start date is not proof of the first gym visit.',
             'profile_context': True},
            {'title': 'Discuss the account', 'say': account,
             'cue': 'Pause and listen. Ask whether the recorded balance matches the client’s understanding.'},
            {'title': 'Understand the client’s plans',
             'say': 'Before we discuss payment options, would you like to return to training with us, or would you prefer to focus on settling the current account?' if owing else help_text,
             'cue': 'Use the response the client has already given. Ask about their plans only if they are unclear, then select the matching conversation below.' if owing else help_cue,
             'choose_path': True},
            *([{'title': 'Negotiate a manageable payment',
                'say': f'Your current arrears are {amount}. What could you afford to pay upfront, and what could you manage each month towards the remaining arrears? Let me check which discount, if any, is available for that proposal.',
                'cue': 'Explore the figures first. A training decision is not a promise to pay. Confirm a payment commitment only after the client accepts the quoted terms. ' + fee_cue,
                'negotiate': True},
               {'title': 'Explain the approval conditions',
                'say': 'To approve this arrangement, we need you to send bank statements covering the last two months so we can prepare the DebiCheck. You will also need to authorise the required DebiCheck mandate. The proposed discount remains conditional until the statements have been received and checked, and DebiCheck approval has been confirmed. Any agreed upfront payment and other approval conditions must also be completed.',
                'cue': 'Pause and confirm the client understands these conditions. Upload the statements below and complete the required DebiCheck checks; saving or submitting a mandate is not approval.'},
               {'title': 'Read back and agree the proposed terms',
                'say': 'Let’s read back the proposed amounts, payment dates and any outstanding conditions now, while we are on the call. Do you agree to this proposal and understand what still needs to be completed before approval?',
                'cue': 'Use the proposal read-back in payment options before saving the call. Record the client’s actual response in the call notes. Agreement to a proposal is separate from approval, receiving payment and restoring gym access. Use approved wording only when the saved arrangement shows approval is complete.'},
               {'title': 'Agree the next step before ending the call',
                'say': 'Before we finish, let’s confirm the amount you can pay, the exact payment date and the payment method. If you are coming into the gym, what time can you come in? Can you confirm that you agree with the amounts and dates we have discussed?',
                'cue': 'Save the agreed figures and schedule while the client is on the call. “I will come today” needs an amount and a time. Read back any remaining approval conditions. If the client cannot agree, record the reason and agree an exact callback date and time; do not record a promise to pay.'}] if owing else []),
        ],
    }
