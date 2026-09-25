"""The contract page: view it, then sign on screen, or print and attach the signed copy.

Staff open it in the same window as the app (not a new tab), and the three ways to
get it signed sit together on the page with a line saying where it stands.
"""
import base64
import io
import os
import re
import struct
import zlib
from html.parser import HTMLParser

from onecpase.applications import ensure_member_application
from onecpase.database import get_db


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms, self.links, self.buttons = [], [], []
        self._form = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._form = {"attrs": attrs, "inputs": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None:
            self._form["inputs"].append(attrs)
        elif tag == "a":
            self.links.append(attrs)
        elif tag == "button":
            self.buttons.append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None


def _parse(html):
    page = _Page()
    page.feed(html)
    return page


def _member(app):
    with app.app_context():
        db = get_db()
        member_id = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number,
                   contact, member_status)
               VALUES ('ELE-0000000001', 'Page', 'Options', 'ID0001', '0712345678', 'Active')"""
        ).lastrowid
        db.commit()
    return member_id


def _login(client):
    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": "admin"})


def _contract(client, member_id):
    return client.get(f"/contracts/{member_id}").get_data(as_text=True)


def _png_data_url(size=64):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + os.urandom(size * 3) for _ in range(size))  # noise, so it is not tiny
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def test_the_contract_page_offers_sign_print_and_attach_together(app, client):
    member_id = _member(app)
    _login(client)

    html = _contract(client, member_id)
    page = _parse(html)

    assert "Sign on screen" in html and "Print" in html and "Attach signed copy" in html
    # the toolbar button and the member's signature block both open the member's pad
    assert sum(1 for b in page.buttons if b.get("data-role") == "member") == 2
    forms = [f for f in page.forms if f["attrs"].get("action") == f"/members/{member_id}/documents"]
    assert len(forms) == 1
    assert forms[0]["attrs"].get("enctype") == "multipart/form-data"
    fields = {i.get("name"): i for i in forms[0]["inputs"]}
    assert fields["document_type"]["value"] == "Signed Contract"
    assert fields["return_to"]["value"] == f"/contracts/{member_id}"
    assert fields["document"]["type"] == "file"
    assert "csrf_token" in fields   # the page is standalone, so nothing adds this for it


def test_a_member_on_the_shared_link_is_not_offered_staff_signing_or_attaching(app, client):
    member_id = _member(app)
    _login(client)
    shared = client.post(
        f"/contracts/{member_id}/share", data={"channel": "link"}, follow_redirects=True
    ).get_data(as_text=True)
    path = re.search(r"/contracts/share/[\w-]+/[\w-]+", shared).group(0)
    with client.session_transaction() as sess:
        sess.clear()

    html = client.get(path).get_data(as_text=True)

    assert "Attach signed copy" not in html
    assert "Sign on screen" not in html
    assert not any(f["attrs"].get("enctype") for f in _parse(html).forms)


def test_the_contract_opens_in_the_same_window_from_the_member_and_application_pages(app, client):
    member_id = _member(app)
    with app.test_request_context():
        db = get_db()
        application_id = ensure_member_application(db, member_id)
        db.commit()
    _login(client)

    for url in (f"/members/{member_id}", f"/applications/{application_id}"):
        page = _parse(client.get(url).get_data(as_text=True))
        contract_links = [a for a in page.links if a.get("href") == f"/contracts/{member_id}"]
        assert contract_links, url
        assert all("target" not in link for link in contract_links), url


def test_the_page_says_it_is_not_signed_yet(app, client):
    member_id = _member(app)
    _login(client)

    assert "Not signed yet" in _contract(client, member_id)


def test_attaching_a_signed_copy_signs_the_contract_and_comes_back_to_it(app, client):
    member_id = _member(app)
    _login(client)

    response = client.post(
        f"/members/{member_id}/documents",
        data={
            "document_type": "Signed Contract",
            "return_to": f"/contracts/{member_id}",
            "document": (io.BytesIO(b"%PDF-1.4 signed by hand"), "signed-by-hand.pdf"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/contracts/{member_id}")
    html = _contract(client, member_id)
    assert "Contract signed" in html
    assert "Not signed yet" not in html
    assert "signed-by-hand.pdf" in html          # the attached copy is listed
    assert "uploaded successfully" in html       # the upload's message is shown here, not left for the next page


def test_a_contract_signed_on_screen_says_so(app, client):
    member_id = _member(app)
    _login(client)

    saved = client.post(
        f"/contracts/{member_id}/sign", json={"signer_role": "member", "image_data": _png_data_url()}
    )

    assert saved.get_json()["ok"] is True
    html = _contract(client, member_id)
    assert "Contract signed" in html
    assert "signed on screen" in html
