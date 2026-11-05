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


def build_call_script(member, caller, last_visit, arrears, *, today=None):
    today = today or business_today()
    member = dict(member)
    caller = dict(caller) if caller else {}
    client_name = ' '.join(str(member.get(key) or '').strip() for key in ('first_name', 'last_name')).strip()
    caller_name = str(caller.get('full_name') or '').strip() or str(caller.get('username') or '').strip() or 'the reception team'
    visit_date = None
    raw = last_visit.get('last_visited') if last_visit else None
    try:
        if isinstance(raw, datetime):
            stamp = raw
        elif isinstance(raw, date):
            stamp = datetime.combine(raw, datetime.min.time())
        else:
            stamp = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(ZoneInfo('Africa/Johannesburg'))
        if stamp.date() <= today:
            visit_date = stamp.date()
    except (ValueError, TypeError):
        pass
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
    if owing:
        account = f'I can also see an overdue balance of {amount} on your account. Does this match your understanding of the account?'
        help_text = 'We would like to help you get back to training and find a more manageable way to pay your instalments. What amount could you comfortably afford, and which payment date would work best for you?'
        help_cue = 'Listen to what the client can afford. Check the existing payment-arrangement options below before confirming any terms.'
    else:
        account = 'Our records currently show no overdue balance on your account. Is there anything else we can help with so you can enjoy your training?'
        help_text = 'We would love to help you keep training with us. What support would make it easier for you to come to the gym?'
        help_cue = 'Agree on the next step with the client and record it in the call notes.'
    return {
        'client_name': client_name or 'Account holder', 'caller_name': caller_name,
        'visit_label': visit_label, 'overdue_label': amount,
        'has_arrears': owing > 0, 'paths': CONVERSATION_PATHS,
        'steps': [
            {'title': 'Greeting & introduction', 'say': greeting,
             'cue': 'Pause. Let the client confirm who they are and whether they can talk.'},
            {'title': 'Reason for the call', 'say': reason,
             'cue': 'Pause and listen. Let the client explain before moving to the account discussion.'},
            {'title': 'Discuss the account', 'say': account,
             'cue': 'Pause and listen. Ask whether the recorded balance matches the client’s understanding.'},
            {'title': 'Understand the client’s plans',
             'say': 'Before we discuss payment options, would you like to return to training with us, or would you prefer to focus on settling the current account?' if owing else help_text,
             'cue': 'Use the response the client has already given. Ask about their plans only if they are unclear, then select the matching conversation below.' if owing else help_cue,
             'choose_path': True},
            *([{'title': 'Negotiate a manageable payment',
                'say': f'Your current arrears are {amount}. What could you afford to pay upfront, and what could you manage each month towards the remaining arrears? Let me check which discount, if any, is available for that proposal.',
                'cue': 'Explore the figures first. A training decision is not a promise to pay. Confirm a payment commitment only after the client accepts the quoted terms.',
                'negotiate': True},
               {'title': 'Explain the approval conditions',
                'say': 'To approve this arrangement, we need you to send bank statements covering the last two months so we can prepare the DebiCheck. You will also need to authorise the required DebiCheck mandate. The proposed discount remains conditional until the statements have been received and checked, and DebiCheck approval has been confirmed. Any agreed upfront payment and other approval conditions must also be completed.',
                'cue': 'Pause and confirm the client understands these conditions. Upload the statements below and complete the required DebiCheck checks; saving or submitting a mandate is not approval.'},
               {'title': 'Confirm the approved arrangement',
                'say': 'Once the documents and DebiCheck are approved, I will confirm your agreed payment amounts, the number of instalments, the total arrears to repay and the expected date of your final payment.',
                'cue': 'Use the saved arrangement confirmation below. Read the approved terms back to the client and ask them to confirm their understanding. Until approval is complete, describe the figures as a proposal.'}] if owing else []),
        ],
    }
