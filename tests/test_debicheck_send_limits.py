"""One DebiCheck a day per client and kind, and no third failed one without a manager."""

from onecpase.database import get_db

from test_debicheck_routes import _login, _ready_to_submit


def _driver(monkeypatch, result=(True, "Mandate accepted")):
    import onecpase.debicheck_routes as routes
    calls = []

    def push(mandate):
        calls.append(mandate)
        return result

    monkeypatch.setattr(routes, "push_mandate_to_nupay", push)
    return calls


def _sql(client, sql, params=()):
    with client.application.app_context():
        db = get_db()
        rows = db.execute(sql, params).fetchall()
        db.commit()
        return rows


def _second_mandate(client, member_id):
    with client.application.app_context():
        db = get_db()
        mandate_id = db.execute(
            """INSERT INTO debicheck_mandates
               (member_id, merchant_id, client_ref1, client_ref2, account_name, account_type,
                account_number, branch_code, frequency, no_installments, tracking, submit_date,
                id_type, id_number, instalment_rands, instalment_cents, instalment_amount,
                payer_type, status)
               SELECT member_id, merchant_id, client_ref1, client_ref2, account_name, account_type,
                account_number, branch_code, frequency, no_installments, tracking, submit_date,
                id_type, id_number, instalment_rands, instalment_cents, instalment_amount,
                payer_type, 'pending' FROM debicheck_mandates WHERE member_id=? LIMIT 1""",
            (member_id,),
        ).lastrowid
        db.commit()
    return mandate_id


def _move_sends_to_yesterday(client):
    _sql(client, "UPDATE debicheck_send_log SET sent_day='2000-01-01'")


def test_second_send_on_the_same_day_is_blocked(client, monkeypatch):
    calls = _driver(monkeypatch)
    member_id, mandate_id = _ready_to_submit(client)
    _login(client)

    assert b"Mandate sent to NuPay" in client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True).data
    response = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert b"Not sent to NuPay" in response.data and b"Only one a day" in response.data

    other = _second_mandate(client, member_id)
    response = client.post(f"/debicheck/{other}/push", follow_redirects=True)
    assert b"Only one a day" in response.data
    assert len(calls) == 1

    page = client.get(f"/debicheck/{other}").data
    assert b"Already sent today" in page and b"Resend Automatically" not in page and b"Push Automatically" not in page


def test_a_push_that_never_reached_nupay_can_be_retried_the_same_day(client, monkeypatch):
    calls = _driver(monkeypatch, (False, "NuPay timed out"))
    _member_id, mandate_id = _ready_to_submit(client)
    _login(client)

    client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert _sql(client, "SELECT COUNT(*) AS n FROM debicheck_send_log")[0]["n"] == 0
    client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert len(calls) == 2


def test_next_day_waits_until_the_last_send_shows_rejected(client, monkeypatch):
    calls = _driver(monkeypatch)
    _member_id, mandate_id = _ready_to_submit(client)
    _login(client)
    client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    _move_sends_to_yesterday(client)

    response = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert b"still waiting for the client" in response.data and len(calls) == 1

    _sql(client, "UPDATE debicheck_mandates SET status='rejected' WHERE id=?", (mandate_id,))
    response = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert b"Mandate sent to NuPay" in response.data and len(calls) == 2
    outcomes = [r["outcome"] for r in _sql(client, "SELECT outcome FROM debicheck_send_log ORDER BY id")]
    assert outcomes == ["rejected", None]


def test_third_failed_attempt_needs_a_manager(client, monkeypatch):
    calls = _driver(monkeypatch)
    member_id, mandate_id = _ready_to_submit(client)
    _login(client)
    for _ in range(2):
        client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
        _move_sends_to_yesterday(client)
        _sql(client, "UPDATE debicheck_mandates SET status='cancelled' WHERE id=?", (mandate_id,))
    assert len(calls) == 2

    response = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert b"A third can only be sent by a manager" in response.data and len(calls) == 2
    assert b"allow an extra attempt" not in client.get(f"/debicheck/{mandate_id}").data

    _login(client, role="manager")
    assert b"allow an extra attempt" in client.get(f"/debicheck/{mandate_id}").data
    client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert len(calls) == 2  # the box must be ticked
    response = client.post(f"/debicheck/{mandate_id}/push", data={"extra_attempt": "1"}, follow_redirects=True)
    assert b"Mandate sent to NuPay" in response.data and len(calls) == 3
    notes = [r["note"] for r in _sql(client, "SELECT note FROM member_notes WHERE member_id=?", (member_id,))]
    assert any("Extra DebiCheck attempt" in note for note in notes)


def test_arrears_and_normal_mandates_can_go_on_the_same_day(client, monkeypatch):
    calls = _driver(monkeypatch)
    member_id, mandate_id = _ready_to_submit(client)
    with client.application.app_context():
        db = get_db()
        ptp_id = db.execute(
            "INSERT INTO ptp_agreements (member_id, arrears_amount, promise_amount, promise_date, payment_method) "
            "VALUES (?, 1000, 1000, '2099-01-01', 'debicheck')", (member_id,)).lastrowid
        db.execute("INSERT INTO ptp_mandate_links (ptp_id, purpose, amount, mandate_id) "
                   "VALUES (?, 'Arrangement (PTP)', 1000, ?)", (ptp_id, mandate_id))
        db.commit()
    other = _second_mandate(client, member_id)
    _login(client)

    client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    client.post(f"/debicheck/{other}/push", follow_redirects=True)
    assert len(calls) == 2
    purposes = sorted(r["purpose"] for r in _sql(client, "SELECT purpose FROM debicheck_send_log"))
    assert purposes == ["Arrangement (PTP)", "Normal instalment"]


def test_backfill_starts_from_mandates_already_sent(client):
    from onecpase.debicheck_limits import ensure_schema
    member_id, mandate_id = _ready_to_submit(client)
    with client.application.app_context():
        db = get_db()
        db.execute("DELETE FROM debicheck_send_log")
        db.execute("UPDATE debicheck_mandates SET status='rejected', submitted_at='2026-11-05T23:30:00' WHERE id=?",
                   (mandate_id,))
        db.commit()
        ensure_schema(db)
        rows = db.execute("SELECT member_id, mandate_id, sent_day, state FROM debicheck_send_log").fetchall()
    assert [tuple(r) for r in rows] == [(member_id, mandate_id, "2026-11-06", "sent")]
