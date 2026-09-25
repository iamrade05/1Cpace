"""NuPay push must not run ahead of Itensity: client_ref1 is derived from the
member's Itensity reference, so pushing before that reference exists would
submit a mandate NuPay and Itensity can never reconcile against each other.
"""

from onecpase.database import get_db


def _login(client, role="staff"):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = role
        session["permissions"] = ["new_debicheck", "debicheck_mandates"]
    with client.application.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO users
               (id, username, password_hash, full_name, role, active)
               VALUES (1, 'debicheck-actor', 'x', 'DebiCheck Actor', ?, 1)""",
            (role,),
        )
        db.commit()


def _member_and_mandate(db, *, itensity_ref=None):
    member_id = db.execute(
        """INSERT INTO members (first_name, last_name, id_number, monthly_installment)
           VALUES ('Test', 'Member', '9001015009087', 500)"""
    ).lastrowid
    if itensity_ref:
        db.execute("UPDATE members SET itensity_ref=? WHERE id=?", (itensity_ref, member_id))
    mandate_id = db.execute(
        """INSERT INTO debicheck_mandates
           (member_id, merchant_id, client_ref1, client_ref2, account_name, account_type,
            account_number, branch_code, frequency, no_installments, tracking, submit_date,
            id_type, id_number, instalment_rands, instalment_cents, instalment_amount,
            payer_type, status)
           VALUES (?, 'M1', 'FIT-1', '1234', 'Test Member', '1', '123456', '123456', '3', 12,
                   '14', '2026-09-20', '0', '9001015009087', 500, 0, 500.0, 'self', 'pending')""",
        (member_id,),
    ).lastrowid
    db.commit()
    return member_id, mandate_id


def test_nupay_push_is_blocked_without_an_itensity_reference(client):
    _login(client)
    with client.application.app_context():
        db = get_db()
        _member_id, mandate_id = _member_and_mandate(db)

    response = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert b"hasn" in response.data and b"Itensity" in response.data
    with client.application.app_context():
        row = get_db().execute(
            "SELECT status FROM debicheck_mandates WHERE id=?", (mandate_id,)
        ).fetchone()
    assert row["status"] == "pending"  # unchanged - never reached the NuPay driver


def test_nupay_manual_review_is_blocked_without_an_itensity_reference(client):
    _login(client)
    with client.application.app_context():
        db = get_db()
        _member_id, mandate_id = _member_and_mandate(db)

    response = client.post(f"/debicheck/{mandate_id}/review-on-nupay", follow_redirects=True)
    assert b"hasn" in response.data and b"Itensity" in response.data
    with client.application.app_context():
        row = get_db().execute(
            "SELECT status FROM debicheck_mandates WHERE id=?", (mandate_id,)
        ).fetchone()
    assert row["status"] == "pending"  # never flipped to 'reviewing'


def test_nupay_push_proceeds_once_a_real_itensity_reference_exists(client, monkeypatch):
    _login(client)
    with client.application.app_context():
        db = get_db()
        _member_id, mandate_id = _member_and_mandate(db, itensity_ref="EHF002160994")

    import onecpase.debicheck_routes as debicheck_routes
    monkeypatch.setattr(
        debicheck_routes, "push_mandate_to_nupay",
        lambda mandate: (True, "Mandate accepted"),
    )

    response = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert b"Mandate sent to NuPay" in response.data
    with client.application.app_context():
        row = get_db().execute(
            "SELECT status FROM debicheck_mandates WHERE id=?", (mandate_id,)
        ).fetchone()
    assert row["status"] == "submitted"


# ── Mandate coverage: which Debit Order members have no mandate at all ──────
#
# debicheck.index() only ever lists mandate rows (JOIN, not LEFT JOIN), so a
# member with zero mandates never appears there. coverage() exists to answer
# "which Debit Order members are missing a mandate" directly.

def test_coverage_splits_debit_order_members_by_whether_they_have_a_mandate(client):
    # Names deliberately avoid the words "No"/"Mandate" - the page heading
    # itself contains that phrase regardless of who's listed under it, so a
    # test member named after it would pass even if the query were broken.
    _login(client)
    with client.application.app_context():
        db = get_db()
        has_id, _ = _member_and_mandate(db, itensity_ref="ITN-1")
        db.execute(
            "INSERT INTO members (first_name, last_name, id_number, payment_type) "
            "VALUES ('Gapless', 'Coverage', '8001015009087', 'Debit Order')"
        )
        db.commit()

    response = client.get("/debicheck/coverage")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Test Member" in html        # has a mandate
    assert "Gapless Coverage" in html   # has none


def test_coverage_ignores_members_who_do_not_pay_by_debit_order(client):
    _login(client)
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO members (first_name, last_name, id_number, payment_type) "
            "VALUES ('Cash', 'Payer', '7001015009087', 'EFT/Cash')"
        )
        db.commit()

    response = client.get("/debicheck/coverage")
    html = response.get_data(as_text=True)
    assert "Cash" not in html or "Payer" not in html


def test_coverage_search_filters_by_name(client):
    _login(client)
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO members (first_name, last_name, id_number, payment_type) "
            "VALUES ('Findme', 'Person', '6001015009087', 'Debit Order'), "
            "       ('Other', 'Person', '6001015009088', 'Debit Order')"
        )
        db.commit()

    response = client.get("/debicheck/coverage?q=Findme")
    html = response.get_data(as_text=True)
    assert "Findme" in html
    assert "Other Person" not in html


def test_coverage_requires_the_debicheck_permission(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "staff"
        session["permissions"] = []
    response = client.get("/debicheck/coverage", follow_redirects=True)
    assert b"don&#39;t have permission" in response.data or b"don't have permission" in response.data
