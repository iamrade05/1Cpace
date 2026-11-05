"""Two people can pay, so a client can have more than one ID copy."""
import io
from pathlib import Path

import pytest

from onecpase.database import get_db
from onecpase.itensity_followups import documents_to_send
from auth_helpers import ensure_staff_account


@pytest.fixture
def member_id(app):
    with app.app_context():
        db = get_db()
        new_id = db.execute("INSERT INTO members(first_name,last_name,id_number) VALUES('Two','Payers','ID-COPIES-1')").lastrowid
        db.commit()
    return new_id


def _admin(client):
    ensure_staff_account(client, "admin")
    with client.session_transaction() as session:
        session.update(user_id=1, username="testuser", role="admin")


def _id_copies(app, member_id):
    with app.app_context():
        return [row["original_name"] for row in get_db().execute(
            "SELECT original_name FROM member_documents WHERE member_id=? AND document_type='SA ID Copy' ORDER BY id",
            (member_id,)).fetchall()]


def test_two_id_copies_at_the_same_time(app, client, member_id):
    _admin(client)

    response = client.post(f"/members/{member_id}/documents", data={
        "document_type": "SA ID Copy",
        "document": [(io.BytesIO(b"first"), "payer-one.pdf"), (io.BytesIO(b"second"), "payer-two.pdf")],
    })

    assert response.status_code == 302
    assert _id_copies(app, member_id) == ["payer-one.pdf", "payer-two.pdf"]


def test_two_id_copies_one_after_the_other(app, client, member_id):
    _admin(client)

    for name, body in (("payer-one.pdf", b"first"), ("payer-two.pdf", b"second")):
        assert client.post(f"/members/{member_id}/documents", data={
            "document_type": "SA ID Copy", "document": (io.BytesIO(body), name),
        }).status_code == 302

    assert _id_copies(app, member_id) == ["payer-one.pdf", "payer-two.pdf"]
    with app.app_context():
        stored = get_db().execute("SELECT stored_name FROM member_documents WHERE member_id=?", (member_id,)).fetchall()
    folder = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id)
    assert all((folder / row["stored_name"]).is_file() for row in stored)


def test_both_id_copies_go_to_itensity(app, client, member_id):
    _admin(client)
    client.post(f"/members/{member_id}/documents", data={
        "document_type": "SA ID Copy",
        "document": [(io.BytesIO(b"first"), "payer-one.pdf"), (io.BytesIO(b"second"), "payer-two.pdf")],
    })

    with app.app_context():
        documents = documents_to_send(get_db(), member_id)

    assert sorted(row["original_name"] for row in documents) == ["payer-one.pdf", "payer-two.pdf"]


def test_the_upload_boxes_let_you_pick_several_files(app, client, member_id):
    _admin(client)
    with app.app_context():
        db = get_db()
        application_id = db.execute("INSERT INTO membership_applications(member_id) VALUES(?)", (member_id,)).lastrowid
        db.commit()

    for url in (f"/members/{member_id}", f"/applications/{application_id}"):
        page = client.get(url, follow_redirects=True).get_data(as_text=True)
        assert 'name="document" required multiple' in page
