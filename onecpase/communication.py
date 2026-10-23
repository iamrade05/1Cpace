from html import escape


def collection_care_message(first_name: str, gym_name: str) -> str:
    """Collections follow-up shared by WhatsApp and email."""
    member_name = (first_name or "there").strip()
    organisation = (gym_name or "your gym").strip()
    return f"""Hi {member_name},

This is {organisation}. We'd like to help you get back to training. Please reply to this message or contact our team so we can discuss your account and a suitable payment arrangement, and explain the next steps for returning to the gym.

We look forward to hearing from you.
{organisation}"""


def collection_care_email_subject(first_name: str, gym_name: str) -> str:
    member_name = (first_name or "there").strip()
    organisation = (gym_name or "Your gym").strip()
    return f"{organisation} — We miss seeing you, {member_name}"


def collection_care_email_html(first_name: str, gym_name: str) -> str:
    member_name = escape((first_name or "there").strip())
    organisation = escape((gym_name or "your gym").strip())
    return f"""
    <div style="background:#f5f7fa;padding:28px 14px;font-family:Arial,sans-serif;">
      <div style="max-width:560px;margin:auto;background:#fff;border:1px solid #e5e7eb;
                  border-radius:14px;overflow:hidden;box-shadow:0 8px 24px rgba(15,23,42,.07);">
        <div style="background:#002e45;padding:24px 30px;">
          <h1 style="color:#fff;margin:0;font-size:1.35rem;font-weight:800;">{organisation}</h1>
          <p style="color:#b8d4df;margin:6px 0 0;font-size:.84rem;">A caring check-in from your gym community</p>
        </div>
        <div style="padding:30px;color:#374151;line-height:1.65;">
          <p style="font-size:1rem;margin:0 0 14px;">Hi <strong>{member_name}</strong>,</p>
          <p style="font-size:.92rem;margin:0 0 16px;">
            This is {organisation}. We'd like to help you get back to training.
            Please reply to this email or contact our team so we can discuss your account
            and a suitable payment arrangement, and explain the next steps for returning to the gym.
          </p>
          <p style="font-size:.92rem;margin:0 0 20px;">
            We look forward to hearing from you.
          </p>
          <p style="color:#6b7280;font-size:.84rem;margin:5px 0 0;">{organisation}</p>
        </div>
      </div>
    </div>
    """


def _rand(value) -> str:
    return f"R{float(value or 0):,.2f}".replace(",", " ")


def collection_offer_message(first_name, gym_name, arrears, total_to_collect, upfront=0,
                             monthly_debit=0, instalments=0, short=False) -> str:
    """WhatsApp (full) and SMS (short) wording for a Promise-to-Pay offer."""
    name = (first_name or "there").strip()
    gym = (gym_name or "your gym").strip()
    arrears = float(arrears or 0)
    total = float(total_to_collect or 0)
    saving = max(round(arrears - total, 2), 0)
    if short:
        text = (
            f"{gym}: Hi {name}, you owe {_rand(arrears)}. Pay {_rand(total)} instead and save {_rand(saving)}"
        )
        if upfront:
            text += f", starting with {_rand(upfront)} now"
        if monthly_debit and instalments:
            text += f", then {_rand(monthly_debit)} a month for {int(instalments)} months"
        return text + ". Reply to arrange."
    lines = [
        f"Hi {name},",
        "",
        f"This is {gym}. We would love to see you back at the gym, and we want to make it easier to clear your account.",
        "",
        f"Your outstanding balance is {_rand(arrears)}. We can reduce this to {_rand(total)}, a saving of {_rand(saving)}.",
    ]
    if upfront:
        lines.append(f"This starts with an upfront payment of {_rand(upfront)}.")
    if monthly_debit and instalments:
        lines.append(f"The rest is {_rand(monthly_debit)} a month for {int(instalments)} months, by debit order.")
    lines += ["", "If this amount is too much for you, tell us what you can afford and we will work something out together.",
              "", "Please reply to this message and we will set it up.", "", f"Ubuntu — we move forward together.\n{gym}"]
    return "\n".join(lines)
