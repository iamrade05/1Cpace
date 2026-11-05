"""The Itensity driver in a real browser, against a local fake of Itensity's screens.

The fake copies the field names mapped from the real site. It proves how the
driver behaves, not that the real site still looks like this.
"""
import importlib.util
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

DRIVER_PATH = Path(__file__).resolve().parent.parent / "onecpase" / "itensity_driver" / "push_to_itensity.py"


@pytest.fixture(scope="module", autouse=True)
def needs_a_browser():
    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
    except PlaywrightError:
        pytest.skip("Chromium is not installed")


LOGIN = """<form method="post" action="/login"><input name="txt_email"><input name="password" type="password">
<input type="submit" value="Log in"></form>"""

ADD_MEMBER = """<form id="addmember" method="post" action="Scripts/AddMember.php">
<input name="txt_email"><input name="name"><input name="surname"><input name="dob"><input name="txt_mobile_tel">
<select name="gender"><option></option><option>Female</option><option>Male</option></select>
<button type="button">Next - Price Presenter</button>
<select id="pp_dur" name="pp_dur"><option></option><option>12 months</option><option>24 months</option></select>
<select id="pp_access" name="pp_access"><option></option><option>Local</option></select>
<select id="pp_pm" name="pp_pm" onchange="loadTariffs()"><option></option><option>Debit Order</option><option>PIF</option></select>
<select id="selectedTar" name="tarrif"><option></option></select>
<div id="cussamount"></div>
<button type="button">Next - Membership Details</button>
<input type="date" name="dt_start">
<button type="button">Next - Additional Details</button>
<input name="Fie4"><input type="radio" name="OptIn" id="OptInYes" value="1"><input type="radio" name="OptIn" id="OptInNo" value="0">
<button type="button">Next - PAR-Q</button>
<button type="button">Next - Payment Details</button>
<input type="radio" name="rad_payment" id="si_4"><input type="radio" name="payerd" id="payersame">
<select id="ddl_bank" name="ddl_bank"><option></option><option>Capitec</option><option>FNB/RMB</option></select>
<select name="txt_Type"><option></option><option>Savings</option><option>Cheque/Current</option></select>
<input name="txt_Responsible_Branch"><input name="txt_Responsible_Acc_NO"><input name="txt_Responsible_Init">
<input name="txt_Responsible_Surname"><input name="txt_Responsible_ID"><input name="txt_Responsible_Mobile">
<input name="txt_Responsible_DOB"><input name="txt_pay_email" %(pay_email)s>
<select name="ddl_Date"><option></option><option value="5">5th</option><option value="15">15th</option><option value="25">25th</option></select>
<select name="ddl_sales"><option></option><option>Staff Member</option><option>Staff Member Two</option></select>
<input type="hidden" name="g-recaptcha-response">
<div id="form_errors">%(form_errors)s</div><div id="form_message">%(form_message)s</div>
<input type="hidden" id="use_exist" value="%(use_exist)s"><input type="hidden" id="use_id_reserved"><input type="hidden" id="tt3_ref">
<div id="bvalid"><a href="javascript:ValidateBankAccount(false)">Bank details need to be checked, Check Now</a></div>
<input type="submit" id="btnSub" value="Submit">
</form>
<script>
const scenario = {bank: '%(bank)s', promotion: %(promotion)s, refuse: %(refuse)s};
var BankValid = false;
document.querySelector("input[name='txt_email']").addEventListener('change', () => fetch('/XML/validate_email.php', {method: 'POST'}));
function ValidateBankAccount(submit) {
  if (scenario.bank === 'duplicate' && !confirm('Duplicate Bank Account found! By clicking OK to proceed')) return;
  fetch('/XML/ValidateAccount.php', {method: 'POST'}).then(() => {
    const bvalid = document.getElementById('bvalid');
    if (scenario.bank === 'valid' || scenario.bank === 'duplicate') {
      BankValid = true;
      bvalid.innerHTML = 'Bank Details are Valid';
      if (submit) addValidation();
    } else if (scenario.bank === 'tt3') {
      document.getElementById('tt3_ref').value = 'R1';
    } else {
      bvalid.innerHTML = '<a href="javascript:ManualConfirmAVS()">Bank details are Invalid - Click to bypass AVS check</a>';
    }
  });
}
function ManualConfirmAVS() { BankValid = true; }
function addValidation() {
  if (!BankValid) return ValidateBankAccount(true);
  if (scenario.promotion && !confirm('You have not selected a promotion, is that correct?')) return;
  document.getElementById('addmember').submit();
}
document.getElementById('addmember').addEventListener('submit', (event) => {
  event.preventDefault();
  if (scenario.refuse) {
    document.getElementById('form_errors').innerHTML = '<h2>The following errors were detected</h2>';
    return;
  }
  addValidation();
});
function loadTariffs() {
  setTimeout(() => {
    const list = document.getElementById('selectedTar');
    list.innerHTML = '<option></option><option>Premium 24</option><option>Premium 24 Month 2026</option>';
    list.onchange = () => {
      document.getElementById('cussamount').innerHTML = '<input name="cussamount" value="334.75">';
    };
  }, 300);
}
</script>"""

PROFILE = """<div>PERSONAL DETAILS %(surname)s</div><ul>%(attachments)s</ul><div id="ModalInner"></div>
<script>
function UploadContract(kind) {
  document.getElementById('ModalInner').innerHTML = `<form method="post" enctype="multipart/form-data" action="Scripts/uploadpdf.php">
    <input name="name"><input type="hidden" name="use_id" value="%(use_id)s"><input type="file" name="fil_file" id="fil_file">
    <input type="submit" value="Upload (MAX:7MB)"></form>`;
}
</script>"""

PAYMENT = """<table><tr><th>Date</th><th>Description</th><th>Type</th><th>Amount</th><th>Balance</th></tr>%(rows)s</table>
<div id="ModalInner"></div>
<script>
function Get_form(kind) {
  const form = (action, types) => `<form id="Payment" method="post" action="Scripts/${action}.php?id=1527701">
    <input type="number" name="txt_Amount" step="0.01"><input name="txt_Rec"><input type="date" name="txt_Date">
    <select name="ddl_type">${types.map((t) => `<option>${t}</option>`).join('')}</select>
    <input type="submit" value="Insert"></form>`;
  document.getElementById('ModalInner').innerHTML = kind === 'fees'
    ? form('AddFees', ['Recurring', 'Once-Off', 'Credit'])
    : form('AddPayment', ['Debit Order', 'EFT', 'Cash', 'Card']);
}
</script>"""


class FakeItensity:
    def __init__(self):
        self.posts = []
        self.surname = "Lovelace"
        self.use_id = "1527701"
        self.attachments = []
        self.ledger = []
        self.detect_fee = True
        self.require_pay_email = False
        self.bank = "valid"
        self.promotion = False
        self.refuse = False
        self.use_exist = "0"
        self.form_errors = ""
        self.form_message = ""
        self.landing = "/profile.php?id=1527701"
        self.id_holder = ""
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body="", status=200, location=None):
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<!doctype html><html><body>{body}</body></html>".encode())

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/":
                    return self._send(LOGIN)
                if path == "/home":
                    return self._send("Welcome")
                if path == "/AddMembers.php":
                    return self._send(ADD_MEMBER % {
                        "pay_email": "required" if fake.require_pay_email else "",
                        "form_errors": fake.form_errors, "form_message": fake.form_message, "use_exist": fake.use_exist,
                        "bank": fake.bank, "promotion": str(fake.promotion).lower(), "refuse": str(fake.refuse).lower(),
                    })
                if path == "/saved":
                    return self._send("Member saved")
                if path == "/Scripts/select.php":
                    return self._send(status=302, location="/profile.php")
                if path == "/Scripts/select_pay.php":
                    return self._send(status=302, location="/payment.php")
                if path == "/profile.php":
                    return self._send(PROFILE % {
                        "surname": fake.surname, "use_id": fake.use_id,
                        "attachments": "".join(f"<li>{title}</li>" for title in fake.attachments),
                    })
                if path == "/payment.php":
                    return self._send(PAYMENT % {"rows": "".join(
                        f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>R {r[3]:.2f}</td><td>R 0.00</td></tr>"
                        for r in fake.ledger
                    )})
                return self._send(status=404)

            def do_POST(self):
                path = urlsplit(self.path).path
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if path == "/login":
                    return self._send(status=302, location="/home")
                if path.endswith("validate_email.php"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(('{"act":"false","user":"%s"}' % fake.id_holder).encode())
                    return None
                if path.endswith("ValidateAccount.php"):
                    fake.posts.append(("bank", None))
                    return self._send("{}")
                if path.endswith("uploadpdf.php"):
                    fake.posts.append(("upload", None))
                    fake.attachments.append(re.search(rb'name="name"\r\n\r\n(.*?)\r\n', raw).group(1).decode())
                    return self._send(status=302, location="/profile.php")
                form = {key: values[0] for key, values in parse_qs(raw.decode()).items()}
                if path.endswith("AddMember.php"):
                    fake.posts.append(("member", form))
                    fake.use_exist = fake.use_id
                    return self._send(status=302, location=fake.landing)
                if path.endswith("AddFees.php"):
                    fake.posts.append(("fee", form))
                    fake.ledger.append((form["txt_Date"], form["txt_Rec"], "Once-Off Fee", float(form["txt_Amount"])))
                    return self._send(status=302, location="/payment.php")
                if path.endswith("AddPayment.php"):
                    fake.posts.append(("payment", form))
                    if fake.detect_fee:
                        fake.ledger.append((form["txt_Date"], "joining fee", "Once-Off Fee", float(form["txt_Amount"])))
                    fake.ledger.append((form["txt_Date"], "Payment", "Card Payment", -float(form["txt_Amount"])))
                    return self._send(status=302, location="/payment.php")
                return self._send(status=404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def kinds(self):
        return [kind for kind, _ in self.posts]


@pytest.fixture
def fake():
    server = FakeItensity()
    yield server
    server.server.shutdown()
    server.server.server_close()


@pytest.fixture
def driver(fake, monkeypatch):
    spec = importlib.util.spec_from_file_location("itensity_driver_browser_test", DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ITENSITY_URL", fake.url)
    monkeypatch.setattr(module, "ITENSITY_USER", "tester")
    monkeypatch.setattr(module, "ITENSITY_PASS", "secret")
    # Keep the driver's real order of steps without its long, site-paced waits.
    real_wait = Page.wait_for_timeout
    monkeypatch.setattr(Page, "wait_for_timeout", lambda self, ms: real_wait(self, min(ms, 700)))
    return module


def _member(**changes):
    member = {
        "id": 7, "first_name": "Ada", "last_name": "Lovelace", "id_number": "9001010000080",
        "contact": "082 123 4567", "email": "", "join_date": "2026-10-30", "tariff": "Premium 24 2026",
        "contract_duration": "24 months", "payment_type": "Debit Order", "sales_person": "Staff Member",
        "bank": "Capitec", "account_number": "123456", "branch_code": "470010", "account_type": "Savings",
        "debit_order_date": "25", "date_of_birth": "1990-01-01", "gender": "Female",
        "monthly_installment": 334.75, "opt_in": True,
    }
    member.update(changes)
    return member


def test_preview_fills_everything_and_never_submits(fake, driver):
    report = driver.push(_member(), headless=True, submit=False)

    assert fake.posts == []
    assert report["wrong"] == []
    assert report["invalid_required"] == []
    assert report["stop_reason"] is None
    assert report["submit_button_found"] is True
    assert report["recaptcha_on_form"] is True
    assert {"Tariff", "Monthly amount", "Debit date", "Account number", "Sales representative"} <= set(report["checked"])


def test_a_tariff_itensity_does_not_offer_stops_the_run(fake, driver):
    with pytest.raises(driver.ItensityPushError) as stopped:
        driver.push(_member(tariff="Black Friday Promo"), headless=True, submit=True)

    assert "tariff" in str(stopped.value)
    assert stopped.value.submitted is False
    assert fake.posts == []


def test_a_near_match_is_never_accepted(fake, driver):
    # 'Staff Member' must not be picked for someone merely sharing part of the name.
    with pytest.raises(driver.ItensityPushError):
        driver.push(_member(sales_person="Staff"), headless=True, submit=True)
    with pytest.raises(driver.ItensityPushError):
        driver.push(_member(debit_order_date="2"), headless=True, submit=True)
    assert fake.posts == []


def test_an_empty_required_field_stops_before_submit(fake, driver):
    fake.require_pay_email = True
    with pytest.raises(driver.ItensityPushError) as stopped:
        driver.push(_member(), headless=True, submit=True)

    assert "txt_pay_email" in str(stopped.value)
    assert stopped.value.submitted is False
    assert fake.posts == []


def test_submit_sends_the_right_values_once_and_returns_the_reference(fake, driver):
    captured = {}
    driver._save_itensity_ref = lambda member_id, ref: captured.update(ref=ref)

    assert driver.push(_member(), headless=True, submit=True) == "1527701"

    assert captured["ref"] == "1527701"
    assert fake.kinds() == ["bank", "member"]
    sent = fake.posts[1][1]
    assert sent["tarrif"] == "Premium 24 Month 2026"
    assert sent["ddl_Date"] == "25"
    assert sent["pp_access"] == "Local"
    assert sent["dt_start"] == "2026-10-30"
    assert sent["cussamount"] == "334.75"
    assert sent["txt_Responsible_Acc_NO"] == "123456"


@pytest.mark.parametrize("use_exist, form_errors, form_message", [
    ("1", "", "<h2>Personal details on record</h2>"),
    ("1527701", "<h2>Active Member</h2>", ""),
])
def test_someone_already_on_record_stops_before_the_bank_check(fake, driver, use_exist, form_errors, form_message):
    fake.use_exist, fake.form_errors, fake.form_message = use_exist, form_errors, form_message
    with pytest.raises(driver.ItensityPushError) as stopped:
        driver.push(_member(), headless=True, submit=True)

    assert stopped.value.submitted is False
    assert fake.posts == []


def test_invalid_bank_details_stop_and_the_bypass_is_never_taken(fake, driver):
    fake.bank = "invalid"
    with pytest.raises(driver.ItensityPushError) as stopped:
        driver.push(_member(), headless=True, submit=True)

    assert "invalid" in str(stopped.value)
    assert stopped.value.submitted is False
    assert fake.kinds() == ["bank"]


def test_a_shared_bank_account_is_not_agreed_on_the_clients_behalf(fake, driver):
    fake.bank = "duplicate"
    with pytest.raises(driver.ItensityPushError) as stopped:
        driver.push(_member(), headless=True, submit=True)

    assert "Duplicate Bank Account" in str(stopped.value)
    assert stopped.value.submitted is False
    assert fake.posts == []


def test_waiting_for_the_banks_approval_stops_without_saving(fake, driver):
    fake.bank = "tt3"
    with pytest.raises(driver.ItensityPushError) as stopped:
        driver.push(_member(), headless=True, submit=True)

    assert "approve" in str(stopped.value)
    assert stopped.value.submitted is False
    assert fake.kinds() == ["bank"]


def test_the_no_promotion_question_is_answered_and_the_member_saved(fake, driver):
    fake.promotion = True
    assert driver.push(_member(), headless=True, submit=True) == "1527701"
    assert fake.kinds() == ["bank", "member"]


def test_a_form_itensity_refuses_is_reported_as_not_saved(fake, driver):
    fake.refuse = True
    with pytest.raises(driver.ItensityPushError) as stopped:
        driver.push(_member(), headless=True, submit=True)

    assert stopped.value.submitted is False
    assert fake.kinds() == ["bank"]


def test_the_reference_comes_from_the_id_check_when_the_landing_page_has_none(fake, driver):
    fake.landing = "/saved"
    assert driver.push(_member(), headless=True, submit=True) == "1527701"
    assert fake.kinds() == ["bank", "member"]


def test_the_id_check_reads_an_active_members_reference_from_the_reply(fake, driver):
    # Itensity leaves use_exist at 0 for an active member.
    fake.id_holder = "2182011"
    with driver.itensity_session(headless=True) as page:
        assert driver.ref_from_id_check(page, "9001010000000") == "2182011"


def test_the_id_check_finds_nobody_for_a_new_client(fake, driver):
    with driver.itensity_session(headless=True) as page:
        assert driver.ref_from_id_check(page, "9001010000000") is None


def test_a_reference_for_someone_else_is_not_accepted(fake, driver):
    fake.surname = "Somebody"
    with pytest.raises(driver.ItensityPushError) as stopped:
        driver.push(_member(), headless=True, submit=True)

    assert "1527701" in str(stopped.value)
    assert stopped.value.submitted is True
    assert fake.kinds() == ["bank", "member"]


def test_attaching_twice_uploads_once(fake, driver, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    with driver.itensity_session(headless=True) as page:
        first = driver.attach_document(page, "1527701", "Lovelace", str(pdf), "SA ID Copy 1C-9", "compliance")
        second = driver.attach_document(page, "1527701", "Lovelace", str(pdf), "SA ID Copy 1C-9", "compliance")

    assert (first, second) == ("sent", "already_attached")
    assert fake.kinds() == ["upload"]


def test_a_document_never_lands_on_someone_elses_profile(fake, driver, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    with driver.itensity_session(headless=True) as page:
        with pytest.raises(driver.ItensityPushError) as wrong_person:
            driver.attach_document(page, "1527701", "Somebody", str(pdf), "SA ID Copy 1C-9", "compliance")
        fake.use_id = "999"
        with pytest.raises(driver.ItensityPushError) as wrong_form:
            driver.attach_document(page, "1527701", "Lovelace", str(pdf), "SA ID Copy 1C-9", "compliance")

    assert wrong_person.value.submitted is False
    assert wrong_form.value.submitted is False
    assert fake.posts == []


def test_reg_fee_is_paid_once_and_reports_detection(fake, driver):
    with driver.itensity_session(headless=True) as page:
        first = driver.record_reg_fee(page, "1527701", "Lovelace", 200.0, "1C-5", "2026-10-30", "Card")
        second = driver.record_reg_fee(page, "1527701", "Lovelace", 200.0, "1C-5", "2026-10-30", "Card")

    assert first == {"status": "sent", "fee_detected": True}
    assert second["status"] == "already_in_itensity"
    assert fake.kinds() == ["payment"]
    sent = fake.posts[0][1]
    assert (sent["txt_Amount"], sent["txt_Rec"], sent["ddl_type"]) == ("200.00", "1C-5", "Card")


def test_a_fee_already_on_the_account_stops_the_payment(fake, driver):
    fake.ledger.append(("2026-10-29", "card fee\nStaff Member", "Once-Off Fee", 200.0))
    with driver.itensity_session(headless=True) as page:
        result = driver.record_reg_fee(page, "1527701", "Lovelace", 200.0, "1C-5", "2026-10-30", "Card")

    assert result["status"] == "already_in_itensity"
    assert fake.posts == []


def test_payment_reports_when_itensity_did_not_detect_the_fee(fake, driver):
    fake.detect_fee = False
    with driver.itensity_session(headless=True) as page:
        result = driver.record_reg_fee(page, "1527701", "Lovelace", 200.0, "1C-5", "2026-10-30", "Card")

    assert result == {"status": "sent", "fee_detected": False}


def test_fee_can_be_loaded_before_the_payment(fake, driver):
    fake.detect_fee = False
    with driver.itensity_session(headless=True) as page:
        result = driver.record_reg_fee(page, "1527701", "Lovelace", 200.0, "1C-5", "2026-10-30", "Card",
                                       add_fee_first=True)

    assert result == {"status": "sent", "fee_detected": True}
    assert fake.kinds() == ["fee", "payment"]
    assert fake.posts[0][1]["ddl_type"] == "Once-Off"


def test_html_becomes_a_real_pdf(driver, tmp_path):
    output = tmp_path / "contract.pdf"
    driver.html_to_pdf("<html><body><h1>Signed contract</h1></body></html>", str(output))
    assert output.read_bytes().startswith(b"%PDF")
