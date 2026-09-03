from onecpase.communication import (collection_care_email_html,
                                    collection_care_email_subject,
                                    collection_care_message)


def test_collection_message_is_personal_caring_and_actionable():
    message = collection_care_message("Lerato", "ELEV8 Health & Fitness")

    assert message.startswith("Hi Lerato,")
    assert "have not seen you at the gym lately" in message
    assert "genuinely care about your wellbeing" in message
    assert "1. Restart your membership application" in message
    assert "2. Continue with your current Premium membership" in message
    assert "same monthly premium" in message
    assert "outstanding arrears" in message
    assert "Simply speak to us" in message
    assert "Ubuntu" in message


def test_collection_email_escapes_personalised_values():
    subject = collection_care_email_subject("Lerato", "ELEV8")
    html = collection_care_email_html("<Lerato>", "ELEV8 & Friends")

    assert subject == "ELEV8 — We miss seeing you, Lerato"
    assert "&lt;Lerato&gt;" in html
    assert "ELEV8 &amp; Friends" in html
    assert "A fresh start" in html
    assert "Continue your membership" in html
