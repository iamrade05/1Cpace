import base64
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from onecpase.applications import _background_itensity_push, registration_fee_paid
from onecpase.database import get_db
from onecpase.integrations import ItensityNotSubmitted, itensity_writes_allowed
from onecpase.itensity_followups import followup_status, reg_fee_to_send, run_followups
from tests.test_itensity import _fully_ready_application, _login, _ready_application

DRIVER_PATH = Path(__file__).resolve().parent.parent / "onecpase" / "itensity_driver" / "push_to_itensity.py"


def _driver():
    spec = importlib.util.spec_from_file_location("itensity_driver_under_test", DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pushed_member(app, fee_amount=200, method="card"):
    with app.app_context():
        db = get_db()
        application_id, member_id = _ready_application(db)
        db.execute("UPDATE membership_applications SET application_status='active', push_status='pushed' WHERE id=?",
                   (application_id,))
        db.execute("UPDATE members SET itensity_ref='1527701' WHERE id=?", (member_id,))
        if fee_amount:
            db.execute(
                """INSERT INTO collections (member_id, amount_paid, outstanding_balance, collection_date, method, status, payment_purpose)
                   VALUES (?, ?, 0, '2026-10-30', ?, 'paid', 'registration_fee')""",
                (member_id, fee_amount, method),
            )
        folder = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id)
        folder.mkdir(parents=True)
        (folder / "test.pdf").write_bytes(b"%PDF-1.4 test")
        db.commit()
        return application_id, member_id, db.execute("PRAGMA database_list").fetchone()["file"]


def _sent(ref, surname, documents):
    return [{"document_id": doc["document_id"], "status": "sent", "error": None} for doc in documents]


# ── The off switch ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("setting,application,expected", [
    ("", 2148, False), ("2148", 2148, True), ("2148", 2149, False),
    ("2148, 2150", 2150, True), ("all", 9, True), ("ALL", 9, True),
])
def test_writes_are_allowed_only_when_listed(app, setting, application, expected):
    app.config["ITENSITY_PUSH_ALLOWED"] = setting
    with app.app_context():
        assert itensity_writes_allowed(application) is expected


def test_default_config_writes_to_nothing():
    from onecpase.config import Config
    assert Config.ITENSITY_PUSH_ALLOWED == ""
    assert Config.ITENSITY_AUTO_FOLLOWUPS is False


def test_push_route_refuses_when_switched_off(app):
    app.config["ITENSITY_PUSH_ALLOWED"] = ""
    with app.app_context():
        application_id, _ = _ready_application(get_db())

    with app.test_client() as client:
        _login(client)
        with patch("onecpase.applications.threading") as threading_mock:
            response = client.post(f"/applications/{application_id}/push")

    assert response.status_code == 302
    assert not threading_mock.Thread.called
    with app.app_context():
        row = get_db().execute("SELECT push_status FROM membership_applications WHERE id=?", (application_id,)).fetchone()
        assert row["push_status"] == "not_pushed"


def test_only_the_listed_application_can_push(app):
    with app.app_context():
        allowed_id, _ = _ready_application(get_db())
    app.config["ITENSITY_PUSH_ALLOWED"] = str(allowed_id + 100)
    with app.test_client() as client:
        _login(client)
        with patch("onecpase.applications.threading") as threading_mock:
            client.post(f"/applications/{allowed_id}/push")
    assert not threading_mock.Thread.called


# ── Push outcomes ───────────────────────────────────────────────────────────


def test_failure_before_submit_can_be_retried(app):
    with app.app_context():
        application_id, member_id = _ready_application(get_db())
        db_path = get_db().execute("PRAGMA database_list").fetchone()["file"]

    with patch("onecpase.applications.push_member_to_itensity",
               return_value=(False, None, ItensityNotSubmitted("Itensity does not offer the tariff 'X'"))):
        _background_itensity_push(app, db_path, application_id, member_id, None, False, [], 1)

    with app.app_context():
        row = get_db().execute("SELECT push_status FROM membership_applications WHERE id=?", (application_id,)).fetchone()
        assert row["push_status"] == "push_failed"


def test_missing_driver_is_not_an_unknown_outcome(app):
    app.config["RADEPULSE_PATH"] = str(app.config["DATABASE_PATH"]) + "-no-such-dir"
    with app.app_context():
        from flask import g
        from onecpase.integrations import push_member_to_itensity
        g.tenant_slug = "elev8"
        _, _, error = push_member_to_itensity({"payment_type": "Debit Order"})
    assert isinstance(error, ItensityNotSubmitted)


def test_followups_do_not_run_unless_switched_on(app):
    with app.app_context():
        application_id, member_id = _ready_application(get_db())
        db_path = get_db().execute("PRAGMA database_list").fetchone()["file"]

    with patch("onecpase.applications.push_member_to_itensity", return_value=(True, "IT-99", None)), \
            patch("onecpase.itensity_followups.run_followups") as followups:
        _background_itensity_push(app, db_path, application_id, member_id, None, False, [], 1)
    assert not followups.called


def test_a_crashing_followup_never_undoes_the_member(app):
    app.config["ITENSITY_AUTO_FOLLOWUPS"] = True
    with app.app_context():
        application_id, member_id = _fully_ready_application(get_db())
        db_path = get_db().execute("PRAGMA database_list").fetchone()["file"]

    with patch("onecpase.applications.push_member_to_itensity", return_value=(True, "IT-99", None)), \
            patch("onecpase.itensity_followups.send_documents", side_effect=RuntimeError("browser died")):
        _background_itensity_push(app, db_path, application_id, member_id, None, False, [], 1)

    with app.app_context():
        row = get_db().execute("SELECT application_status,push_status FROM membership_applications WHERE id=?",
                               (application_id,)).fetchone()
        assert (row["application_status"], row["push_status"]) == ("active", "pushed")


# ── Documents ───────────────────────────────────────────────────────────────


def test_documents_are_sent_once_with_the_right_kind(app):
    application_id, member_id, db_path = _pushed_member(app)
    calls = []

    def attach(ref, surname, documents):
        calls.append(documents)
        return _sent(ref, surname, documents)

    with patch("onecpase.itensity_followups.attach_member_documents_to_itensity", side_effect=attach), \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        run_followups(app, db_path, application_id, member_id, 1, ("documents",))
        run_followups(app, db_path, application_id, member_id, 1, ("documents",))

    assert len(calls) == 1
    assert sorted(doc["kind"] for doc in calls[0]) == ["compliance", "compliance"]
    assert all("1C-" in doc["title"] for doc in calls[0])


def test_a_failed_document_is_retried_without_resending_the_others(app):
    application_id, member_id, db_path = _pushed_member(app)
    calls = []

    def attach(ref, surname, documents):
        calls.append([doc["document_id"] for doc in documents])
        results = _sent(ref, surname, documents)
        if len(calls) == 1:
            results[0].update(status="failed", error="Itensity was slow")
        return results

    with patch("onecpase.itensity_followups.attach_member_documents_to_itensity", side_effect=attach), \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        run_followups(app, db_path, application_id, member_id, 1, ("documents",))
        run_followups(app, db_path, application_id, member_id, 1, ("documents",))

    assert calls[1] == [calls[0][0]]


def test_a_missing_contract_is_reported_not_invented(app):
    application_id, member_id, db_path = _pushed_member(app)
    with patch("onecpase.itensity_followups.attach_member_documents_to_itensity", side_effect=_sent), \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        outcome = run_followups(app, db_path, application_id, member_id, 1, ("documents",))
    assert any("Signed Contract" in problem for problem in outcome["documents"]["problems"])


def test_every_signers_image_travels_inside_the_contract_pdf(app):
    application_id, member_id, db_path = _pushed_member(app)
    with app.app_context():
        db = get_db()
        folder = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id) / "signatures"
        folder.mkdir(parents=True)
        for role in ("member", "staff"):
            (folder / f"{role}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + role.encode())
            db.execute("INSERT INTO contract_signatures (member_id, signer_role, image_filename) VALUES (?, ?, ?)",
                       (member_id, role, f"{role}.png"))
        db.commit()
    rendered = []

    def fake_pdf(html, output):
        rendered.append(html)
        Path(output).write_bytes(b"%PDF-1.4 contract")

    contract = f'<img src="/members/{member_id}/signature/staff"><img src="/members/{member_id}/signature/witness">'
    with patch("onecpase.contracts.render_contract", return_value=contract), \
            patch("onecpase.itensity_followups.html_to_pdf", side_effect=fake_pdf), \
            patch("onecpase.itensity_followups.attach_member_documents_to_itensity", side_effect=_sent):
        run_followups(app, db_path, application_id, member_id, 1, ("documents",))

    expected = base64.b64encode(b"\x89PNG\r\n\x1a\nstaff").decode()
    assert f'src="data:image/png;base64,{expected}"' in rendered[0]
    # A role nobody signed stays as it was instead of breaking the page.
    assert f'src="/members/{member_id}/signature/witness"' in rendered[0]


def test_a_contract_signed_on_screen_becomes_a_pdf_with_the_signature(app):
    application_id, member_id, db_path = _pushed_member(app)
    signature = b"\x89PNG\r\n\x1a\n" + b"signature-bytes"
    with app.app_context():
        db = get_db()
        folder = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id) / "signatures"
        folder.mkdir(parents=True)
        (folder / "sig.png").write_bytes(signature)
        db.execute("INSERT INTO contract_signatures (member_id, signer_role, image_filename) VALUES (?, 'member', 'sig.png')",
                   (member_id,))
        db.commit()
    rendered, uploaded = [], []

    def fake_pdf(html, output):
        rendered.append(html)
        Path(output).write_bytes(b"%PDF-1.4 contract")

    def attach(ref, surname, documents):
        uploaded.extend(documents)
        return _sent(ref, surname, documents)

    with patch("onecpase.itensity_followups.html_to_pdf", side_effect=fake_pdf), \
            patch("onecpase.itensity_followups.attach_member_documents_to_itensity", side_effect=attach):
        outcome = run_followups(app, db_path, application_id, member_id, 1, ("documents",))

    assert "error" not in outcome and outcome["documents"]["problems"] == []
    assert base64.b64encode(signature).decode() in rendered[0]
    assert [doc["kind"] for doc in uploaded].count("contract") == 1
    with app.app_context():
        row = get_db().execute(
            "SELECT original_name FROM member_documents WHERE member_id=? AND document_type='Signed Contract'",
            (member_id,),
        ).fetchone()
        assert row["original_name"].endswith(".pdf")


def test_an_oversized_file_is_refused_before_any_browser_starts(app):
    application_id, member_id, db_path = _pushed_member(app)
    with app.app_context():
        path = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id) / "test.pdf"
        path.write_bytes(b"x" * (7 * 1024 * 1024 + 1))
    with patch("onecpase.itensity_followups.attach_member_documents_to_itensity") as attach, \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        run_followups(app, db_path, application_id, member_id, 1, ("documents",))
    assert not attach.called
    with app.app_context():
        rows = followup_status(get_db(), member_id)["documents"]
        assert rows and all(row["status"] == "failed" for row in rows)


def test_an_image_is_converted_to_pdf_before_upload(app):
    application_id, member_id, db_path = _pushed_member(app)
    with app.app_context():
        db = get_db()
        folder = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id)
        (folder / "id.jpg").write_bytes(b"not really a jpeg")
        db.execute("UPDATE member_documents SET stored_name='id.jpg', original_name='id.jpg' WHERE member_id=? AND document_type='SA ID Copy'",
                   (member_id,))
        db.commit()
    converted, uploaded = [], []

    def fake_pdf(html, output):
        converted.append(html)
        Path(output).write_bytes(b"%PDF-1.4 converted")

    def attach(ref, surname, documents):
        uploaded.extend(doc["path"] for doc in documents)
        return _sent(ref, surname, documents)

    with patch("onecpase.itensity_followups.html_to_pdf", side_effect=fake_pdf), \
            patch("onecpase.itensity_followups.attach_member_documents_to_itensity", side_effect=attach), \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        run_followups(app, db_path, application_id, member_id, 1, ("documents",))

    assert len(converted) == 1 and "data:image/jpeg;base64," in converted[0]
    assert all(path.endswith(".pdf") for path in uploaded)


# ── Reg fee ─────────────────────────────────────────────────────────────────


def test_reg_fee_is_never_sent_twice(app):
    application_id, member_id, db_path = _pushed_member(app)
    with patch("onecpase.itensity_followups.record_reg_fee_in_itensity",
               return_value={"status": "sent", "fee_detected": True, "error": None}) as record, \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        first = run_followups(app, db_path, application_id, member_id, 1, ("reg_fee",))
        second = run_followups(app, db_path, application_id, member_id, 1, ("reg_fee",))

    assert record.call_count == 1
    assert first["reg_fee"]["status"] == "sent"
    assert second["reg_fee"]["status"] == "sent"
    sent = record.call_args.args[2]
    assert (sent["amount"], sent["paid_on"], sent["payment_type"]) == (200.0, "2026-10-30", "Card")


def test_an_uncertain_payment_blocks_any_resend(app):
    application_id, member_id, db_path = _pushed_member(app)
    with patch("onecpase.itensity_followups.record_reg_fee_in_itensity",
               return_value={"status": "unknown", "fee_detected": False, "error": "Timed out"}) as record, \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        run_followups(app, db_path, application_id, member_id, 1, ("reg_fee",))
        again = run_followups(app, db_path, application_id, member_id, 1, ("reg_fee",))

    assert record.call_count == 1
    assert again["reg_fee"]["status"] == "unknown"


def test_a_confirmed_failure_can_be_retried(app):
    application_id, member_id, db_path = _pushed_member(app)
    results = [{"status": "failed", "fee_detected": False, "error": "Login failed"},
               {"status": "sent", "fee_detected": True, "error": None}]
    with patch("onecpase.itensity_followups.record_reg_fee_in_itensity", side_effect=results) as record, \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        run_followups(app, db_path, application_id, member_id, 1, ("reg_fee",))
        again = run_followups(app, db_path, application_id, member_id, 1, ("reg_fee",))
    assert record.call_count == 2
    assert again["reg_fee"]["status"] == "sent"


def test_admin_review_settles_an_uncertain_payment(app):
    application_id, member_id, db_path = _pushed_member(app)
    with patch("onecpase.itensity_followups.record_reg_fee_in_itensity",
               return_value={"status": "unknown", "fee_detected": False, "error": "Timed out"}), \
            patch("onecpase.itensity_followups.bind_itensity_tenant"):
        run_followups(app, db_path, application_id, member_id, 1, ("reg_fee",))

    with app.test_client() as client:
        _login(client)
        assert client.post(f"/applications/{application_id}/itensity/reg-fee/review",
                           data={"outcome": "found"}).status_code == 400
        assert client.post(f"/applications/{application_id}/itensity/reg-fee/review",
                           data={"outcome": "found", "notes": "Seen on the statement"}).status_code == 302
        assert client.post(f"/applications/{application_id}/itensity/reg-fee/review",
                           data={"outcome": "found", "notes": "Again"}).status_code == 409

    with app.app_context():
        assert followup_status(get_db(), member_id)["reg_fee"]["status"] == "sent"


@pytest.mark.parametrize("amount,method,message", [
    (0, "card", "No paid registration fee"),
    (150, "card", "doesn't match"),
    (200, "snapscan", "no clear match"),
])
def test_reg_fee_is_refused_when_it_cannot_be_trusted(app, amount, method, message):
    app.config["REGISTRATION_FEE_AMOUNT"] = 200.0
    _, member_id, _ = _pushed_member(app, fee_amount=amount, method=method)
    with app.app_context():
        from flask import g
        g.tenant_slug = "elev8"
        fee, reason = reg_fee_to_send(get_db(), member_id)
    assert fee is None and message in reason


def test_two_fee_payments_stop_the_send(app):
    _, member_id, _ = _pushed_member(app)
    with app.app_context():
        db = get_db()
        db.execute("""INSERT INTO collections (member_id, amount_paid, outstanding_balance, collection_date, method, status, payment_purpose)
                      VALUES (?, 200, 0, '2026-10-31', 'cash', 'paid', 'registration_fee')""", (member_id,))
        db.commit()
        fee, reason = reg_fee_to_send(db, member_id)
    assert fee is None and "More than one" in reason


def test_the_set_fee_applies_only_to_the_itensity_gym_and_a_short_payment_fails(app):
    app.config["REGISTRATION_FEE_AMOUNT"] = 200.0
    _, member_id, _ = _pushed_member(app, fee_amount=150)
    with app.app_context():
        from flask import g
        db = get_db()
        g.tenant_slug = "other-gym"
        assert registration_fee_paid(db, member_id)
        g.tenant_slug = "elev8"
        assert not registration_fee_paid(db, member_id)
        db.execute("UPDATE collections SET amount_paid=200 WHERE member_id=?", (member_id,))
        assert registration_fee_paid(db, member_id)


# ── The application page ────────────────────────────────────────────────────


def test_application_page_shows_what_reached_itensity(app):
    application_id, _, _ = _pushed_member(app)
    with app.test_client() as client:
        _login(client)
        page = client.get(f"/applications/{application_id}").get_data(as_text=True)
    assert "Sent to Itensity" in page
    assert "Send Reg Fee" in page


# ── The driver's safety rules, without a browser ────────────────────────────


def _member(**changes):
    member = {
        "first_name": "Ada", "last_name": "Lovelace", "id_number": "9001010000080", "contact": "0821234567",
        "join_date": "2026-10-30", "tariff": "Premium 24 2026", "contract_duration": "24 months",
        "payment_type": "Debit Order", "sales_person": "Staff Member", "bank": "Capitec",
        "account_number": "123456", "account_type": "Savings", "debit_order_date": "25",
    }
    member.update(changes)
    return member


def test_driver_requires_every_field_a_debit_order_depends_on():
    driver = _driver()
    assert driver.validate_member(_member()) == []
    for field in ("join_date", "tariff", "sales_person", "debit_order_date", "bank"):
        assert driver.validate_member(_member(**{field: ""})), field
    assert driver.validate_member(_member(account_type="1"))
    assert driver.validate_member(_member(payment_type="Snapscan"))


def test_driver_maps_the_confirmed_tariff_only():
    driver = _driver()
    assert driver.TARIFF_LABEL_MAP["Premium 24 2026"] == "Premium 24 Month 2026"
    assert "Premium 36 2026" not in driver.TARIFF_LABEL_MAP


def test_driver_reads_ledger_amounts():
    driver = _driver()
    assert driver.ledger_amount("R -200.00  R 0.00") == -200.0
    assert driver.ledger_amount("R 1,200.50") == 1200.5
    assert driver.ledger_amount("") is None


def test_driver_recognises_a_reg_fee_already_on_the_account():
    driver = _driver()
    row = lambda text, amount: {"description": text, "type": "Once-Off Fee", "amount": amount}
    assert driver.reg_fee_rows([row("joining fee\nStaff", 200.0)], 200)
    assert driver.reg_fee_rows([row("card fee\nStaff", 200.0)], 200)
    assert driver.reg_fee_rows([row("Payment from POS - joining fee", -200.0)], 200)
    assert not driver.reg_fee_rows([row("Auto Pro-Rata\nStaff", 200.0)], 200)
    assert not driver.reg_fee_rows([row("card fee\nStaff", 100.0)], 200)


def test_driver_normalises_references():
    driver = _driver()
    assert driver.normalize_itensity_ref("EHF001234") == "1234"
    assert driver.normalize_itensity_ref("1527701") == "1527701"


def test_driver_keeps_no_member_details_on_disk(tmp_path, monkeypatch):
    driver = _driver()
    monkeypatch.setattr(driver, "DEBUG_DIR", tmp_path)

    class Page:
        url = "https://example.test/AddMembers.php?id=9"

        def evaluate(self, script):
            return [{"name": "txt_Responsible_Acc_NO", "filled": True, "required": True}]

    driver.save_debug_artifacts(Page(), "probe")
    saved = (tmp_path / "probe_fields.txt").read_text()
    assert "txt_Responsible_Acc_NO: filled (required)" in saved
    assert "id=9" not in saved
    assert [path.name for path in tmp_path.iterdir()] == ["probe_fields.txt"]
