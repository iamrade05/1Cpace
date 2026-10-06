def _sign_in(client, user_id):
    with client.session_transaction() as s:
        s.update({"user_id": user_id, "username": "u", "role": "admin", "permissions": []})


def test_signed_in_staff_are_counted_one_by_one_not_by_shared_address(app):
    from onecpase.extensions import _rate_limit_key

    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "196.41.220.16"}):
        from flask import session

        session["user_id"] = 7
        assert _rate_limit_key() == "user:7"
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "196.41.220.16"}):
        from flask import session

        session["user_id"] = 8
        assert _rate_limit_key() == "user:8"
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "196.41.220.16"}):
        assert _rate_limit_key() == "196.41.220.16"   # anonymous visitors are still counted by address


def test_a_busy_user_no_longer_locks_out_a_colleague_on_the_same_connection(app):
    busy, colleague = app.test_client(), app.test_client()
    _sign_in(busy, 1)
    _sign_in(colleague, 2)
    # Well past the old 60-an-hour allowance, from the same address.
    codes = [busy.get("/collections/reconciliation", environ_base={"REMOTE_ADDR": "196.41.220.16"}).status_code for _ in range(90)]
    assert 429 not in codes
    assert colleague.get("/collections/reconciliation", environ_base={"REMOTE_ADDR": "196.41.220.16"}).status_code != 429


def test_login_stays_strictly_limited(app):
    client = app.test_client()
    codes = [client.post("/login", data={"username": "x", "password": "y"}, environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code
             for _ in range(30)]
    assert 429 in codes
