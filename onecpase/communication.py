from html import escape


def collection_care_message(first_name: str, gym_name: str) -> str:
    """Human, Ubuntu-centred collections copy shared by WhatsApp and email."""
    member_name = (first_name or "there").strip()
    organisation = (gym_name or "your gym").strip()
    return f"""Hi {member_name},

This is {organisation}. We noticed that we have not seen you at the gym lately, and we wanted to check in. We hope you are doing well. You are part of our gym community, and we genuinely care about your wellbeing.

If you are still interested in working out with us, you have two options:

1. Restart your membership application and begin again; or
2. Continue with your current Premium membership at the same monthly premium while we help you work through the outstanding arrears.

You do not have to figure this out alone. Simply speak to us, and we will listen and find the best way forward together.

Please reply to this message or contact our team when you are ready.

Ubuntu — we move forward together.
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
            We noticed that we have not seen you at the gym lately, and we wanted to check in.
            We hope you are doing well. You are part of our gym community, and we genuinely
            care about your wellbeing.
          </p>
          <p style="font-size:.92rem;margin:0 0 12px;">
            If you are still interested in working out with us, you have two options:
          </p>
          <div style="background:#f4f7f9;border-left:4px solid #b0842d;border-radius:8px;
                      padding:14px 16px;margin-bottom:10px;font-size:.9rem;">
            <strong>1. A fresh start</strong><br>
            Restart your membership application and begin again.
          </div>
          <div style="background:#f4f7f9;border-left:4px solid #0b7285;border-radius:8px;
                      padding:14px 16px;margin-bottom:18px;font-size:.9rem;">
            <strong>2. Continue your membership</strong><br>
            Continue with your current Premium membership at the same monthly premium while
            we help you work through the outstanding arrears.
          </div>
          <p style="font-size:.92rem;margin:0 0 14px;">
            You do not have to figure this out alone. Simply speak to us, and we will listen
            and find the best way forward together.
          </p>
          <p style="font-size:.92rem;margin:0 0 20px;">
            Please reply to this email or contact our team when you are ready.
          </p>
          <p style="color:#002e45;font-weight:700;margin:0;">Ubuntu — we move forward together.</p>
          <p style="color:#6b7280;font-size:.84rem;margin:5px 0 0;">{organisation}</p>
        </div>
      </div>
    </div>
    """
