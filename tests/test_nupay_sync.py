"""Bring V8's DebiCheck mandate statuses in line with NuPay's Mandate Report in one pass.

Checking members one at a time logs in to NuPay and downloads the whole report each
time. The sync logs in once, reads the report once, and settles every mandate from
those rows. These tests use synthetic report rows - no browser and no NuPay.
"""
from unittest.mock import patch

from onecpase import nupay_driver
from onecpase.database import get_db
from onecpase.encryption import hash_for_lookup
from onecpase.nupay_sync import sync_from_report


def _row(client_ref="2160994-1234", status="Active", accepted="2026-08-01 10:00:00.000",
         contract="DCPRD0000001", account="1112223334", debtor_id="", **extra):
    row = {
        "client_reference": client_ref, "contract_reference": contract,
        "debtor_account_number": account, "status": status, "status_reason": "",
        "mandate_acceptance_date": accepted, "date_created": accepted[:10],
        "response_description": "ok", "debtor_id": debtor_id,
    }
    row.update(extra)
    return row


def _member(app, *, itensity_ref="EHF002160994", join_date="2026-01-01", first="Sam",
            id_number="9001015009087"):
    with app.app_context():
        db = get_db()
        member_id = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number, id_number_hash,
                   contact, member_status, itensity_ref, join_date)
               VALUES (?, ?, 'Sync', ?, ?, '0712345678', 'Active', ?, ?)""",
            (f"ELE-{first}", first, id_number, hash_for_lookup(id_number), itensity_ref, join_date),
        ).lastrowid
        db.commit()
    return member_id


def _mandate(app, member_id, *, client_ref1="2160994", status="submitted", account="1112223334"):
    with app.app_context():
        db = get_db()
        mandate_id = db.execute(
            """INSERT INTO debicheck_mandates (member_id, merchant_id, client_ref1, client_ref2,
                   account_name, account_type, account_number, branch_code, submit_date, status)
               VALUES (?, 'm1', ?, '1234', 'Sam Sync', '1', ?, '250655', '2026-08-01', ?)""",
            (member_id, client_ref1, account, status),
        ).lastrowid
        db.commit()
    return mandate_id


def _status(app, mandate_id):
    with app.app_context():
        return get_db().execute(
            "SELECT status FROM debicheck_mandates WHERE id = ?", (mandate_id,)
        ).fetchone()["status"]


def _mandate_count(app):
    with app.app_context():
        return get_db().execute("SELECT COUNT(*) FROM debicheck_mandates").fetchone()[0]


def _notes(app, member_id):
    with app.app_context():
        return [r["note"] for r in get_db().execute(
            "SELECT note FROM member_notes WHERE member_id = ?", (member_id,))]


def _sync(app, rows, *, apply=False, import_missing=False):
    with app.test_request_context():
        db = get_db()
        result = sync_from_report(db, rows, apply=apply, actor_id=1, import_missing=import_missing)
        db.commit()
    return result


def test_a_waiting_mandate_becomes_active_when_nupay_says_active(app):
    member_id = _member(app)
    mandate_id = _mandate(app, member_id, status="submitted")

    result = _sync(app, [_row(status="Active")], apply=True)

    assert _status(app, mandate_id) == "active"
    assert result["updated"] == 1
    assert result["changes"][0]["old"] == "submitted" and result["changes"][0]["new"] == "active"
    assert any("NuPay" in note and "active" in note for note in _notes(app, member_id))


def test_a_preview_reports_the_change_but_makes_none(app):
    member_id = _member(app)
    mandate_id = _mandate(app, member_id, status="submitted")

    result = _sync(app, [_row(status="Active")], apply=False)

    assert result["updated"] == 1 and result["applied"] is False
    assert _status(app, mandate_id) == "submitted"
    assert _notes(app, member_id) == []


def test_running_it_twice_changes_nothing_the_second_time(app):
    member_id = _member(app)
    _mandate(app, member_id, status="submitted")
    _sync(app, [_row(status="Active")], apply=True)

    again = _sync(app, [_row(status="Active")], apply=True)

    assert again["updated"] == 0 and again["unchanged"] == 1


def test_wording_that_means_the_same_is_not_counted_as_a_change(app):
    member_id = _member(app)
    _mandate(app, member_id, status="approved")     # already counts as active for the access rule

    result = _sync(app, [_row(status="Active")], apply=True)

    assert result["updated"] == 0 and result["unchanged"] == 1


def test_a_rejected_mandate_is_marked_rejected(app):
    member_id = _member(app)
    mandate_id = _mandate(app, member_id, status="active")

    _sync(app, [_row(status="Rejected Authorisation")], apply=True)

    assert _status(app, mandate_id) == "rejected"


def test_a_match_from_before_the_member_joined_is_flagged_not_applied(app):
    """A shared bank account can surface someone else's older mandate."""
    member_id = _member(app, join_date="2026-06-01")
    mandate_id = _mandate(app, member_id, status="submitted")

    _sync(app, [_row(status="Active", accepted="2025-01-01 09:00:00.000")], apply=True)

    assert _status(app, mandate_id) == "unverified"


def test_a_mandate_nupay_does_not_list_is_left_alone(app):
    member_id = _member(app)
    mandate_id = _mandate(app, member_id, status="submitted")

    result = _sync(app, [_row(client_ref="9999999-1111", account="0000000000")], apply=True)

    assert _status(app, mandate_id) == "submitted"
    assert result["not_found"] == 1 and result["updated"] == 0


def test_a_status_sync_counts_live_nupay_mandates_with_no_local_record_but_does_not_create_them(app):
    known = _member(app, itensity_ref="EHF003000001", first="Known", id_number="9101015009086")
    _member(app, itensity_ref="EHF003000002", first="AlsoKnown", id_number="9201015009085")
    rows = [
        _row(client_ref="3000001-5555", contract="DCPRD1"),      # a member V8 knows, no local mandate
        _row(client_ref="8888888-5555", contract="DCPRD2"),      # nobody V8 knows
        _row(client_ref="3000002-5555", status="Rejected Authorisation", contract="DCPRD3"),  # not live
    ]

    result = _sync(app, rows, apply=True)

    unrecorded = result["unrecorded"]
    assert unrecorded["live_without_local_record"] == 2
    assert unrecorded["matched_to_member"] == 1
    assert unrecorded["no_member"] == 1
    assert unrecorded["examples"][0]["member_id"] == known
    assert _mandate_count(app) == 0        # only an explicit import creates mandate records


def test_the_members_sa_id_finds_them_when_the_client_reference_does_not(app):
    _member(app, itensity_ref="EHF003000009", first="ById", id_number="9301015009084")
    rows = [_row(client_ref="garbled", debtor_id="9301015009084")]

    result = _sync(app, rows, apply=False)

    assert result["unrecorded"]["matched_to_member"] == 1


def test_the_report_summary_counts_nupay_rows_by_status(app):
    rows = [_row(status="Active"), _row(status="Active"), _row(status="Rejected Authorisation"),
            _row(status="Pending Authorisation"), _row(status="In Active")]

    result = _sync(app, rows, apply=False)

    assert result["report_rows"] == 5
    assert result["report_by_status"] == {"active": 2, "rejected": 1, "pending": 1, "cancelled": 1}


# ── Recording live NuPay mandates that V8 has no record of ───────────────────

def _mandates(app):
    with app.app_context():
        return get_db().execute("SELECT * FROM debicheck_mandates ORDER BY id").fetchall()


def _full_row(**overrides):
    row = _row(client_ref="3000001-5555", contract="DCPRD9", account="1234567890",
               debtor_id="9001015009087", debtor_name="Sam Sync", debtor_account_type_code="1",
               debtor_branch_number="250655", instalment_amount="345.58",
               first_collection_date="2026-08-25")
    row.update(overrides)
    return row


def test_a_live_nupay_mandate_is_recorded_for_the_member_it_belongs_to(app):
    from onecpase.encryption import decrypt

    member_id = _member(app, itensity_ref="EHF003000001")

    result = _sync(app, [_full_row()], import_missing=True)

    (mandate,) = _mandates(app)
    assert result["import"]["recorded"] == 1
    assert (mandate["member_id"], mandate["client_ref1"], mandate["client_ref2"], mandate["status"]) \
        == (member_id, "3000001", "5555", "active")
    assert mandate["imported_from_nupay"] == 1 and mandate["contract_reference"] == "DCPRD9"
    assert (mandate["instalment_amount"], mandate["instalment_rands"], mandate["instalment_cents"]) == (345.58, 345, 58)
    assert mandate["submit_date"] == "2026-08-25" and mandate["account_type"] == "1"
    assert mandate["payer_type"] == "self"                # the account holder is the member
    assert mandate["account_number"] != "1234567890"      # encrypted at rest, like any mandate
    with app.app_context():
        assert decrypt(mandate["account_number"]) == "1234567890"
        assert decrypt(mandate["branch_code"]) == "250655"
    assert any("imported from NuPay" in note for note in _notes(app, member_id))


def test_a_preview_records_nothing(app):
    _member(app, itensity_ref="EHF003000001")

    result = _sync(app, [_full_row()])

    assert result["import"]["to_record"] == 1 and result["import"]["recorded"] == 0
    assert _mandate_count(app) == 0


def test_recording_twice_records_nothing_the_second_time(app):
    _member(app, itensity_ref="EHF003000001")
    _sync(app, [_full_row()], import_missing=True)

    again = _sync(app, [_full_row()], import_missing=True)

    assert again["import"]["recorded"] == 0 and again["unrecorded"]["live_without_local_record"] == 0
    assert again["unchanged"] == 1 and _mandate_count(app) == 1


def test_only_the_latest_of_several_live_mandates_for_a_member_is_recorded(app):
    _member(app, itensity_ref="EHF003000001")
    rows = [_full_row(contract_reference="OLD", mandate_acceptance_date="2025-01-01 09:00:00.000"),
            _full_row(contract_reference="NEW", mandate_acceptance_date="2026-08-01 09:00:00.000")]

    result = _sync(app, rows, import_missing=True)

    (mandate,) = _mandates(app)
    assert mandate["contract_reference"] == "NEW"
    assert result["import"]["superseded"] == 1


def test_a_member_named_only_by_the_account_holders_id_is_held_for_review(app):
    """A parent paying for a child has their own ID on the mandate, not the member's."""
    _member(app, itensity_ref="EHF003000001")
    rows = [_full_row(client_reference="garbled", debtor_id="9001015009087")]

    result = _sync(app, rows, import_missing=True)

    assert _mandate_count(app) == 0
    assert result["import"]["held_id_only"] == 1 and result["import"]["to_record"] == 0


def test_a_client_reference_of_ref_concatenated_with_the_holders_id_is_recognised(app):
    """The new portal sometimes writes client_reference as the Itensity ref glued
    straight onto the account holder's 13-digit SA ID, with no separator
    ('30000019001015009087' = ref '3000001' + ID '9001015009087') - this must be
    read as a real match, not held back as if only the ID were known."""
    _member(app, itensity_ref="EHF003000001")
    rows = [_full_row(client_reference="30000019001015009087", debtor_id="9001015009087")]

    result = _sync(app, rows, import_missing=True)

    assert result["import"]["recorded"] == 1 and result["import"]["held_id_only"] == 0
    assert _mandate_count(app) == 1


def test_the_recorded_tracking_days_come_from_tracking_desc_not_the_yn_flag(app):
    """NuPay's `tracking` field is only a Y/N flag; the real day count is in
    `tracking_desc` ('5 Day Tracking'). Converted back to V8's own tracking code."""
    _member(app, itensity_ref="EHF003000001")

    _sync(app, [_full_row(tracking="Y", tracking_desc="5 Day Tracking")], import_missing=True)

    assert _mandates(app)[0]["tracking"] == "03"        # V8's own code for 5-day tracking


def test_tracking_ignores_the_yn_flag_itself(app):
    _member(app, itensity_ref="EHF003000001")

    _sync(app, [_full_row(tracking="N", tracking_desc="4 Day Tracking")], import_missing=True)

    assert _mandates(app)[0]["tracking"] == "02"        # V8's own code for 4-day tracking


def test_tracking_falls_back_when_the_day_count_cannot_be_read(app):
    _member(app, itensity_ref="EHF003000001")

    _sync(app, [_full_row(tracking="Y", tracking_desc="")], import_missing=True)

    assert _mandates(app)[0]["tracking"] == "14"


def test_the_recorded_frequency_comes_from_nupays_own_wording_not_a_digit_guess(app):
    """NuPay's `frequency` field holds words (Monthly/Weekly/Ad-Hoc), not a digit code."""
    _member(app, itensity_ref="EHF003000001")

    _sync(app, [_full_row(frequency="WEEK")], import_missing=True)

    assert _mandates(app)[0]["frequency"] == "1"        # V8's own code for weekly


def test_frequency_falls_back_to_monthly_when_unrecognised(app):
    _member(app, itensity_ref="EHF003000001")

    _sync(app, [_full_row(frequency="")], import_missing=True)

    assert _mandates(app)[0]["frequency"] == "3"


def test_a_third_party_payer_does_not_stop_the_members_mandate_being_recorded(app):
    _member(app, itensity_ref="EHF003000001")
    rows = [_full_row(debtor_id="8001015009087")]        # the payer's ID - not a V8 member

    _sync(app, rows, import_missing=True)

    (mandate,) = _mandates(app)
    assert mandate["payer_type"] == "third_party"


def test_the_client_reference_beats_a_payer_who_happens_to_be_another_member(app):
    child = _member(app, itensity_ref="EHF003000001", first="Child", id_number="9401015009083")
    parent = _member(app, itensity_ref="EHF003000002", first="Parent", id_number="7001015009084")
    rows = [_full_row(debtor_id="7001015009084")]        # the parent pays; the reference names the child

    _sync(app, rows, import_missing=True)

    (mandate,) = _mandates(app)
    assert mandate["member_id"] == child and mandate["member_id"] != parent
    assert mandate["payer_type"] == "third_party"


def test_a_member_who_already_has_a_mandate_record_is_not_given_a_second(app):
    member_id = _member(app, itensity_ref="EHF003000001")
    _mandate(app, member_id, client_ref1="FIT-77", account="0000099999", status="draft")

    result = _sync(app, [_full_row()], import_missing=True)

    assert _mandate_count(app) == 1
    assert result["import"]["member_already_has_a_mandate"] == 1 and result["import"]["recorded"] == 0


def test_recording_a_mandate_leaves_the_members_application_untouched(app):
    from onecpase.applications import ensure_member_application

    member_id = _member(app, itensity_ref="EHF003000001")
    with app.test_request_context():
        db = get_db()
        ensure_member_application(db, member_id)
        db.commit()

    _sync(app, [_full_row()], import_missing=True)

    with app.app_context():
        gates = [r["debit_check_status"] for r in get_db().execute("SELECT debit_check_status FROM compliance_checklists")]
    assert gates and set(gates) == {"pending"}


def test_a_recorded_mandate_is_found_again_by_its_contract_reference(app):
    """Later syncs match it exactly, without depending on the client reference."""
    _member(app, itensity_ref="EHF003000001")
    _sync(app, [_full_row()], import_missing=True)
    later = _full_row(client_reference="reissued-9999")   # NuPay changed the client reference

    result = _sync(app, [later], import_missing=True)

    assert result["not_found"] == 0 and result["unchanged"] == 1 and _mandate_count(app) == 1


def test_a_mandate_v8_shows_active_but_nupays_active_list_lacks_is_flagged_not_changed(app):
    active = _mandate(app, _member(app), client_ref1="2160994", status="active")
    waiting = _mandate(app, _member(app, itensity_ref="EHF002160995", first="Wait", id_number="9501015009082"),
                       client_ref1="2160995", account="5559990000", status="pending")

    result = _sync(app, [_row(client_ref="7777777-1", account="1")], apply=True)

    flagged = result["missing_from_active_list"]
    assert flagged["count"] == 1 and flagged["examples"][0]["mandate_id"] == active
    assert _status(app, active) == "active" and _status(app, waiting) == "pending"


def test_an_imported_mandate_can_never_be_sent_to_nupay_again(app, client):
    from onecpase.debicheck import record_push_result

    member_id = _member(app, itensity_ref="EHF003000001")
    _sync(app, [_full_row()], import_missing=True)
    mandate_id = _mandates(app)[0]["id"]
    _sign_in(client, permissions=["new_debicheck", "debicheck_mandates"])

    with patch("onecpase.debicheck_routes.push_mandate_to_nupay") as push, \
         patch("onecpase.debicheck_routes._background_manual_review_push") as thread:
        pushed = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True).get_data(as_text=True)
        reviewed = client.post(f"/debicheck/{mandate_id}/review-on-nupay", follow_redirects=True).get_data(as_text=True)

    push.assert_not_called()
    thread.assert_not_called()
    assert "imported from NuPay" in pushed and "imported from NuPay" in reviewed
    assert _status(app, mandate_id) == "active"
    with app.app_context():                     # even a stray failed-push result cannot overwrite it
        db = get_db()
        record_push_result(db, mandate_id, False, "should not stick")
        db.commit()
    assert _status(app, mandate_id) == "active"


def test_the_mandate_page_says_an_imported_mandate_is_live_and_offers_no_send_buttons(app, client):
    _member(app, itensity_ref="EHF003000001")
    _sync(app, [_full_row()], import_missing=True)
    mandate_id = _mandates(app)[0]["id"]
    _sign_in(client, permissions=["new_debicheck", "debicheck_mandates"])

    page = client.get(f"/debicheck/{mandate_id}").get_data(as_text=True)

    assert "Imported from NuPay" in page and "DCPRD9" in page
    assert "Review &amp; Submit on NuPay" not in page and "Push Automatically" not in page


# ── The page ─────────────────────────────────────────────────────────────────

def _sign_in(client, role="admin", permissions=()):
    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": role, "permissions": list(permissions)})


def test_the_sync_page_previews_without_changing_anything(app, client):
    member_id = _member(app)
    mandate_id = _mandate(app, member_id, status="submitted")
    _sign_in(client)

    with patch("onecpase.nupay_driver.fetch_mandate_report_rows", return_value=([_row(status="Active")], "ok")):
        page = client.post("/debicheck/sync", data={"action": "preview"}).get_data(as_text=True)

    assert "Preview" in page and "would be updated" in page
    assert _status(app, mandate_id) == "submitted"


def test_the_sync_page_applies_when_asked(app, client):
    member_id = _member(app)
    mandate_id = _mandate(app, member_id, status="submitted")
    _sign_in(client)

    with patch("onecpase.nupay_driver.fetch_mandate_report_rows", return_value=([_row(status="Active")], "ok")) as pull:
        client.post("/debicheck/sync", data={"action": "apply"})

    assert pull.call_count == 1               # one login for however many mandates
    assert _status(app, mandate_id) == "active"


def test_the_sync_page_previews_and_then_records_mandates_only_when_asked(app, client):
    _member(app, itensity_ref="EHF003000001")
    _sign_in(client)

    with patch("onecpase.nupay_driver.fetch_mandate_report_rows", return_value=([_full_row()], "ok")):
        preview = client.post("/debicheck/sync", data={"action": "preview"}).get_data(as_text=True)
        client.post("/debicheck/sync", data={"action": "apply"})             # statuses only
        assert _mandate_count(app) == 0
        recorded = client.post("/debicheck/sync", data={"action": "import"}).get_data(as_text=True)

    assert "would be recorded" in preview
    assert _mandate_count(app) == 1 and "recorded" in recorded


def test_a_failed_pull_changes_nothing_and_says_why(app, client):
    member_id = _member(app)
    mandate_id = _mandate(app, member_id, status="submitted")
    _sign_in(client)

    with patch("onecpase.nupay_driver.fetch_mandate_report_rows", return_value=(None, "NuPay login failed")):
        page = client.post("/debicheck/sync", data={"action": "apply"}).get_data(as_text=True)

    assert "NuPay login failed" in page
    assert _status(app, mandate_id) == "submitted"


def test_staff_without_the_debicheck_permission_cannot_run_it(app, client):
    _sign_in(client, role="reception", permissions=[])

    with patch("onecpase.nupay_driver.fetch_mandate_report_rows") as pull:
        response = client.post("/debicheck/sync", data={"action": "apply"})

    assert response.status_code == 302
    pull.assert_not_called()


# ── The single-mandate check shares the same code ────────────────────────────

def test_the_single_mandate_check_still_reads_the_report_and_matches_as_before(app):
    mandate = {"client_ref1": "2160994", "account_number": "1112223334", "contract_reference": "",
               "member_join_date": "2026-01-01", "merchant_id": "m1"}

    with patch.object(nupay_driver, "_require_creds", return_value=[]), \
         patch.object(nupay_driver, "fetch_mandate_report_rows",
                      return_value=([_row(status="Active")], "ok")) as pull:
        result = nupay_driver.refresh_mandate_status(mandate)

    assert pull.call_count == 1
    assert result.found and result.status == "active"


def test_the_single_mandate_check_reports_a_failed_pull(app):
    mandate = {"client_ref1": "2160994", "merchant_id": "m1"}

    with patch.object(nupay_driver, "_require_creds", return_value=[]), \
         patch.object(nupay_driver, "fetch_mandate_report_rows", return_value=(None, "NuPay login failed")):
        result = nupay_driver.refresh_mandate_status(mandate)

    assert not result.found and "NuPay login failed" in result.message
