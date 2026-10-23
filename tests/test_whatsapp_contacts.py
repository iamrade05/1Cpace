from onecpase.database import get_db
from onecpase.encryption import encrypt
from onecpase.whatsapp_contacts import identify_conversations, phone_key


def member(db, first, phone):
    return db.execute('INSERT INTO members(first_name,last_name,contact,id_number) VALUES (?,?,?,?)', (first,'Client',phone,'test-'+first)).lastrowid


def conversation(db, phone, linked=None):
    cid=db.execute("INSERT INTO wa_conversations(wa_number,contact_phone,linked_member_id) VALUES ('reception',?,?)",(phone,linked)).lastrowid
    return db.execute('SELECT * FROM wa_conversations WHERE id=?',(cid,)).fetchone()


def test_unique_number_links_and_shows_client(app):
    with app.app_context():
        db=get_db()
        mid=member(db,'Thabo','082 123 4567')
        conv=conversation(db,'27821234567')
        result=identify_conversations(db,[conv])[0]
        assert result['client']['id']==mid and result['client']['full_name']=='Thabo Client'
        assert db.execute('SELECT linked_member_id FROM wa_conversations WHERE id=?',(conv['id'],)).fetchone()[0]==mid


def test_shared_number_is_not_linked(app):
    with app.app_context():
        db=get_db()
        member(db,'One','0821234567');member(db,'Two','+27 82 123 4567')
        result=identify_conversations(db,[conversation(db,'27821234567')])[0]
        assert result['client'] is None and result['phone_match_count']==2


def test_manual_link_is_preserved_and_unknown_is_unlinked(app):
    with app.app_context():
        db=get_db()
        manual=member(db,'Chosen','0820000000')
        member(db,'Other','0821234567')
        result=identify_conversations(db,[conversation(db,'27821234567',manual),conversation(db,'27829999999')])
        assert result[0]['client']['id']==manual
        assert result[1]['client'] is None and result[1]['phone_match_count']==0


def test_encrypted_contact_matches(app):
    with app.app_context():
        db=get_db();mid=member(db,'Encrypted',encrypt('0821234567'))
        assert identify_conversations(db,[conversation(db,'27821234567')])[0]['client']['id']==mid


def test_own_alternative_numbers_match_but_emergency_contact_does_not(app):
    with app.app_context():
        db=get_db();mid=member(db,'Alternate','0820000000')
        db.execute('UPDATE members SET alternative_number=?,work_number=?,emergency_contact_number=? WHERE id=?',
                   ('0821234567','+27821234567','0829999999',mid))
        rows=identify_conversations(db,[conversation(db,'27821234567'),conversation(db,'27829999999')])
        assert rows[0]['client']['id']==mid and rows[0]['phone_match_count']==1
        assert rows[1]['client'] is None


def test_phone_keys_are_exact():
    assert phone_key('0821234567')==phone_key('+27 82 123 4567')==phone_key('0027821234567')
    assert phone_key('Call 0821234567') is None
    assert phone_key('4567') is None


def test_inbox_and_chat_show_profile_link(app,client):
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role) VALUES(1,'contacts','x','Staff','staff')")
        mid=member(db,'Matched','0821234567');conv=conversation(db,'27821234567');cid=conv['id'];db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1,role='staff',permissions=['reception_whatsapp'])
    for path in ['/communications/whatsapp/reception',f'/communications/whatsapp/reception/chat/{cid}']:
        response=client.get(path)
        assert response.status_code==200
        assert b'Matched Client' in response.data
        assert f'/members/{mid}'.encode() in response.data
