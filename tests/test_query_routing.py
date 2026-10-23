from onecpase.database import get_db
from onecpase.query_routing import route, notify_assignment
from onecpase.extensions import mail


def seed(db):
    for uid, name, role in [(71, 'Ayrton', 'manager'), (72, 'Mvuleni', 'admin'), (73, 'Thato', 'sales')]:
        db.execute('INSERT INTO users (id,username,password_hash,full_name,role,active) VALUES (?,?,?,?,?,1)',
                   (uid, name.lower(), 'x', name, role))
    db.commit()


def test_routes_named_owners_and_admin_queue(app):
    app.config['ELEV8_QUERY_ROUTING'] = True
    with app.app_context():
        db = get_db(); seed(db)
        assert route(db, 'refund') == {'department': 'Management', 'assigned_to': 71}
        assert route(db, 'sales')['assigned_to'] == 73
        assert route(db, 'membership', 'Renewal')['assigned_to'] == 73
        assert route(db, 'membership', 'Transfer') == {'department': 'Admin', 'assigned_to': None}
        assert route(db, 'payments')['department'] == 'Admin'


def test_other_tenants_are_not_routed_to_elev8(app):
    with app.test_request_context('/'):
        from flask import g
        g.tenant_slug = 'another-gym'
        assert route(get_db(), 'refund') is None


def test_assignment_email_recipients_and_failure(app, monkeypatch):
    app.config.update(ELEV8_QUERY_ROUTING=True, MAIL_USERNAME='test')
    with app.app_context():
        db = get_db(); seed(db)
        qid = db.execute("INSERT INTO queries(reference,category,department,assigned_to,description) "
                         "VALUES ('Q-TEST','refund','Management',71,'Private details')").lastrowid
        db.commit()
        with mail.record_messages() as messages:
            assert notify_assignment(db, qid) is True
        assert messages[0].recipients == ['ayrton@elev8healthandfitnessgym.com']
        assert 'Private details' not in messages[0].body
        db.execute("UPDATE queries SET category='general',department='Admin',assigned_to=NULL WHERE id=?", (qid,))
        db.commit()
        with mail.record_messages() as messages:
            assert notify_assignment(db, qid) is True
        assert any(m.recipients == ['mvuleni@elev8healthandfitnessgym.com'] for m in messages)
        db.execute("UPDATE queries SET category='sales',department='Sales',assigned_to=73 WHERE id=?", (qid,))
        db.commit()
        with mail.record_messages() as messages:
            assert notify_assignment(db, qid) is True
        assert {m.recipients[0] for m in messages} == {
            'sales@elev8healthandfitnessgym.com', 'ayrton@elev8healthandfitnessgym.com'}
        with mail.record_messages() as messages:
            assert notify_assignment(db, qid, escalated=True) is True
        assert {m.recipients[0] for m in messages} == {
            'mvuleni@elev8healthandfitnessgym.com', 'ayrton@elev8healthandfitnessgym.com'}
        def fail(message):
            raise RuntimeError('SMTP unavailable')
        monkeypatch.setattr(mail, 'send', fail)
        assert notify_assignment(db, qid) is False
        assert db.execute('SELECT id FROM queries WHERE id=?', (qid,)).fetchone()


def test_create_and_escalate_route_and_notify(app, client):
    app.config.update(ELEV8_QUERY_ROUTING=True, MAIL_USERNAME='test')
    with app.app_context():
        seed(get_db())
    with client.session_transaction() as session:
        session.update(user_id=72, role='admin', username='mvuleni')
    with app.app_context(), mail.record_messages() as messages:
        response = client.post('/queries/add', data={
            'category': 'sales', 'description': 'Joining enquiry',
            'assigned_to': '72', 'department': 'Admin'})
        assert response.status_code == 302
    assert {m.recipients[0] for m in messages} == {
        'sales@elev8healthandfitnessgym.com', 'ayrton@elev8healthandfitnessgym.com'}
    with app.app_context():
        qry = get_db().execute('SELECT * FROM queries ORDER BY id DESC LIMIT 1').fetchone()
        assert qry['assigned_to'] == 73 and qry['department'] == 'Sales'
        qid = qry['id']
    with app.app_context(), mail.record_messages() as messages:
        assert client.post(f'/queries/{qid}/escalate', data={'reason': 'Manager help required'}).status_code == 302
    assert {m.recipients[0] for m in messages} == {
        'mvuleni@elev8healthandfitnessgym.com', 'ayrton@elev8healthandfitnessgym.com'}
    with app.app_context():
        alerts = get_db().execute("SELECT recipient_id FROM staff_notifications WHERE source_kind='query' "
                                  'AND source_id=? AND resolved_at IS NULL', (qid,)).fetchall()
        assert {71, 72}.issubset({r['recipient_id'] for r in alerts})
