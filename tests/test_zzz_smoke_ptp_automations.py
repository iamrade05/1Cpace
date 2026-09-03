"""Temporary smoke test for the Phase 1 collections automation work.
Not part of the permanent suite -- deleted after manual verification."""
from datetime import date, timedelta

from onecpase.database import get_db, member_activity


def _seed(app):
    db = get_db()
    db.execute(
        """INSERT INTO users (id, username, password_hash, full_name, role, department, active)
           VALUES (1, 'admin1', 'x', 'Admin One', 'admin', 'Admin', 1),
                  (2, 'recep1', 'x', 'Recep One', 'reception', 'Reception', 1),
                  (3, 'mgr1', 'x', 'Manager One', 'manager', 'Collections', 1)"""
    )
    # Member A: 3 months in arrears, no PTP/DebiCheck -> should be restricted
    cur = db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, gym_access_status)
           VALUES ('ELE-0000000001','Arrears','MemberA','ID0001','0710000001','Active','allowed')"""
    )
    mid_a = cur.lastrowid
    for i, month in enumerate(["2026-05", "2026-06", "2026-07"]):
        db.execute(
            """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
                   collection_date, status, notes)
               VALUES (?, 500, 0, ?, 'failed', 'Recurring Fee')""",
            (mid_a, f"{month}-01"),
        )

    # Member B: 9 months in arrears (oldest month Nov 2025) -> should be flagged inactive
    cur = db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, gym_access_status)
           VALUES ('ELE-0000000002','OldDebt','MemberB','ID0002','0710000002','Active','blocked')"""
    )
    mid_b = cur.lastrowid
    db.execute(
        """UPDATE members SET access_block_reason='manual' WHERE id=?""", (mid_b,)
    )
    for month in ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]:
        db.execute(
            """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
                   collection_date, status, notes)
               VALUES (?, 500, 0, ?, 'failed', 'Recurring Fee')""",
            (mid_b, f"{month}-01"),
        )

    # PTP agreement referenced later for the approve-route role check
    cur = db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, gym_access_status)
           VALUES ('ELE-0000000003','Approval','MemberC','ID0003','0710000003','Active','allowed')"""
    )
    mid_c = cur.lastrowid
    cur = db.execute(
        """INSERT INTO ptp_agreements (member_id, created_by, promise_amount, promise_date,
               payment_method, ptp_status, manager_approval_status)
           VALUES (?, 1, 500, '2026-09-01', 'cash', 'pending', 'pending')""",
        (mid_c,),
    )
    ptp_id = cur.lastrowid
    db.commit()
    return {"mid_a": mid_a, "mid_b": mid_b, "mid_c": mid_c, "ptp_id": ptp_id}


def test_dashboard_loads_and_preview_endpoint_works(app):
    with app.app_context():
        ids = _seed(app)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "admin1"
            sess["role"] = "admin"
            sess["permissions"] = []

        resp = client.get("/ptp/")
        assert resp.status_code == 200
        assert b"Access Restricted" in resp.data
        assert b"Flagged Inactive" in resp.data

        preview = client.get("/ptp/run-automations/preview")
        assert preview.status_code == 200
        counts = preview.get_json()
        print("PREVIEW COUNTS:", counts)
        assert counts["restricted_count"] == 1
        assert counts["inactive_count"] == 1

        # dry run must not have written anything
        with app.app_context():
            row = get_db().execute(
                "SELECT gym_access_status FROM members WHERE id=?", (ids["mid_a"],)
            ).fetchone()
            assert row["gym_access_status"] == "allowed"

        run = client.post("/ptp/run-automations")
        assert run.status_code == 302

    with app.app_context():
        db = get_db()
        a = db.execute("SELECT gym_access_status, access_block_reason FROM members WHERE id=?", (ids["mid_a"],)).fetchone()
        b = db.execute("SELECT member_status FROM members WHERE id=?", (ids["mid_b"],)).fetchone()
        print("MEMBER A:", dict(a))
        print("MEMBER B:", dict(b))
        assert a["gym_access_status"] == "blocked"
        assert a["access_block_reason"] == "arrears_no_arrangement"
        assert b["member_status"] == "Inactive"


def test_ptp_approve_requires_manager_role(app):
    with app.app_context():
        ids = _seed(app)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "recep1"
            sess["role"] = "reception"
            sess["permissions"] = ["all_collections"]
        resp = client.post(
            f"/members/{ids['mid_c']}/ptp/{ids['ptp_id']}/approve",
            data={"decision": "approved"},
            follow_redirects=True,
        )
        assert b"You don&#39;t have permission" in resp.data or b"don't have permission" in resp.data

    with app.app_context():
        status = get_db().execute(
            "SELECT manager_approval_status FROM ptp_agreements WHERE id=?", (ids["ptp_id"],)
        ).fetchone()["manager_approval_status"]
        assert status == "pending"

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 3
            sess["username"] = "mgr1"
            sess["role"] = "manager"
            sess["permissions"] = []
        resp = client.post(
            f"/members/{ids['mid_c']}/ptp/{ids['ptp_id']}/approve",
            data={"decision": "approved"},
        )
        assert resp.status_code == 302

    with app.app_context():
        status = get_db().execute(
            "SELECT manager_approval_status FROM ptp_agreements WHERE id=?", (ids["ptp_id"],)
        ).fetchone()["manager_approval_status"]
        assert status == "approved"
