from onecpase.database import get_db
from unittest.mock import patch


def _login_as_admin(client):
    """Create the same authorization state established by a real admin login."""
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "testuser"
        sess["role"] = "admin"


def test_members_index_requires_login(client):
    response = client.get("/members", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_members_index_page(app):
    with app.test_client() as client:
        _login_as_admin(client)

        response = client.get("/members")
        assert response.status_code == 200
        assert b"Members" in response.data


def test_members_add_page(app):
    with app.test_client() as client:
        _login_as_admin(client)

        response = client.get("/members/add")
        assert response.status_code == 200
        assert b"Add Member" in response.data


def test_members_add_member(app):
    with app.test_client() as client:
        _login_as_admin(client)

        response = client.post(
            "/members/add",
            data={
                "first_name": "John",
                "last_name": "Doe",
                "id_number": "1234567890128",
                "contact": "0712345678",
                "email": "john@example.com",
                "join_date": "2026-01-01",
                "package": "Basic",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"John Doe" in response.data or b"Members" in response.data

        # Verify member was saved in DB
        db = get_db()
        member = db.execute(
            "SELECT * FROM members WHERE id_number = ?", ("1234567890128",)
        ).fetchone()
        assert member is not None
        assert member["first_name"] == "John"
        assert member["last_name"] == "Doe"
        assert member["member_ref"] == "ELE-0000000001"


def test_add_member_pushes_third_party_debicheck(app):
    with app.test_client() as client:
        _login_as_admin(client)
        data = {
            "first_name": "New",
            "last_name": "Member",
            "id_number": "9001015009087",
            "monthly_installment": "345.58",
            "payment_type": "Third-Party Debit Order",
            "payer_type": "third_party",
            "payer_name": "Parent Payer",
            "payer_id_number": "7001015009084",
            "account_name": "Parent Payer",
            "bank": "FNB / RMB",
            "account_type": "Cheque/Current",
            "account_number": "1234567890",
            "branch_code": "250655",
            "debicheck_submit_date": "2026-07-01",
            "debicheck_client_ref2": "1234",
            "debicheck_frequency": "3",
            "debicheck_installments": "12",
            "debicheck_tracking": "14",
            "submit_action": "push_nupay",
        }
        with patch(
            "onecpase.members.push_mandate_to_nupay",
            return_value=(True, "Waiting for authentication"),
        ) as push:
            response = client.post("/members/add", data=data)

        assert response.status_code == 302
        push.assert_called_once()
        db = get_db()
        member = db.execute(
            "SELECT * FROM members WHERE id_number = ?", (data["id_number"],)
        ).fetchone()
        mandate = db.execute(
            "SELECT * FROM debicheck_mandates WHERE member_id = ?", (member["id"],)
        ).fetchone()
        assert member["payment_type"] == "Third-Party Debit Order"
        assert member["payer_id_number"] == data["payer_id_number"]
        assert mandate["payer_type"] == "third_party"
        assert mandate["account_type"] == "1"
        assert mandate["status"] == "submitted"


def test_third_party_debicheck_requires_valid_payer_id(app):
    with app.test_client() as client:
        _login_as_admin(client)
        data = {
            "first_name": "Blocked",
            "last_name": "Member",
            "id_number": "9101015009086",
            "monthly_installment": "300",
            "payment_type": "Third-Party Debit Order",
            "payer_type": "third_party",
            "payer_name": "Parent Payer",
            "payer_id_number": "123",
            "account_name": "Parent Payer",
            "bank": "ABSA",
            "account_type": "Savings",
            "account_number": "1234567890",
            "branch_code": "632005",
            "debicheck_submit_date": "2026-07-01",
            "debicheck_client_ref2": "1234",
            "submit_action": "push_nupay",
        }
        with patch("onecpase.members.push_mandate_to_nupay") as push:
            response = client.post("/members/add", data=data)

        assert response.status_code == 200
        push.assert_not_called()
        assert b"valid 13-digit SA ID number" in response.data
        assert get_db().execute(
            "SELECT id FROM members WHERE id_number = ?", (data["id_number"],)
        ).fetchone() is None
