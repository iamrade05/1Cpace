"""Approving a contract from the shared link needs a one-time code.

The link alone is only a bearer token - whoever holds it could approve. So before
the member can approve, a 6-digit code goes to the WhatsApp number (or, failing
that, the email) the gym already has on file, and only that code unlocks Approve.
"""
import base64
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from onecpase.database import get_db
from onecpase.extensions import mail

SIGNATURE = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"0" * 400).decode()


class _Sent:
    """Stands in for the WhatsApp sender and remembers what it was asked to send."""

    def __init__(self):
        self.messages = []

    def __call__(self, phone_id, to, text):
        self.messages.append((to, text))
        return {}

    @property
    def code(self):
        return re.search(r"\b(\d{6})\b", self.messages[-1][1]).group(1)


@pytest.fixture
def sent():
    fake = _Sent()
    with patch("onecpase.contracts.send_whatsapp_message", fake):
        yield fake


def _member(app, contact="0712345678", email=""):
    with app.app_context():
        db = get_db()
        member_id = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number,
                   contact, email, member_status, payment_type)
               VALUES ('ELE-0000000001', 'Link', 'Member', 'ITN-0000001', ?, ?, 'Active', 'EFT/Cash')""",
            (contact, email),
        ).lastrowid
        db.commit()
    return member_id


def _share_link(client, member_id):
    """The path a member is sent, made the way staff make it."""
    from test_staff_passwords import _make_admin
    _make_admin(client)
    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": "admin"})
    page = client.post(
        f"/contracts/{member_id}/share", data={"channel": "link"}, follow_redirects=True
    ).get_data(as_text=True)
    path = re.search(r"/contracts/share/[\w-]+/[\w-]+", page).group(0)
    with client.session_transaction() as sess:  # from here on this is the member's own device
        sess.clear()
    return path


def _act(client, path, **data):
    return client.post(path, data=data)


def _token(app):
    with app.app_context():
        return get_db().execute(
            "SELECT * FROM contract_share_tokens ORDER BY id DESC LIMIT 1"
        ).fetchone()


def _set_token(app, **columns):
    sets = ", ".join(f"{name} = ?" for name in columns)
    with app.app_context():
        db = get_db()
        db.execute(f"UPDATE contract_share_tokens SET {sets} WHERE id = ?",
                   (*columns.values(), _token(app)["id"]))
        db.commit()


def _contracts_signed(app):
    with app.app_context():
        return get_db().execute(
            "SELECT COUNT(*) FROM compliance_checklists WHERE contract_status = 'signed'"
        ).fetchone()[0]


def _text(response):
    return response.get_data(as_text=True)


def test_the_link_asks_for_a_code_before_it_offers_approval(app, client):
    path = _share_link(client, _member(app))

    page = _text(client.get(path))

    assert "Send me a code" in page
    assert "contractSignForm" not in page


def test_approving_without_a_verified_code_is_refused(app, client):
    path = _share_link(client, _member(app))

    response = _act(client, path, response="approved")

    assert response.status_code == 403
    assert "verify" in _text(response).lower()
    assert _token(app)["status"] == "pending"
    assert _contracts_signed(app) == 0


def test_the_code_goes_to_the_members_whatsapp_number_and_is_never_shown(app, client, sent):
    path = _share_link(client, _member(app))

    page = _text(_act(client, path, action="send_code"))

    assert len(sent.messages) == 1
    assert sent.messages[0][0] == "27712345678"        # international form of 0712345678
    assert "ending 5678" in page
    assert sent.code not in page


def test_the_right_code_unlocks_signing_and_signs_the_contract(app, client, sent):
    path = _share_link(client, _member(app))
    _act(client, path, action="send_code")

    _act(client, path, action="verify_code", code=sent.code)
    page = _text(client.get(path))
    assert "contractSignForm" in page

    _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE)

    assert _token(app)["status"] == "approved"
    assert _contracts_signed(app) == 1


def test_a_wrong_code_is_refused_and_five_of_them_lock_it(app, client, sent):
    path = _share_link(client, _member(app))
    _act(client, path, action="send_code")

    first = _text(_act(client, path, action="verify_code", code="000000"))
    assert "is not right" in first
    for _ in range(4):
        _act(client, path, action="verify_code", code="000000")
    locked = _text(_act(client, path, action="verify_code", code=sent.code))  # even the right one now

    assert "Too many wrong attempts" in locked
    assert _token(app)["verified_at"] is None

    # A fresh code starts again.
    _set_token(app, otp_sent_at="2000-01-01T00:00:00")
    _act(client, path, action="send_code")
    _act(client, path, action="verify_code", code=sent.code)
    assert _token(app)["verified_at"] is not None


def test_an_expired_code_is_refused(app, client, sent):
    path = _share_link(client, _member(app))
    _act(client, path, action="send_code")
    _set_token(app, otp_expires_at="2000-01-01T00:00:00")

    page = _text(_act(client, path, action="verify_code", code=sent.code))

    assert "expired" in page
    assert _token(app)["verified_at"] is None


def test_codes_cannot_be_requested_in_a_burst(app, client, sent):
    path = _share_link(client, _member(app))

    _act(client, path, action="send_code")
    page = _text(_act(client, path, action="send_code"))

    assert len(sent.messages) == 1
    assert "wait a minute" in page


def test_the_number_of_codes_per_link_is_capped(app, client, sent):
    path = _share_link(client, _member(app))

    for _ in range(6):
        _set_token(app, otp_sent_at="2000-01-01T00:00:00")
        page = _text(_act(client, path, action="send_code"))

    assert len(sent.messages) == 5
    assert "Too many codes" in page


def test_email_is_used_when_whatsapp_is_not_available(app, client):
    path = _share_link(client, _member(app, email="link.member@example.com"))

    with patch("onecpase.contracts.send_whatsapp_message", return_value={"error": "WhatsApp API not configured"}):
        with mail.record_messages() as outbox:
            page = _text(_act(client, path, action="send_code"))

    assert len(outbox) == 1
    assert outbox[0].recipients == ["link.member@example.com"]
    code = re.search(r"\b(\d{6})\b", outbox[0].body).group(1)
    assert "l***@example.com" in page
    assert code not in page
    _act(client, path, action="verify_code", code=code)
    assert _token(app)["verified_at"] is not None


def test_no_code_is_issued_when_it_cannot_be_delivered(app, client):
    path = _share_link(client, _member(app))

    with patch("onecpase.contracts.send_whatsapp_message", return_value={"error": "WhatsApp API not configured"}):
        page = _text(_act(client, path, action="send_code"))

    assert "could not send a code" in page
    assert _token(app)["otp_hash"] is None


def _approve(client, sent, path):
    _act(client, path, action="send_code")
    _act(client, path, action="verify_code", code=sent.code)
    return _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE)


def _shows_staff_navigation(page):
    return "Sign Out" in page or "Dashboard" in page


def test_the_confirmation_page_shows_a_member_no_staff_navigation(app, client, sent):
    path = _share_link(client, _member(app))

    page = _text(_approve(client, sent, path))

    assert "Contract signed" in page
    assert not _shows_staff_navigation(page)


def test_the_declined_page_shows_a_member_no_staff_navigation(app, client):
    path = _share_link(client, _member(app))

    page = _text(_act(client, path, response="declined"))

    assert "Contract declined" in page
    assert not _shows_staff_navigation(page)


def test_a_used_link_shows_a_member_no_staff_navigation(app, client, sent):
    path = _share_link(client, _member(app))
    _approve(client, sent, path)

    response = client.get(path)

    assert response.status_code == 410
    assert "unavailable" in _text(response)
    assert not _shows_staff_navigation(_text(response))


def test_declining_needs_no_code(app, client):
    path = _share_link(client, _member(app))

    _act(client, path, response="declined", note="Wrong amount")

    assert _token(app)["status"] == "declined"
    assert _contracts_signed(app) == 0


def _member_with_id(app, id_number="8001015009087"):
    from onecpase.encryption import encrypt
    member_id = _member(app, email="link.member@example.com")
    with app.app_context():
        db = get_db()
        db.execute(
            """UPDATE members SET id_number=?, payer_name='Payer', payer_id_number=?, branch_code=?,
                   street_name='Private Street', parq_q1=1 WHERE id=?""",
            (encrypt(id_number), encrypt("7507074800085"), encrypt("051001"), member_id),
        )
        db.commit()
    return member_id


def test_nothing_about_the_member_shows_until_the_id_digits_are_entered(app, client):
    path = _share_link(client, _member_with_id(app))

    before = _text(client.get(path))

    for private in ("8001015009087", "009087", "7507074800085", "051001", "Link Member",
                    "link.member@example.com", "Private Street", "heart condition", "7. Terms"):
        assert private not in before, private
    assert "last 6 digits of your ID number" in before

    after = _text(_act(client, path, action="verify_code", code="009087"))
    assert "8001015009087" in after
    assert "7507074800085" in after
    assert "Private Street" in after
    assert "7. Terms" in after
    assert "contractSignForm" in after


def test_parq_is_for_staff_and_never_on_the_contract_the_client_signs(app, client):
    from onecpase.database import default_sections_config
    sections = default_sections_config()
    for section in sections:
        if section["key"] == "parq":
            section["enabled"] = True
    member_id = _member_with_id(app)
    with app.app_context():
        db = get_db()
        assert db.execute("UPDATE contract_templates SET sections_config=?", (json.dumps(sections),)).rowcount
        db.commit()
    path = _share_link(client, member_id)

    opened = _text(_act(client, path, action="verify_code", code="009087"))
    assert "heart condition" not in opened

    _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE,
         debicheck_accepted="1")
    with app.app_context():
        doc = get_db().execute("SELECT * FROM member_documents WHERE document_type='Signed Contract'").fetchone()
    copy = (Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id) / doc["stored_name"]).read_text(encoding="utf-8")
    assert "heart condition" not in copy

    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": "admin"})
    assert "heart condition" in _text(client.get(f"/contracts/{member_id}"))


def test_a_member_with_a_real_id_is_never_sent_a_code(app, client, sent):
    path = _share_link(client, _member_with_id(app))

    page = _text(_act(client, path, action="send_code"))

    assert sent.messages == []
    assert "Send me a code" not in page
    assert _token(app)["otp_hash"] is None


def test_wrong_id_digits_are_refused_and_five_lock_the_link(app, client):
    path = _share_link(client, _member_with_id(app))

    first = _text(_act(client, path, action="verify_code", code="111111"))
    assert "not right" in first
    assert "8001015009087" not in first
    for _ in range(4):
        _act(client, path, action="verify_code", code="111111")
    locked = _text(_act(client, path, action="verify_code", code="009087"))  # even the right digits now

    assert "new link" in locked
    assert "8001015009087" not in locked
    assert _token(app)["verified_at"] is None


def test_a_new_link_from_the_gym_clears_the_lock(app, client):
    member_id = _member_with_id(app)
    path = _share_link(client, member_id)
    for _ in range(5):
        _act(client, path, action="verify_code", code="111111")

    fresh = _share_link(client, member_id)
    page = _text(_act(client, fresh, action="verify_code", code="009087"))

    assert "8001015009087" in page
    assert client.get(path).status_code == 410


def test_a_forwarded_link_still_asks_for_the_digits(app, client):
    path = _share_link(client, _member_with_id(app))
    _act(client, path, action="verify_code", code="009087")
    assert "8001015009087" in _text(client.get(path))

    with client.session_transaction() as sess:  # another person's phone
        sess.clear()
    page = _text(client.get(path))

    assert "8001015009087" not in page
    assert "last 6 digits of your ID number" in page
    assert _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE).status_code == 403


def test_a_passport_uses_its_last_six_characters(app, client, sent):
    path = _share_link(client, _member_with_id(app, "a12-345bc9"))

    page = _text(client.get(path))
    assert "last 6 characters of your passport number" in page
    assert "Send me a code" not in page
    assert "not right" in _text(_act(client, path, action="verify_code", code="111111"))

    opened = _text(_act(client, path, action="verify_code", code="345bc9"))     # either case

    assert "Private Street" in opened
    assert sent.messages == []


def test_an_imported_stand_in_number_is_not_used_so_a_code_is_sent(app, client, sent):
    path = _share_link(client, _member_with_id(app, "ITN-2167518"))

    assert "Send me a code" in _text(client.get(path))
    assert "Private Street" not in _text(_act(client, path, action="verify_code", code="167518"))
    _act(client, path, action="send_code")
    assert "Private Street" in _text(_act(client, path, action="verify_code", code=sent.code))
