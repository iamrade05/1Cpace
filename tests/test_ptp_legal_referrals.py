from onecpase.database import get_db


def _seed_legal_candidate():
    db = get_db()
    db.execute(
        """INSERT INTO users (id, username, password_hash, full_name, role, department, active)
           VALUES (1, 'admin1', 'x', 'Admin One', 'admin', 'Admin', 1)"""
    )
    cursor = db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, gym_access_status)
           VALUES ('ELE-LEGAL-0001', 'Legal', 'Candidate', 'IDLEGAL0001', '0715550001',
                   'Active', 'allowed')"""
    )
    member_id = cursor.lastrowid
    for month in ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06", "2025-07"]:
        db.execute(
            """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
                   collection_date, status, notes)
               VALUES (?, 500, 0, ?, 'failed', 'type=Recurring Fee')""",
            (member_id, f"{month}-01"),
        )
    db.commit()
    return member_id


def test_legal_referral_queue_supports_refer_and_resolve(app):
    with app.app_context():
        member_id = _seed_legal_candidate()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "admin1"
            sess["role"] = "admin"
            sess["permissions"] = ["all_collections"]

        response = client.get("/ptp/legal")
        assert response.status_code == 200
        assert b"Legal Referrals" in response.data
        assert b"Refer to Legal" in response.data

        referral = client.post(
            f"/members/{member_id}/legal/refer",
            data={"reason": "More than 6 months in arrears"},
            follow_redirects=True,
        )
        assert referral.status_code == 200
        assert b"Open legal referrals" in referral.data

        with app.app_context():
            row = get_db().execute(
                "SELECT status, arrears_months FROM legal_referrals WHERE member_id=?",
                (member_id,),
            ).fetchone()
            assert row["status"] == "open"
            assert row["arrears_months"] == 7

        resolved = client.post(
            f"/members/{member_id}/legal/resolve",
            follow_redirects=True,
        )
        assert resolved.status_code == 200

    with app.app_context():
        row = get_db().execute(
            "SELECT status FROM legal_referrals WHERE member_id=?",
            (member_id,),
        ).fetchone()
        assert row["status"] == "resolved"
