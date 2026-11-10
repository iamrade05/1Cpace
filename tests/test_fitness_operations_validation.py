from onecpase.database import get_db


def sign_in(app, client):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) "
                   "VALUES(1,'staff','x','Test Staff','admin',1)")
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1, role="admin")


def test_class_add_and_edit_reject_invalid_schedule(app, client):
    sign_in(app, client)
    valid = {"name": "Spin", "schedule_day": "Monday", "schedule_time": "06:30", "capacity": "20"}
    for invalid in ({"capacity": "-5"}, {"schedule_day": "made-up"}, {"schedule_time": "29:99"}):
        assert client.post("/fitness/add", data={**valid, **invalid}).status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT count(*) FROM fitness_classes").fetchone()[0] == 0

    assert client.post("/fitness/add", data=valid).status_code == 302
    with app.app_context():
        class_id = get_db().execute("SELECT id FROM fitness_classes").fetchone()[0]
    response = client.post(f"/fitness/{class_id}/edit", data={**valid, "name": "", "schedule_time": ""})
    assert response.status_code == 200
    with app.app_context():
        row = get_db().execute("SELECT name,schedule_time,active FROM fitness_classes WHERE id=?", (class_id,)).fetchone()
        assert tuple(row) == ("Spin", "06:30", 1)


def test_assessment_and_body_matrix_reject_invalid_measurements(app, client):
    sign_in(app, client)
    with app.app_context():
        db = get_db()
        member_id = db.execute("INSERT INTO members(first_name,last_name,id_number,outcome) "
                               "VALUES('Test','Member','fitness-validation-member','joined')").lastrowid
        db.commit()

    valid = {"member_id": member_id, "date": "2026-11-07", "weight_kg": "80", "body_fat_pct": "20"}
    paths = (("/fitness/assessments/add", 200), (f"/fitness/body-matrix/{member_id}/add", 302))
    for path, invalid_status in paths:
        for invalid in ({"weight_kg": "-80"}, {"body_fat_pct": "150"}, {"bmi": "NaN"}, {"date": "2026-02-30"}):
            assert client.post(path, data={**valid, **invalid}).status_code == invalid_status
    with app.app_context():
        assert get_db().execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
        assert get_db().execute("SELECT count(*) FROM body_matrix").fetchone()[0] == 0

    for path, _ in paths:
        assert client.post(path, data=valid).status_code == 302
    with app.app_context():
        for table in ("assessments", "body_matrix"):
            row = get_db().execute(f"SELECT date,weight_kg,body_fat_pct,muscle_mass_kg FROM {table}").fetchone()
            assert tuple(row) == ("2026-11-07", 80, 20, None)


def test_equipment_and_maintenance_reject_invalid_values(app, client):
    sign_in(app, client)
    valid = {"name": "Bike", "equipment_type": "cardio", "condition": "good", "purchase_date": "2026-10-01"}
    for invalid in ({"equipment_type": "made-up"}, {"condition": "broken-ish"}, {"purchase_date": "not-a-date"}):
        assert client.post("/operations/add", data={**valid, **invalid}).status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT count(*) FROM equipment").fetchone()[0] == 0

    assert client.post("/operations/add", data=valid).status_code == 302
    with app.app_context():
        equipment_id = get_db().execute("SELECT id FROM equipment").fetchone()[0]
    assert client.post(f"/operations/{equipment_id}/edit", data={**valid, "condition": "invalid"}).status_code == 200
    path = f"/operations/{equipment_id}/maintenance/add"
    maintenance = {"description": "Service", "maintenance_date": "2026-11-07", "cost": "20.50"}
    for invalid in ({"cost": "inf"}, {"cost": "-1"}, {"maintenance_date": ""}, {"next_due_date": "2026-11-06"}):
        assert client.post(path, data={**maintenance, **invalid}).status_code == 302
    with app.app_context():
        assert tuple(get_db().execute("SELECT condition,active FROM equipment").fetchone()) == ("good", 1)
        assert get_db().execute("SELECT count(*) FROM maintenance_log").fetchone()[0] == 0

    assert client.post(path, data={**maintenance, "next_due_date": "2027-01-01"}).status_code == 302
    with app.app_context():
        row = get_db().execute("SELECT maintenance_date,cost,next_due_date FROM maintenance_log").fetchone()
        assert tuple(row) == ("2026-11-07", 20.5, "2027-01-01")
