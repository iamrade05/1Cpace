"""Custom membership package - a manager-only override of the standard tariffs.

For a member on a different agreement or a family package. Stored in the existing
tariff/package/amount/duration columns (tariff is the marker "Custom") so reports,
applications and collections keep working, plus a reason and an audit note.
"""
import pytest

from onecpase.database import get_db
from onecpase.encryption import hash_for_lookup

CUSTOM = {
    "tariff": "Custom",
    "custom_name": "Family - Ily (4 members)",
    "custom_amount": "450.00",
    "custom_months": "24",
    "custom_type": "Family package",
    "custom_note": "Mother and three children on one agreement",
}
ID_NUMBER = "9001015009087"


def _sign_in(client, role):
    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": role, "permissions": ["add_member", "all_members"]})


def _member_form(**extra):
    data = {
        "first_name": "Fam", "last_name": "Ily", "id_number": ID_NUMBER,
        "contact": "0712345678", "payment_type": "Cash",
    }
    data.update(extra)
    return data


def _member(app, id_number=ID_NUMBER):
    with app.app_context():
        return get_db().execute(
            "SELECT * FROM members WHERE id_number_hash = ?", (hash_for_lookup(id_number),)
        ).fetchone()


def _notes(app, member_id):
    with app.app_context():
        return [
            row["note"] for row in get_db().execute(
                "SELECT note FROM member_notes WHERE member_id = ? ORDER BY id", (member_id,)
            )
        ]


def _custom_member(app, client):
    """A member a manager has already put on the custom package."""
    _sign_in(client, "manager")
    client.post("/members/add", data=_member_form(**CUSTOM))
    return _member(app)


def _edit(client, member_id, **extra):
    data = _member_form(contact="0799999999")
    data.update(extra)
    return client.post(f"/members/{member_id}/edit", data=data)


def test_manager_can_add_a_member_on_a_custom_package(app, client):
    _sign_in(client, "manager")

    client.post("/members/add", data=_member_form(**CUSTOM))

    member = _member(app)
    assert member is not None
    assert member["tariff"] == "Custom"
    assert member["package"] == "Family - Ily (4 members)"
    assert member["monthly_installment"] == 450.0
    assert member["contract_duration"] == "24 months"
    assert "Family package" in member["custom_package_note"]
    assert "three children" in member["custom_package_note"]


def test_reception_cannot_add_a_member_on_a_custom_package(app, client):
    _sign_in(client, "reception")

    response = client.post("/members/add", data=_member_form(**CUSTOM))

    assert response.status_code == 200
    assert b"Only a manager" in response.data
    assert _member(app) is None


def test_manager_can_move_an_existing_member_to_a_custom_package(app, client):
    _sign_in(client, "manager")
    client.post("/members/add", data=_member_form(tariff="Premium 36 2026", monthly_installment="302.25"))
    member_id = _member(app)["id"]

    _edit(client, member_id, **CUSTOM)

    member = _member(app)
    assert member["tariff"] == "Custom"
    assert member["monthly_installment"] == 450.0
    assert member["contract_duration"] == "24 months"
    assert any("Custom package" in note for note in _notes(app, member_id))


def test_setting_a_custom_package_is_logged_with_the_amount_and_reason(app, client):
    _sign_in(client, "manager")
    client.post("/members/add", data=_member_form(**CUSTOM))
    member_id = _member(app)["id"]

    logged = [note for note in _notes(app, member_id) if "Custom package" in note]

    assert len(logged) == 1
    assert "R450.00" in logged[0]
    assert "24 months" in logged[0]
    assert "three children" in logged[0]


def test_reception_cannot_turn_a_standard_member_into_a_custom_one(app, client):
    _sign_in(client, "manager")
    client.post("/members/add", data=_member_form(tariff="Premium 36 2026", monthly_installment="302.25"))
    member_id = _member(app)["id"]

    _sign_in(client, "reception")
    response = _edit(client, member_id, **CUSTOM)

    assert b"Only a manager" in response.data
    member = _member(app)
    assert member["tariff"] == "Premium 36 2026"
    assert member["monthly_installment"] == 302.25
    assert member["contact"] != "0799999999"


@pytest.mark.parametrize(
    "posted",
    [
        {"tariff": "Custom", "monthly_installment": "1.00", "contract_duration": "1 months"},  # tampered
        {"tariff": "", "monthly_installment": "0", "contract_duration": "", "package": ""},     # wiped
    ],
)
def test_reception_editing_a_custom_member_cannot_change_or_wipe_the_package(app, client, posted):
    member_id = _custom_member(app, client)["id"]

    _sign_in(client, "reception")
    _edit(client, member_id, **posted)

    member = _member(app)
    assert member["contact"] == "0799999999"          # the edit itself went through
    assert member["tariff"] == "Custom"
    assert member["package"] == "Family - Ily (4 members)"
    assert member["monthly_installment"] == 450.0
    assert member["contract_duration"] == "24 months"
    assert "three children" in member["custom_package_note"]


@pytest.mark.parametrize(
    "bad",
    [
        {"custom_name": ""},
        {"custom_amount": ""},
        {"custom_amount": "abc"},
        {"custom_amount": "-5"},
        {"custom_months": "0"},
        {"custom_months": "x"},
        {"custom_type": "Something else"},
        {"custom_note": ""},
    ],
)
def test_an_incomplete_custom_package_is_refused(app, client, bad):
    _sign_in(client, "manager")

    response = client.post("/members/add", data=_member_form(**{**CUSTOM, **bad}))

    assert response.status_code == 200
    assert b"custom package" in response.data.lower()
    assert _member(app) is None


def test_moving_a_member_back_to_a_standard_tariff_clears_the_custom_reason(app, client):
    member_id = _custom_member(app, client)["id"]

    _edit(client, member_id, tariff="Premium 36 2026", monthly_installment="302.25",
          contract_duration="36 months", package="")

    member = _member(app)
    assert member["tariff"] == "Premium 36 2026"
    assert member["monthly_installment"] == 302.25
    assert not member["custom_package_note"]


def test_only_managers_are_offered_the_custom_option(app, client):
    _sign_in(client, "manager")
    assert b'value="Custom"' in client.get("/members/add").data

    _sign_in(client, "reception")
    assert b'value="Custom"' not in client.get("/members/add").data


def test_a_custom_member_shows_its_package_to_reception_but_only_a_manager_can_change_it(app, client):
    member_id = _custom_member(app, client)["id"]

    _sign_in(client, "reception")
    page = client.get(f"/members/{member_id}/edit").data

    assert b"Family - Ily (4 members)" in page
    assert b'id="customPackagePanel"' not in page


def test_the_member_page_shows_why_a_package_is_custom(app, client):
    member_id = _custom_member(app, client)["id"]

    _sign_in(client, "reception")
    page = client.get(f"/members/{member_id}").data

    assert b"Family - Ily (4 members)" in page
    assert b"three children" in page


def test_the_contract_names_the_custom_package_not_just_custom(app, client):
    member_id = _custom_member(app, client)["id"]

    _sign_in(client, "manager")
    page = client.get(f"/contracts/{member_id}").data

    assert b"Family - Ily (4 members)" in page


def test_the_elev8_sync_does_not_overwrite_a_custom_package(app, client):
    from onecpase.elev8_sync import sync_source_members

    _custom_member(app, client)
    roster = [{
        "unique_ref": "1234567", "id_number": ID_NUMBER, "first_name": "Fam", "last_name": "Ily",
        "contact": "0711111111", "join_date": "2024-01-01", "member_status": "Active",
        "package": "Premium 36 2026", "monthly_installment": 302.25,
    }]

    with app.app_context():
        db = get_db()
        sync_source_members(db, roster, "test")
        db.commit()

    member = _member(app)
    assert member["tariff"] == "Custom"
    assert member["package"] == "Family - Ily (4 members)"
    assert member["monthly_installment"] == 450.0
    assert member["contract_duration"] == "24 months"
    # Everything else still comes from the source roster.
    assert member["contact"] == "0711111111"
    assert member["itensity_ref"] == "1234567"
