"""Shared class capacity/ownership writes for staff web and native member clients."""
from .database import _db_type


def change_booking(db, class_id, member_id, action, booking_id=None):
    if action not in ('book', 'cancel'):
        raise ValueError('Choose book or cancel.')
    backend = _db_type()
    db.execute('BEGIN IMMEDIATE' if backend == 'sqlite' else 'BEGIN')
    try:
        sql = 'SELECT * FROM fitness_classes WHERE id=?'
        if backend == 'postgresql': sql += ' FOR UPDATE'
        fc = db.execute(sql, (class_id,)).fetchone()
        if not fc or (action == 'book' and not fc['active']):
            raise ValueError('This class is not available.')
        if action == 'cancel':
            sql = "UPDATE class_bookings SET status='cancelled' WHERE class_id=? AND member_id=? AND status='booked'"
            params = (class_id, member_id)
            if booking_id is not None:
                sql += ' AND id=?'
                params += (booking_id,)
            db.execute(sql, params)
        else:
            member = db.execute("SELECT id FROM members WHERE id=? AND member_status='Active'", (member_id,)).fetchone()
            if not member: raise ValueError('An active member is required.')
            existing = db.execute("SELECT id FROM class_bookings WHERE class_id=? AND member_id=? AND status='booked'",
                                  (class_id, member_id)).fetchone()
            if not existing:
                count = db.execute("SELECT COUNT(*) FROM class_bookings WHERE class_id=? AND status='booked'", (class_id,)).fetchone()[0]
                if count >= fc['capacity']: raise ValueError('This class is full.')
                db.execute("INSERT INTO class_bookings(class_id,member_id,status) VALUES (?,?,'booked')", (class_id, member_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
