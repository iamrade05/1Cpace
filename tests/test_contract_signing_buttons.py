"""The on-screen signature buttons on the contract page.

A label placed in an inline onclick with |tojson puts double quotes inside a
double-quoted attribute, which ends the attribute early: the handler is cut off
("Unexpected end of input") and the signature pad never opens. The buttons pass
their values through data attributes instead.
"""
from html.parser import HTMLParser

from onecpase.database import get_db


class _SignButtons(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "button" and "btn-sign" in (dict(attrs).get("class") or ""):
            self.buttons.append(dict(attrs))


def _contract_page(app, client):
    with app.app_context():
        db = get_db()
        member_id = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number,
                   contact, member_status)
               VALUES ('ELE-0000000001', 'Sig', 'Test', 'ID0001', '0712345678', 'Active')"""
        ).lastrowid
        db.commit()
    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": "admin"})
    return client.get(f"/contracts/{member_id}").get_data(as_text=True)


def test_sign_buttons_are_well_formed_and_carry_their_signer(app, client):
    parser = _SignButtons()
    parser.feed(_contract_page(app, client))

    assert len(parser.buttons) == 2  # member and representative (no witness on this contract)
    for attrs in parser.buttons:
        # A stray attribute name means a quote inside a value ended it early.
        assert set(attrs) <= {"type", "class", "data-role", "data-label", "onclick"}, attrs
        assert attrs["onclick"].startswith("openSigPad(") and attrs["onclick"].endswith(")")
    by_role = {attrs["data-role"]: attrs["data-label"] for attrs in parser.buttons}
    assert by_role == {
        "member": "Member Signature & Date",
        "representative": "Gym Representative Signature & Date",
    }
