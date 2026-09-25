"""On-screen contract signing: a tablet/mobile signature pad captured per
signer (member/representative/witness), rendered at a fixed 40mm physical
size, plus the pre-existing remote approve-by-link flow it shares its
"mark the contract signed" logic with.
"""
import base64

from onecpase.database import get_db


def _login_as_admin(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["role"] = "admin"


def _member_id(app):
    with app.app_context():
        db = get_db()
        mid = db.execute(
            "INSERT INTO members (first_name, last_name, id_number) VALUES ('Sign', 'Test', 'SIGN-TEST-1')"
        ).lastrowid
        db.commit()
        return mid


def _fake_png_data_url(size=500):
    # The route checks size and the data: prefix, not real PNG structure -
    # any blob of a realistic signature-sized length is enough here.
    return "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"x" * size).decode()


def _encrypted_member_id(app, id_number="8001015009087"):
    """A member inserted the way the real app stores one - id_number
    encrypted at rest, not plain text - unlike _member_id() above."""
    from onecpase.encryption import encrypt_member

    with app.app_context():
        db = get_db()
        data = encrypt_member({"first_name": "Encrypted", "last_name": "IdTest", "id_number": id_number})
        mid = db.execute(
            "INSERT INTO members (first_name, last_name, id_number, id_number_hash) VALUES (?, ?, ?, ?)",
            (data["first_name"], data["last_name"], data["id_number"], data["id_number_hash"]),
        ).lastrowid
        db.commit()
        return mid


def test_the_staff_contract_page_shows_the_real_id_number_not_ciphertext(app):
    # view_contract() used to pass the raw DB row straight to the template,
    # unlike public_share()'s render_contract() call which already decrypts -
    # so the "SA ID Number" field printed/signed on the staff page was the
    # encrypted ciphertext, not a number a person could read or a client
    # could confirm before signing.
    id_number = "8001015009087"
    mid = _encrypted_member_id(app, id_number)
    with app.test_client() as client:
        _login_as_admin(client)
        response = client.get(f"/contracts/{mid}")
        html = response.get_data(as_text=True)
        assert id_number in html
        assert "enc:" not in html


def test_the_contract_shows_the_tenants_own_logo(app):
    # The contract template had no logo at all before - the app already has
    # a tenant-branding convention (tenant.logo_filename, static/tenant_logos/
    # <slug>.svg) used on login.html/join.html; render_contract() now resolves
    # the current tenant (g.tenant, or a g.tenant_slug lookup) and the
    # template follows the same convention.
    from flask import g
    from onecpase.contracts import render_contract

    mid = _member_id(app)
    with app.test_request_context(f"/contracts/{mid}"):
        db = get_db()
        member = db.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        g.tenant = {"slug": "elev8", "name": "Elev8 Health and Fitness Gym", "logo_filename": "elev8.svg"}
        html = render_contract(None, dict(member))
    assert '<img src="/static/tenant_logos/elev8.svg"' in html
    assert 'alt="Elev8 Health and Fitness Gym logo"' in html


def test_no_logo_is_shown_when_there_is_no_tenant(app):
    # No g.tenant and no g.tenant_slug (e.g. single-tenant install, or the
    # cookie hasn't been set yet) - must render cleanly with no logo, not a
    # broken image or a Jinja error.
    from onecpase.contracts import render_contract

    mid = _member_id(app)
    with app.test_request_context(f"/contracts/{mid}"):
        db = get_db()
        member = db.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        html = render_contract(None, dict(member))
    assert "tenant_logos" not in html


def test_signing_is_offered_on_the_staff_contract_page(app):
    mid = _member_id(app)
    with app.test_client() as client:
        _login_as_admin(client)
        response = client.get(f"/contracts/{mid}")
        assert response.status_code == 200
        assert b"Sign on Screen" in response.data
        assert b"Sign here" in response.data  # the signature pad modal


def test_saving_a_member_signature_marks_the_contract_signed(app):
    mid = _member_id(app)
    with app.test_client() as client:
        _login_as_admin(client)
        response = client.post(
            f"/contracts/{mid}/sign",
            json={"signer_role": "member", "image_data": _fake_png_data_url()},
        )
        assert response.status_code == 200
        assert response.get_json()["ok"] is True

        with app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT signer_role, image_filename FROM contract_signatures WHERE member_id=?", (mid,)
            ).fetchone()
            assert row["signer_role"] == "member"
            assert row["image_filename"].endswith(".png")

            checklist = db.execute(
                """SELECT cc.contract_status FROM compliance_checklists cc
                   JOIN membership_applications ma ON ma.id = cc.application_id
                   WHERE ma.member_id = ?""", (mid,),
            ).fetchone()
            assert checklist["contract_status"] == "signed"

        # The saved signature is now servable and shows on the contract page.
        image_response = client.get(f"/contracts/{mid}/signature/member")
        assert image_response.status_code == 200
        assert image_response.mimetype == "image/png"

        page = client.get(f"/contracts/{mid}")
        assert b"width: 40mm" in page.data  # the signature's fixed physical size
        assert b'src="/contracts/%d/signature/member"' % mid in page.data
        assert b"Re-sign" in page.data


def test_representative_signature_does_not_touch_the_contract_status(app):
    """Only the member's own signature is proof the contract was signed -
    a staff witness signing does not, on its own, complete that gate."""
    mid = _member_id(app)
    with app.test_client() as client:
        _login_as_admin(client)
        response = client.post(
            f"/contracts/{mid}/sign",
            json={"signer_role": "representative", "image_data": _fake_png_data_url()},
        )
        assert response.get_json()["ok"] is True
        with app.app_context():
            # A representative signature alone must not conjure up an
            # application/checklist - only a member signature does that.
            application = get_db().execute(
                "SELECT id FROM membership_applications WHERE member_id=?", (mid,)
            ).fetchone()
            assert application is None


def test_an_unknown_signer_role_is_rejected(app):
    mid = _member_id(app)
    with app.test_client() as client:
        _login_as_admin(client)
        response = client.post(
            f"/contracts/{mid}/sign",
            json={"signer_role": "manager", "image_data": _fake_png_data_url()},
        )
        assert response.status_code == 400


def test_a_blank_or_missing_signature_is_rejected(app):
    mid = _member_id(app)
    with app.test_client() as client:
        _login_as_admin(client)
        response = client.post(
            f"/contracts/{mid}/sign",
            json={"signer_role": "member", "image_data": _fake_png_data_url(size=10)},
        )
        assert response.status_code == 400
        with app.app_context():
            count = get_db().execute(
                "SELECT COUNT(*) FROM contract_signatures WHERE member_id=?", (mid,)
            ).fetchone()[0]
            assert count == 0


def test_signature_image_404s_when_nothing_has_been_signed(app):
    mid = _member_id(app)
    with app.test_client() as client:
        _login_as_admin(client)
        assert client.get(f"/contracts/{mid}/signature/member").status_code == 404


# ── Remote approve-by-link flow shares the same "mark signed" logic ─────────
# Regression: the approval branch previously ran
# `UPDATE membership_applications SET contract_status=...`, a column that
# only exists on compliance_checklists - every real approval click raised
# sqlite3.OperationalError. Never caught because this code path had no test
# coverage at all before this file. Exercised directly (rather than through
# the full tenant-slug share URL) since that only needs platform-db tenant
# setup that's incidental to what's actually being proven here.

def test_mark_contract_signed_does_not_reference_a_nonexistent_column(app):
    from onecpase.contracts import _mark_contract_signed

    mid = _member_id(app)
    with app.test_request_context():
        db = get_db()
        _mark_contract_signed(db, mid)  # must not raise sqlite3.OperationalError
        db.commit()

        checklist = db.execute(
            """SELECT cc.contract_status FROM compliance_checklists cc
               JOIN membership_applications ma ON ma.id = cc.application_id
               WHERE ma.member_id = ?""", (mid,),
        ).fetchone()
        assert checklist["contract_status"] == "signed"
