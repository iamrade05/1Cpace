from onecpase.communication import (collection_care_email_html,
                                    collection_care_email_subject,
                                    collection_care_message)


def test_collection_message_is_personal_caring_and_actionable():
    message = collection_care_message("Lerato", "ELEV8 Health & Fitness")

    assert message.startswith("Hi Lerato,")
    assert "ELEV8 Health & Fitness" in message
    assert "get back to training" in message
    assert "suitable payment arrangement" in message
    assert "next steps for returning to the gym" in message
    assert "restart" not in message.lower()
    assert "same monthly premium" not in message



def test_collection_email_escapes_personalised_values():
    subject = collection_care_email_subject("Lerato", "ELEV8")
    html = collection_care_email_html("<Lerato>", "ELEV8 & Friends")

    assert subject == "ELEV8 — We miss seeing you, Lerato"
    assert "&lt;Lerato&gt;" in html
    assert "ELEV8 &amp; Friends" in html
    assert "suitable payment arrangement" in html
    assert "next steps for returning to the gym" in html
    assert "Restart your membership" not in html
    assert "same monthly premium" not in html
