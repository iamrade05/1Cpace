from datetime import date, datetime, timezone
import sqlite3

import pytest

from onecpase.database import get_db
from onecpase.staff_kpi import (
    SAST, business_day, build_daily_scorecard, ensure_schema, record_authentication,
    record_ptp_channel, selected_day,
)


def staff(db, name, department="Reception"):
    return db.execute("""INSERT INTO users(username,password_hash,full_name,role,department,active)
        VALUES(?,'x',?,'staff',?,1)""", (name, name, department)).lastrowid


def actuals(report):
    return {row["id"]: {cell["key"]: cell["actual"] for cell in row["cells"]} for row in report["rows"]}


@pytest.mark.parametrize("department,scope", [("Reception", "reception"), ("Sales", "sales")])
def test_daily_targets_exclude_managers_but_keep_department_staff(app, department, scope):
    with app.app_context():
        db = get_db()
        worker = staff(db, "Target staff", department)
        for role in ("manager", "sales_manager", "admin"):
            uid = staff(db, "Target excluded " + role, department)
            db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
            assert build_daily_scorecard(db, scope, "2026-10-28", user_id=uid)["rows"] == []
        assert [row["id"] for row in build_daily_scorecard(db, scope, "2026-10-28")["rows"]] == [worker]


def mandate(db, uid, status="pending"):
    member_id = new_member(db)
    return db.execute("""INSERT INTO debicheck_mandates(member_id,merchant_id,client_ref1,client_ref2,
        account_name,account_type,account_number,branch_code,submit_date,submitted_by,status)
        VALUES(?,'test','test','test','K Member','Savings','0000000000','000000','2026-10-26',?,?)""", (member_id,uid,status)).lastrowid


def new_member(db):
    suffix = db.execute("SELECT COALESCE(MAX(id),0)+1 FROM members").fetchone()[0]
    return db.execute("INSERT INTO members(first_name,last_name,id_number) VALUES('KPI','Member',?)", (f"synthetic-{suffix}",)).lastrowid


def test_first_authentication_counts_once_and_credits_submitter_not_checker(app):
    with app.app_context():
        db = get_db()
        uid = staff(db, "Reception owner")
        other = staff(db, "Sales checker", "Sales")
        mid = mandate(db, uid)
        pending = mandate(db, uid, "submitted")
        record_authentication(db, pending, "submitted", at=datetime(2026, 10, 25, 23, tzinfo=timezone.utc))
        # No membership application is required to record arrears mandates.
        from onecpase.debicheck import sync_debicheck_to_application
        db.execute("UPDATE debicheck_mandates SET status='approved' WHERE id=?", (mid,))
        sync_debicheck_to_application(db, mid, actor_id=other)
        assert db.execute("SELECT kpi_authenticated_at FROM debicheck_mandates WHERE id=?", (mid,)).fetchone()[0]
        db.execute("UPDATE debicheck_mandates SET kpi_authenticated_at='2026-10-25T23:00:00+00:00' WHERE id=?", (mid,))
        for status in ("approved", "pending", "active", "completed"):
            record_authentication(db, mid, status, at=datetime(2026, 10, 27, tzinfo=timezone.utc))
        assert actuals(build_daily_scorecard(db, "reception", "2026-10-26"))[uid]["debicheck"] == 1
        assert actuals(build_daily_scorecard(db, "reception", "2026-10-27"))[uid]["debicheck"] == 0
        assert actuals(build_daily_scorecard(db, "sales", "2026-10-26"))[other]["joined"] == 0


def test_migration_does_not_fabricate_historical_authentication_or_reset_custom_targets():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""CREATE TABLE debicheck_mandates(id INTEGER PRIMARY KEY,status TEXT);
        INSERT INTO debicheck_mandates VALUES(1,'Active'),(2,'Pending Authorisation');
        CREATE TABLE ptp_agreements(id INTEGER PRIMARY KEY);
        CREATE TABLE collection_rules(rule_key TEXT,rule_value TEXT);
        INSERT INTO collection_rules VALUES('daily_call_target','80');""")
    ensure_schema(db, "sqlite")
    assert tuple(db.execute("SELECT kpi_authentication_seen,kpi_authenticated_at FROM debicheck_mandates WHERE id=1").fetchone()) == (1, None)
    record_authentication(db, 1, "approved")
    assert db.execute("SELECT kpi_authenticated_at FROM debicheck_mandates WHERE id=1").fetchone()[0] is None
    assert db.execute("SELECT rule_value FROM collection_rules").fetchone()[0] == "100"
    db.execute("UPDATE collection_rules SET rule_value='125'")
    ensure_schema(db, "sqlite")
    assert db.execute("SELECT rule_value FROM collection_rules").fetchone()[0] == "125"
    record_authentication(db, 2, "approved")
    assert db.execute("SELECT kpi_authenticated_at FROM debicheck_mandates WHERE id=2").fetchone()[0]
    db.close()


@pytest.mark.parametrize("value,zone,expected", [
    ("2026-10-25 21:59:59", timezone.utc, date(2026, 10, 25)),
    ("2026-10-25 22:00:00", timezone.utc, date(2026, 10, 26)),
    ("2026-10-26 00:00:00", SAST, date(2026, 10, 26)),
    ("2026-10-26T01:00:00+03:00", timezone.utc, date(2026, 10, 26)),
    ("bad", timezone.utc, None),
])
def test_sast_business_day(value, zone, expected):
    assert business_day(value, naive_zone=zone) == expected


def test_per_person_calls_use_attributed_outbound_pbx_talk_time(app):
    with app.app_context():
        db = get_db()
        uid = staff(db, "Reception")
        other = staff(db, "Outside department", "Sales")
        for owner, direction, when, talk in (
            (uid, "outbound", "2026-10-26 00:01:00", 60),
            (uid, "outbound", "2026-10-26 09:00:00", 59),
            (uid, "outbound", "2026-10-26 10:00:00", 0),
            (uid, "inbound", "2026-10-26 11:00:00", 120),
            (uid, "outbound", "2026-10-25 23:59:00", 120),
            (uid, "outbound", "2026-10-26T23:00:00+00:00", 120),
            (other, "outbound", "2026-10-26 12:00:00", 120),
            (None, "outbound", "2026-10-26 12:00:00", 120),
        ):
            db.execute("INSERT INTO pbx_call_logs(user_id,direction,start_time,talk_duration) VALUES(?,?,?,?)", (owner,direction,when,talk))
        report = build_daily_scorecard(db, "reception", "2026-10-26")
        assert actuals(report) == {uid: {"calls": 3, "connects": 1, "phone_ptps": 0, "walk_in_ptps": 0, "debicheck": 0}}
        assert [t[2] for t in report["targets"]] == [100, 10, 2, 2, 2]
        assert build_daily_scorecard(db, "reception", "2026-10-26", user_id=other)["rows"] == []


def test_ptps_have_separate_channels_and_unknown_history_is_not_guessed(app):
    with app.app_context():
        db = get_db()
        uid = staff(db, "PTP caller")
        for channel, status in (("call","pending"),("walk_in","paid"),("unknown","pending"),
                                ("other","pending"),("call","superseded"),("walk_in","cancelled")):
            member_id = new_member(db)
            pid = db.execute("""INSERT INTO ptp_agreements(member_id,promise_date,payment_method,created_by,ptp_status,created_at)
                VALUES(?,'2026-11-01','cash',?,?,'2026-10-26 09:00:00')""",(member_id,uid,status)).lastrowid
            record_ptp_channel(db,pid,channel)
            db.execute("UPDATE ptp_agreements SET kpi_recorded_at='2026-10-26T09:00:00+00:00' WHERE id=?",(pid,))
        report = build_daily_scorecard(db,"reception","2026-10-26")
        assert actuals(report)[uid]["phone_ptps"] == 1
        assert actuals(report)[uid]["walk_in_ptps"] == 1
        assert report["missing_ptp_channel"] == 1
        with pytest.raises(ValueError):
            record_ptp_channel(db,pid,"made-up")


def test_sales_counts_creator_first_booking_and_compliance_credit(app, monkeypatch):
    with app.app_context():
        db = get_db()
        uid = staff(db,"Consultant A","Sales")
        other = staff(db,"Consultant B","Sales")
        lead = db.execute("""INSERT INTO leads(full_name,created_by,assigned_to,original_source,source,created_at)
            VALUES('Referral',?,?,'Referral','Outreach','2026-10-25 23:00:00')""",(uid,other)).lastrowid
        db.execute("""INSERT INTO leads(full_name,created_by,original_source,duplicate_of_lead_id,created_at)
            VALUES('Duplicate',?,'Referral',?,'2026-10-26 09:00:00')""",(uid,lead))
        for when in ("2026-10-25 23:30:00","2026-10-26 12:00:00","2026-10-27 09:00:00"):
            db.execute("INSERT INTO sales_stage_history(lead_id,to_stage,changed_by,changed_at) VALUES(?,'APPOINTMENT_BOOKED',?,?)",(lead,uid,when))
        complete = db.execute("INSERT INTO membership_applications(lead_id,created_by) VALUES(?,?)",(lead,uid)).lastrowid
        blocked = db.execute("INSERT INTO membership_applications(created_by) VALUES(?)",(uid,)).lastrowid
        for appid in (complete,blocked,complete):
            db.execute("""INSERT INTO stage_events(entity_type,entity_id,application_id,field,to_value,owner_id,occurred_at)
                VALUES('compliance',?,?,'overall_status','complete',?,'2026-10-25 23:00:00')""",(appid,appid,uid))
        monkeypatch.setattr("onecpase.applications.compliance_completion_blockers",lambda db,aid: ["Unsigned"] if aid==blocked else [])
        report = build_daily_scorecard(db,"sales","2026-10-26")
        assert actuals(report)[uid] == {"connects":0,"referrals":1,"appointments":1,"joined":1}
        assert actuals(report)[other] == {"connects":0,"referrals":0,"appointments":0,"joined":0}
        assert actuals(build_daily_scorecard(db,"sales","2026-10-27"))[uid]["appointments"] == 0
        assert [t[2] for t in report["targets"]] == [30,30,10,3]


def test_dashboard_shows_daily_targets_and_preserves_department_boundaries(app, client):
    with app.app_context():
        db = get_db()
        uid = staff(db,"My reception daily")
        staff(db,"SALES PRIVATE NAME","Sales")
        db.commit()
    with client.session_transaction() as state:
        state.update(user_id=uid,role="staff",department="Reception",permissions=["dashboard","all_collections"])
    for route in ("/reception-dashboard","/collections/dashboard"):
        response = client.get(route+"?kpi_date=2026-10-26")
        assert response.status_code == 200
        assert b"Daily staff targets" in response.data and b"0 / 100" in response.data
        assert b"Authenticated DebiChecks" in response.data and b"SALES PRIVATE NAME" not in response.data
    assert client.get("/sales-dashboard").status_code == 403


def test_invalid_day_falls_back_to_today_and_empty_team_is_safe(app):
    assert selected_day("not-a-day") == datetime.now(SAST).date()
    with app.app_context():
        assert build_daily_scorecard(get_db(),"sales","2026-10-26")["rows"] == []


@pytest.mark.parametrize("channel",["call","walk_in","other","unknown","invalid"])
def test_member_ptp_form_records_channel_without_changing_grant_rules(app,client,channel):
    with app.app_context():
        db=get_db()
        uid=staff(db,"Form reception")
        mid=new_member(db)
        db.execute("UPDATE members SET monthly_installment=399 WHERE id=?",(mid,))
        db.commit()
    with client.session_transaction() as state:
        state.update(user_id=uid,role="reception",department="Reception",permissions=["all_collections"])
    data={"promise_amount":"300","promise_date":"2099-01-01","payment_method":"debicheck",
          "arrangement_type":"partial","notes":"Synthetic agreement","arrears_amount":"1000"}
    if channel!="unknown":
        data["contact_channel"]=channel
    assert client.post(f"/members/{mid}/ptp/create",data=data).status_code==302
    with app.app_context():
        rows=get_db().execute("SELECT kpi_contact_channel,kpi_recorded_at,grant_status FROM ptp_agreements WHERE member_id=?",(mid,)).fetchall()
        if channel=="invalid":
            assert not rows
        else:
            assert len(rows)==1 and rows[0]["kpi_contact_channel"]==channel
            assert rows[0]["kpi_recorded_at"]
            assert rows[0]["grant_status"]!="granted"


def test_kpi_templates_follow_existing_style_budget():
    import json
    from pathlib import Path
    import re
    root=Path(__file__).resolve().parents[1]
    baseline=json.loads((root/"tests/template_ui_baseline.json").read_text(encoding="utf-8"))
    patterns={"inline_styles":r"\bstyle\s*=", "style_blocks":r"<style\b",
              "hex_colours":r"#[0-9a-fA-F]{3,8}\b", "emoji":"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]"}
    for name in ("_daily_staff_kpis.html","sales_dashboard.html","department_dashboard.html",
                 "collections/dashboard.html","members/detail.html"):
        source=(root/"onecpase/templates"/name).read_text(encoding="utf-8")
        for metric,pattern in patterns.items():
            assert len(re.findall(pattern,source))<=baseline.get(name,{}).get(metric,0),(name,metric)
