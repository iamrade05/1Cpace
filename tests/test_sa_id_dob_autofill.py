"""SA ID number -> date of birth autofill on the member and prospect-conversion forms.

The logic lives in one browser script (static/js/sa_id.js) shared by the forms,
so it is exercised here through Node rather than by string-matching templates.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from onecpase.database import get_db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "onecpase" / "static" / "js" / "sa_id.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _node(body):
    """Run *body* (a JS function body that returns a value) with sa_id.js loaded.

    ``today`` is fixed at 2026-09-25 and ``field(value)`` builds a stand-in for a
    date input that records the listener the script attaches to it.
    """
    program = (
        "const vm = require('vm'), fs = require('fs');"
        "const window = {};"
        f"vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPT))}, 'utf8'), {{ window }});"
        "const today = new Date(2026, 8, 25);"
        "const field = (value) => ({ value, dataset: {}, addEventListener(t, fn) { this.onInput = fn; } });"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
@pytest.mark.parametrize(
    "id_number, expected",
    [
        ("9001015800086", "1990-01-01"),   # a full ID
        ("900101", "1990-01-01"),          # the first six digits are enough
        ("0503155800086", "2005-03-15"),   # 2000s
        ("260925", "2026-09-25"),          # born today
        ("261231", "1926-12-31"),          # 2026-12-31 is in the future, so the 1900s
        ("000229", "2000-02-29"),          # a real leap day
        ("950231", None),                  # there is no 31 February
        ("010229", None),                  # neither 2001 nor 1901 had a 29 February
        ("951301", None),                  # there is no month 13
        ("90010", None),                   # fewer than six digits
        ("M0102030", None),                # a passport: letters mean it is not an SA ID
        ("", None),
    ],
)
def test_birth_date_comes_from_the_first_six_digits(id_number, expected):
    assert _node(f"return window.saIdBirthDate({json.dumps(id_number)}, today);") == expected


@needs_node
def test_typing_an_id_fills_an_empty_date_of_birth():
    body = "const f = field(''); window.saIdFillDob(f, '1990-01-01', true); return f.value;"
    assert _node(body) == "1990-01-01"


@needs_node
def test_page_load_never_overwrites_a_stored_date_of_birth():
    body = "const f = field('1985-06-15'); window.saIdFillDob(f, '1990-01-01', false); return f.value;"
    assert _node(body) == "1985-06-15"


@needs_node
def test_page_load_still_fills_a_blank_date_of_birth():
    body = "const f = field(''); window.saIdFillDob(f, '1990-01-01', false); return f.value;"
    assert _node(body) == "1990-01-01"


@needs_node
def test_correcting_the_id_updates_the_date_it_filled_in():
    body = (
        "const f = field('');"
        "window.saIdFillDob(f, '1990-01-01', true);"
        "window.saIdFillDob(f, '1991-01-01', true);"
        "return f.value;"
    )
    assert _node(body) == "1991-01-01"


@needs_node
def test_a_date_the_user_typed_is_not_overwritten():
    body = (
        "const f = field('');"
        "window.saIdFillDob(f, '1990-01-01', true);"
        "f.value = '1980-05-05'; f.onInput();"
        "window.saIdFillDob(f, '1991-01-01', true);"
        "return f.value;"
    )
    assert _node(body) == "1980-05-05"


@needs_node
def test_clearing_a_typed_date_lets_the_id_fill_it_again():
    body = (
        "const f = field('');"
        "window.saIdFillDob(f, '1990-01-01', true);"
        "f.value = '1980-05-05'; f.onInput();"
        "f.value = ''; f.onInput();"
        "window.saIdFillDob(f, '1991-01-01', true);"
        "return f.value;"
    )
    assert _node(body) == "1991-01-01"


# ── The forms actually load and use the script ───────────────────────────────

def _login_as_admin(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "testuser"
        sess["role"] = "admin"


def test_add_member_page_uses_the_shared_script(app):
    with app.test_client() as client:
        _login_as_admin(client)

        page = client.get("/members/add").data

    assert b"js/sa_id.js" in page
    assert b"extractFromId(this.value, true)" in page


def test_edit_member_page_uses_the_shared_script(app):
    with app.app_context():
        db = get_db()
        member_id = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number,
                   contact, member_status)
               VALUES ('ELE-0000000077', 'Edit', 'Member', 'ID0077', '0710000077', 'Active')"""
        ).lastrowid
        db.commit()
    with app.test_client() as client:
        _login_as_admin(client)

        page = client.get(f"/members/{member_id}/edit").data

    assert b"js/sa_id.js" in page
    assert b"extractFromId(this.value, true)" in page


def test_prospect_conversion_form_fills_the_date_of_birth_from_the_id():
    source = (ROOT / "onecpase" / "templates" / "leads" / "convert.html").read_text(encoding="utf-8")

    assert "js/sa_id.js" in source
    assert 'id="dobField"' in source
    assert "saIdFillDob" in source
