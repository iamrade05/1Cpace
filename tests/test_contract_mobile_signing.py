"""A member reads and signs their contract on their own phone from the shared link."""
import base64
from pathlib import Path

from onecpase.database import get_db
from onecpase.encryption import decrypt, encrypt
from tests.test_contract_share_verification import (  # noqa: F401  (sent is a fixture)
    SIGNATURE, _act, _contracts_signed, _member, _share_link, _text, _token, sent,
)


def _verified(app, client, sent):
    path = _share_link(client, _member(app))
    _act(client, path, action="send_code")
    _act(client, path, action="verify_code", code=sent.code)
    return path


def _one(app, sql, *params):
    with app.app_context():
        return get_db().execute(sql, params).fetchone()


def test_signing_records_the_signature_and_the_device_it_came_from(app, client, sent):
    path = _verified(app, client, sent)

    response = client.post(path, data=dict(action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE),
                           headers={"User-Agent": "Phone Browser"})

    assert "Contract signed" in _text(response)
    token = _token(app)
    signature = _one(app, "SELECT * FROM contract_signatures WHERE id=?", token["signature_id"])
    assert token["status"] == "approved" and token["responded_at"]
    assert signature["signer_role"] == "member" and signature["signed_by"] is None
    assert token["signed_user_agent"] == "Phone Browser"
    assert token["signed_ip"]
    assert _contracts_signed(app) == 1


def test_a_copy_of_the_contract_as_signed_is_kept_for_staff(app, client, sent):
    path = _verified(app, client, sent)

    _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE)

    doc = _one(app, "SELECT * FROM member_documents WHERE document_type='Signed Contract'")
    assert doc["original_name"].endswith(".html") and doc["file_size"] > 0
    html = (Path(app.config["UPLOAD_FOLDER"]) / "members" / str(doc["member_id"]) / doc["stored_name"]).read_text(encoding="utf-8")
    assert SIGNATURE in html
    assert "Link Member" in html
    assert "<script" not in html and "csrf" not in html


def test_the_old_approve_button_cannot_sign_without_a_signature(app, client, sent):
    path = _verified(app, client, sent)

    response = _act(client, path, response="approved")

    assert response.status_code == 400
    assert _token(app)["status"] == "pending"
    assert _contracts_signed(app) == 0


def test_the_agreement_must_be_accepted_before_signing(app, client, sent):
    path = _verified(app, client, sent)

    response = _act(client, path, action="sign", read_to_end="1", image_data=SIGNATURE)

    assert response.status_code == 400
    assert "tick the box" in _text(response)
    assert _token(app)["status"] == "pending"


def test_the_agreement_must_be_scrolled_through_before_signing(app, client, sent):
    path = _verified(app, client, sent)

    response = _act(client, path, action="sign", accepted="1", image_data=SIGNATURE)

    assert response.status_code == 400
    assert "scroll through" in _text(response)
    assert _token(app)["status"] == "pending"


def _debit_member(app, **columns):
    member_id = _member(app)
    values = dict(payment_type="Debit Order", bank="Standard Bank", account_type="Cheque/Current",
                  account_number=encrypt("123456789"), branch_code=encrypt("051001"),
                  monthly_installment=599, debit_order_date="1", **columns)
    with app.app_context():
        db = get_db()
        db.execute(f"UPDATE members SET {', '.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), member_id))
        db.commit()
    return member_id


def _verified_debit(app, client, sent, **columns):
    path = _share_link(client, _debit_member(app, **columns))
    _act(client, path, action="send_code")
    _act(client, path, action="verify_code", code=sent.code)
    return path


SIGNED = dict(action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE)
AUTHORISED = dict(debicheck_accepted="1", debit_signature_data=SIGNATURE)


def test_a_debit_order_member_sees_their_banking_details_masked_before_signing(app, client, sent):
    page = _text(client.get(_verified_debit(app, client, sent)))

    assert "Standard Bank" in page and "051001" in page
    assert "123****789" in page and "123456789" not in page
    assert "debicheck_accepted" in page


def test_a_debit_order_needs_the_debicheck_authorisation(app, client, sent):
    path = _verified_debit(app, client, sent)

    response = _act(client, path, **SIGNED)

    assert response.status_code == 400
    assert "authorise the DebiCheck" in _text(response)
    assert _token(app)["status"] == "pending"

    _act(client, path, **AUTHORISED, **SIGNED)

    token = _token(app)
    assert token["status"] == "approved"
    assert token["debicheck_accepted_at"] and token["read_to_end_at"]


def test_a_cash_member_is_not_asked_for_a_debicheck_authorisation(app, client, sent):
    path = _verified(app, client, sent)

    assert "debicheck_accepted" not in _text(client.get(path))
    _act(client, path, **SIGNED)

    assert _token(app)["status"] == "approved"
    assert _token(app)["debicheck_accepted_at"] is None


def test_a_debit_order_without_banking_details_cannot_be_signed(app, client, sent):
    path = _verified_debit(app, client, sent, payer_name="")
    with app.app_context():
        db = get_db()
        db.execute("UPDATE members SET account_number=NULL")
        db.commit()

    page = _text(client.get(path))
    response = _act(client, path, **AUTHORISED, **SIGNED)

    assert "not complete" in page and "contractSignForm" not in page
    assert response.status_code == 400
    assert _token(app)["status"] == "pending"


def test_a_payer_who_is_not_the_member_is_saved_encrypted(app, client, sent):
    path = _verified_debit(app, client, sent)

    for bad in (dict(payer_name="", payer_id_number="7507074800085"), dict(payer_name="Pay Er", payer_id_number="123")):
        response = _act(client, path, third_party_payer="1", **AUTHORISED, **bad, **SIGNED)
        assert response.status_code == 400
        assert "13-digit" in _text(response)

    _act(client, path, third_party_payer="1", payer_name="Pay Er", payer_id_number="7507074800085", **AUTHORISED, **SIGNED)

    member = _one(app, "SELECT payer_name, payer_id_number FROM members")
    assert member["payer_name"] == "Pay Er"
    assert "7507074800085" not in member["payer_id_number"]
    with app.app_context():
        assert decrypt(member["payer_id_number"]) == "7507074800085"
    doc = _one(app, "SELECT * FROM member_documents WHERE document_type='Signed Contract'")
    copy = (Path(app.config["UPLOAD_FOLDER"]) / "members" / str(doc["member_id"]) / doc["stored_name"]).read_text(encoding="utf-8")
    assert "Pay Er" in copy and "7507074800085" in copy


def test_a_blank_signature_is_refused(app, client, sent):
    path = _verified(app, client, sent)

    for image in ("", "data:image/png;base64,AAAA", "not an image"):
        response = _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=image)
        assert response.status_code == 400
        assert "draw your signature" in _text(response)
    assert _one(app, "SELECT COUNT(*) AS n FROM contract_signatures")["n"] == 0
    assert _token(app)["status"] == "pending"


def test_signing_needs_the_code_first(app, client):
    path = _share_link(client, _member(app))

    response = _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE)

    assert response.status_code == 403
    assert _one(app, "SELECT COUNT(*) AS n FROM contract_signatures")["n"] == 0


def test_a_signed_link_cannot_be_signed_again(app, client, sent):
    path = _verified(app, client, sent)
    _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE)

    response = _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE)

    assert response.status_code == 410
    assert _one(app, "SELECT COUNT(*) AS n FROM contract_signatures")["n"] == 1


def test_the_link_shows_a_member_no_way_into_staff_pages(app, client, sent):
    path = _verified(app, client, sent)

    page = _text(client.get(path))

    assert "Back to Member" not in page
    assert "/members/" not in page
    assert "js/contract-sign.js" in page
    assert 'name="viewport"' in page


def test_staff_can_see_a_contract_signed_from_the_link(app, client, sent):
    path = _verified(app, client, sent)
    _act(client, path, action="sign", read_to_end="1", accepted="1", image_data=SIGNATURE)
    member_id = _token(app)["member_id"]

    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": "admin"})
    page = _text(client.get(f"/contracts/{member_id}"))

    assert "Contract signed" in page
    assert "signed on screen" in page
    assert "signed copy attached" in page
    assert client.get(f"/contracts/{member_id}/signature/member").status_code == 200


def test_the_account_holder_must_sign_the_banking_details(app, client, sent):
    path = _verified_debit(app, client, sent)

    for image in ("", "data:image/png;base64,AAAA"):
        response = _act(client, path, debicheck_accepted="1", debit_signature_data=image, **SIGNED)
        assert response.status_code == 400
        assert "banking details box" in _text(response)
    assert _one(app, "SELECT COUNT(*) AS n FROM contract_signatures")["n"] == 0
    assert _token(app)["status"] == "pending"


def test_the_debit_authorisation_is_kept_as_its_own_signature(app, client, sent):
    path = _verified_debit(app, client, sent)
    debit = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"1" * 400).decode()

    _act(client, path, debicheck_accepted="1", debit_signature_data=debit, **SIGNED)

    token = _token(app)
    member_sig = _one(app, "SELECT * FROM contract_signatures WHERE id=?", token["signature_id"])
    debit_sig = _one(app, "SELECT * FROM contract_signatures WHERE id=?", token["debit_signature_id"])
    assert member_sig["signer_role"] == "member"
    assert debit_sig["signer_role"] == "debit_authoriser"
    assert member_sig["image_filename"] != debit_sig["image_filename"]
    folder = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(token["member_id"]) / "signatures"
    assert (folder / debit_sig["image_filename"]).read_bytes() == b"\x89PNG" + b"1" * 400
    doc = _one(app, "SELECT * FROM member_documents WHERE document_type='Signed Contract'")
    copy = (Path(app.config["UPLOAD_FOLDER"]) / "members" / str(doc["member_id"]) / doc["stored_name"]).read_text(encoding="utf-8")
    assert debit in copy and SIGNATURE in copy
    assert "authorising the debit order" in copy


def test_the_payer_is_named_on_their_debit_signature_and_staff_can_see_it(app, client, sent):
    path = _verified_debit(app, client, sent)

    _act(client, path, third_party_payer="1", payer_name="Pay Er", payer_id_number="7507074800085", **AUTHORISED, **SIGNED)

    member_id = _token(app)["member_id"]
    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": "admin"})
    page = _text(client.get(f"/contracts/{member_id}"))
    assert "/signature/debit_authoriser" in page
    assert "authorising the debit order" in page
    assert client.get(f"/contracts/{member_id}/signature/debit_authoriser").status_code == 200


def test_a_cash_member_signs_once(app, client, sent):
    path = _verified(app, client, sent)

    assert "contractDebitPad" not in _text(client.get(path))
    _act(client, path, **SIGNED)

    assert _token(app)["status"] == "approved"
    assert _token(app)["debit_signature_id"] is None
