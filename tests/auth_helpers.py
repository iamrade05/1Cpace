"""Persist the identity/grants represented by a synthetic browser sign-in."""
from onecpase.database import get_db


def ensure_staff_account(client, role, user_id=1, permissions=None):
    with client.application.app_context():
        db=get_db()
        db.execute('''INSERT OR IGNORE INTO users(id,username,password_hash,full_name,role,department,active)
            VALUES (?,?,'fixture-password','Fixture Staff',?,'General',1)''',
            (user_id,f'fixture-staff-{user_id}',role))
        db.execute('UPDATE users SET role=? WHERE id=?',(role,user_id))
        if permissions is not None:
            db.execute('DELETE FROM user_permissions WHERE user_id=?',(user_id,))
            for permission in permissions:
                db.execute('INSERT OR IGNORE INTO user_permissions(user_id,permission) VALUES (?,?)',(user_id,permission))
        db.commit()
