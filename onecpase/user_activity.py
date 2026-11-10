"""Who has been using the system and what they did (admin only).

Three kinds of record feed the User activity page:

* ``user_sign_ins``: every sign-in, failed attempt (wrong password, wrong
  code, unknown username, disabled account), sign-out and password change,
  with IP address and browser. Kept indefinitely; it is small.
* ``user_page_visits``: every page a signed-in person opens, every form they
  submit and every file they download. Background refreshes (JSON polling)
  are not recorded. Kept for ``PAGE_VISIT_DAYS`` days.
* the existing change logs (``audit_logs``, ``reception_audit``,
  ``hr_audit``): what people changed. HR entries show the action only, never
  the payload, because payroll detail is restricted even from admins.

Times are stored in UTC ("YYYY-MM-DD HH:MM:SS") like ``audit_logs`` and shown
in SAST.

Page visits are written on their own short connection after the response is
sent, so recording a visit can never commit half-finished work of a page that
failed, and a failure to record never breaks the page.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app, g, request, session

SAST = ZoneInfo("Africa/Johannesburg")
PAGE_VISIT_DAYS = 90
TIMELINE_LIMIT = 2000
_PRUNE_EVERY = timedelta(hours=12)
_last_prune: dict[str, datetime] = {}

SIGN_IN_EVENTS = {
    "signed_in": "Signed in",
    "code_sent": "Password correct, code emailed",
    "wrong_password": "Wrong password",
    "unknown_username": "Unknown username",
    "disabled_account": "Disabled account tried to sign in",
    "wrong_code": "Wrong sign-in code",
    "too_many_codes": "Locked out: too many wrong codes",
    "signed_out": "Signed out",
    "password_changed": "Changed own password",
    "password_reset": "Reset password by email code",
}
FAILED_SIGN_INS = frozenset(
    {"wrong_password", "unknown_username", "disabled_account", "wrong_code", "too_many_codes"}
)
KINDS = {"all": "Everything", "sign_ins": "Sign-ins", "pages": "Pages opened", "changes": "Changes"}

_SKIP_BLUEPRINTS = {"platform", "mobile", "health", "comms_webhooks"}

_AREA_LABELS = {
    "access_report": "Access report", "admin": "Admin", "admin_events": "Admin events",
    "applications": "Applications", "auth": "Account", "call_list": "Call list",
    "collections": "Collections", "comms": "Communications", "contracts": "Contracts",
    "dashboard": "Dashboard", "debicheck": "DebiCheck", "events": "Events",
    "first_month_collections": "First-month collections", "fitness": "Fitness",
    "fitness_journey": "Fitness journey", "hr": "HR", "workforce": "Workforce",
    "internal_comms": "Internal messages", "leads": "Leads", "members": "Members",
    "notifications": "Notifications", "onboarding": "Onboarding", "operations": "Operations",
    "pbx": "PBX", "ptp": "Promises to pay", "join": "Join", "queries": "Queries",
    "reception_admin": "Reception admin", "reconciliation": "Reconciliation",
    "reports": "Reports", "sales_pipeline": "Sales pipeline",
    "screen_recordings": "Screen recordings", "tenants": "Gym selection",
    "training": "Training", "turnstile": "Turnstile", "whatsapp_web": "WhatsApp",
}

# URL parameter -> what it identifies, for naming the client or record opened.
_SUBJECT_KEYS = {
    "mid": "member", "member_id": "member",
    "lid": "lead", "lead_id": "lead",
    "aid": "application", "application_id": "application",
    "qid": "query", "query_id": "query",
    "mandate_id": "mandate",
    "cid": "collection", "collection_id": "collection",
    "uid": "staff",
}


# ── Schema ────────────────────────────────────────────────────────────────────

def ensure_schema(db, backend: str = "sqlite") -> None:
    pk = "SERIAL PRIMARY KEY" if backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS user_sign_ins (
            id {pk},
            user_id INTEGER,
            username TEXT,
            event TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL)"""
    )
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS user_page_visits (
            id {pk},
            user_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            endpoint TEXT,
            view_args TEXT,
            status INTEGER,
            via_platform INTEGER NOT NULL DEFAULT 0,
            ip TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL)"""
    )
    for table in ("user_sign_ins", "user_page_visits"):
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table} (user_id, created_at)")
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_created ON {table} (created_at)")
    db.commit()


# ── Recording ─────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _client() -> tuple[str, str]:
    ip = (request.remote_addr or "")[:64]
    agent = (request.headers.get("User-Agent") or "")[:300]
    return ip, agent


def record_sign_in(db, event: str, user_id=None, username=None, commit: bool = True) -> None:
    """Record a sign-in event on the request's own connection.

    Called only where the sign-in code has nothing half-done in the
    transaction (or is about to commit it anyway). Never raises: a failure
    to record must not stop anyone signing in or out.
    """
    try:
        ip, agent = _client()
        db.execute(
            """INSERT INTO user_sign_ins (user_id, username, event, ip, user_agent, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, (str(username or "").strip().lower()[:80] or None), event, ip, agent, _utc_now()),
        )
        if commit:
            db.commit()
    except Exception as exc:  # pragma: no cover - logged, never fatal
        current_app.logger.warning("Could not record sign-in event %s: %s", event, exc)


def record_failed_password(db, username: str) -> None:
    """Attribute a wrong-password attempt to the account it was aimed at."""
    try:
        row = db.execute(
            "SELECT id, active, COALESCE(is_platform_user, 0) AS is_platform_user FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    except Exception as exc:  # pragma: no cover
        current_app.logger.warning("Could not look up failed sign-in user: %s", exc)
        row = None
    if row is None or row["is_platform_user"]:
        record_sign_in(db, "unknown_username", username=username)
    elif not row["active"]:
        record_sign_in(db, "disabled_account", user_id=row["id"], username=username)
    else:
        record_sign_in(db, "wrong_password", user_id=row["id"], username=username)


def _should_record(response) -> bool:
    if not session.get("user_id"):
        return False
    if request.endpoint in (None, "static") or request.blueprint in _SKIP_BLUEPRINTS:
        return False
    if request.method in ("HEAD", "OPTIONS"):
        return False
    from .auth import session_tenant_ok
    if not session_tenant_ok():
        return False
    if request.method != "GET":
        return True  # every form submit / action
    disposition = response.headers.get("Content-Disposition", "")
    return response.mimetype == "text/html" or "attachment" in disposition.lower()


def _connection_target() -> tuple[str, str]:
    """Where this request's database lives, captured while the request is open."""
    from .database import _db_type
    if _db_type() == "postgresql":
        return "postgresql", current_app.config["DATABASE_URL"]
    tenant = g.get("tenant")
    return "sqlite", (tenant["db_path"] if tenant else current_app.config["DATABASE_PATH"])


def _activity_connection(target: tuple[str, str]):
    backend, where = target
    if backend == "postgresql":
        import psycopg2
        from .db_adapter import DbAdapter
        return DbAdapter(psycopg2.connect(where))
    return sqlite3.connect(where, timeout=5)


def record_request(response):
    """after_request hook: remember which page a signed-in person opened.

    The row is written once the response has been sent and the request's own
    database connection is closed (``call_on_close``). Writing earlier would
    wait on that connection's lock whenever a page failed part-way, and must
    never risk committing its half-finished work.
    """
    try:
        if not _should_record(response):
            return response
        ip, agent = _client()
        path = request.path
        if request.query_string:
            path += "?" + request.query_string.decode("utf-8", "replace")
        view_args = {k: v for k, v in (request.view_args or {}).items() if isinstance(v, (int, str))}
        row = (
            session["user_id"], request.method, path[:500], request.endpoint,
            json.dumps(view_args, sort_keys=True) if view_args else None,
            response.status_code, 1 if session.get("is_power_user") else 0,
            ip, agent, _utc_now(),
        )
        target = _connection_target()
        prune_key = str(g.get("tenant_slug") or target[1])
        logger = current_app.logger
    except Exception as exc:  # pragma: no cover - logged, never fatal
        current_app.logger.warning("Could not record page visit: %s", exc)
        return response

    def _write():
        try:
            conn = _activity_connection(target)
            try:
                conn.execute(
                    """INSERT INTO user_page_visits
                       (user_id, method, path, endpoint, view_args, status, via_platform, ip, user_agent, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    row,
                )
                _prune(conn, prune_key)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # logged, never fatal
            logger.warning("Could not record page visit: %s", exc)

    response.call_on_close(_write)
    return response


def _prune(conn, key: str) -> None:
    now = datetime.now(timezone.utc)
    if now - _last_prune.get(key, datetime.min.replace(tzinfo=timezone.utc)) < _PRUNE_EVERY:
        return
    _last_prune[key] = now
    cutoff = (now - timedelta(days=PAGE_VISIT_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM user_page_visits WHERE created_at < ?", (cutoff,))


# ── Reading ───────────────────────────────────────────────────────────────────

def parse_filters(args, today: date | None = None) -> dict:
    today = today or datetime.now(SAST).date()

    def _day(value, fallback):
        try:
            return date.fromisoformat(str(value or "").strip())
        except ValueError:
            return fallback

    end = _day(args.get("end"), today)
    start = _day(args.get("start"), end - timedelta(days=6))
    if start > end:
        start, end = end, start
    try:
        user_id = int(args.get("user") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    kind = args.get("kind") if args.get("kind") in KINDS else "all"
    return {
        "start": start, "end": end, "user_id": user_id, "kind": kind,
        "failed_only": args.get("failed") == "1",
    }


def _utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    lo = datetime.combine(start, dtime.min, SAST).astimezone(timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), dtime.min, SAST).astimezone(timezone.utc)
    return lo, hi


def _parse_stamp(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _table_exists(db, name: str) -> bool:
    try:
        db.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchall()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def describe_device(agent: str | None) -> str:
    text = agent or ""
    if not text:
        return ""
    if "Edg/" in text:
        browser = "Edge"
    elif "SamsungBrowser" in text:
        browser = "Samsung Internet"
    elif "OPR/" in text or "Opera" in text:
        browser = "Opera"
    elif "Firefox/" in text:
        browser = "Firefox"
    elif "Chrome/" in text or "CriOS" in text:
        browser = "Chrome"
    elif "Safari/" in text:
        browser = "Safari"
    elif "okhttp" in text.lower() or "Dalvik" in text:
        browser = "Android app"
    else:
        browser = "Other browser"
    if "Windows" in text:
        system = "Windows"
    elif "Android" in text:
        system = "Android"
    elif "iPhone" in text or "iPad" in text:
        system = "iPhone/iPad"
    elif "Mac OS X" in text or "Macintosh" in text:
        system = "Mac"
    elif "Linux" in text:
        system = "Linux"
    else:
        system = ""
    return f"{browser} on {system}" if system else browser


def _humanise(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip().capitalize()


def _page_label(row) -> str:
    endpoint = row["endpoint"] or ""
    area_key, _, view = endpoint.partition(".")
    area = _AREA_LABELS.get(area_key, _humanise(area_key) if area_key else "Page")
    view_text = _humanise(view) if view else ""
    label = f"{area}: {view_text}" if view_text else area
    if row["method"] != "GET":
        return f"Submitted {label}"
    if "download" in endpoint:
        return f"Downloaded {label}"
    return label


def _resolve_subjects(db, wanted: dict[str, set[int]]) -> dict[tuple[str, int], str]:
    """Names for the records people opened, looked up in bulk."""
    names: dict[tuple[str, int], str] = {}

    def _fetch(kind, sql, ids, fmt):
        ids = sorted(ids)[:900]
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        try:
            for row in db.execute(sql.format(marks=marks), ids).fetchall():
                label = fmt(row)
                if label:
                    names[(kind, int(row["id"]))] = label
        except Exception as exc:  # pragma: no cover - a missing table must not break the page
            current_app.logger.warning("Activity lookup for %s failed: %s", kind, exc)
            try:
                db.rollback()
            except Exception:
                pass

    def _member_name(row):
        return " ".join(p for p in (row["first_name"], row["last_name"]) if p).strip()

    _fetch("member", "SELECT id, first_name, last_name FROM members WHERE id IN ({marks})",
           wanted.get("member", set()), lambda r: f"Client {_member_name(r)}")
    _fetch("lead", "SELECT id, full_name FROM leads WHERE id IN ({marks})",
           wanted.get("lead", set()), lambda r: f"Lead {r['full_name']}")
    _fetch("application",
           """SELECT a.id, COALESCE(NULLIF(TRIM(COALESCE(m.first_name,'') || ' ' || COALESCE(m.last_name,'')), ''),
                                   l.full_name, '') AS name
              FROM membership_applications a
              LEFT JOIN members m ON m.id = a.member_id
              LEFT JOIN leads l ON l.id = a.lead_id
              WHERE a.id IN ({marks})""",
           wanted.get("application", set()),
           lambda r: f"Application #{r['id']} {r['name']}".strip())
    _fetch("query", "SELECT id, reference, member_name FROM queries WHERE id IN ({marks})",
           wanted.get("query", set()),
           lambda r: " ".join(p for p in ("Query", r["reference"] or f"#{r['id']}", r["member_name"]) if p))
    _fetch("mandate",
           """SELECT d.id, m.first_name, m.last_name FROM debicheck_mandates d
              LEFT JOIN members m ON m.id = d.member_id WHERE d.id IN ({marks})""",
           wanted.get("mandate", set()), lambda r: f"DebiCheck mandate for {_member_name(r)}".strip())
    _fetch("collection",
           """SELECT c.id, m.first_name, m.last_name FROM collections c
              LEFT JOIN members m ON m.id = c.member_id WHERE c.id IN ({marks})""",
           wanted.get("collection", set()), lambda r: f"Collection for {_member_name(r)}".strip())
    _fetch("staff", "SELECT id, full_name FROM users WHERE id IN ({marks})",
           wanted.get("staff", set()), lambda r: f"Staff account {r['full_name']}")
    return names


def _subject_refs(view_args: dict) -> list[tuple[str, int]]:
    refs = []
    for key, kind in _SUBJECT_KEYS.items():
        value = view_args.get(key)
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            refs.append((kind, int(value)))
    return refs


def _export_detail(raw) -> str:
    try:
        record = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return str(raw or "")[:200]
    parts = [
        f"{record.get('rows', 0)} rows as {str(record.get('format', '')).upper()}",
        record.get("title") or "",
        record.get("page") or "",
    ]
    return " · ".join(p for p in parts if p)


def _short(value, limit=200) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def load_activity(db, filters: dict) -> dict:
    lo, hi = _utc_bounds(filters["start"], filters["end"])
    lo_s, hi_s = lo.strftime("%Y-%m-%d %H:%M:%S"), hi.strftime("%Y-%m-%d %H:%M:%S")
    # Prefix bounds a day wider, for logs written in ISO "T" format; filtered exactly below.
    lo_wide = (lo - timedelta(days=1)).strftime("%Y-%m-%d")
    hi_wide = (hi + timedelta(days=1)).strftime("%Y-%m-%d")
    user_id, kind, failed_only = filters["user_id"], filters["kind"], filters["failed_only"]

    staff = {
        int(r["id"]): r for r in db.execute(
            """SELECT id, full_name, username, role, active, last_login FROM users
               WHERE COALESCE(is_platform_user, 0) = 0 ORDER BY full_name"""
        ).fetchall()
    }

    def _user_clause(column):
        return (f" AND {column} = ?", [user_id]) if user_id else ("", [])

    events: list[dict] = []

    # Sign-ins
    if kind in ("all", "sign_ins"):
        clause, params = _user_clause("user_id")
        failed_clause = ""
        if failed_only:
            failed_clause = " AND event IN (" + ",".join("?" for _ in FAILED_SIGN_INS) + ")"
            params = params + sorted(FAILED_SIGN_INS)
        for r in db.execute(
            f"""SELECT * FROM user_sign_ins WHERE created_at >= ? AND created_at < ?{clause}{failed_clause}
                ORDER BY created_at DESC LIMIT ?""",
            [lo_s, hi_s, *params, TIMELINE_LIMIT + 1],
        ).fetchall():
            event = r["event"]
            detail = ""
            if r["user_id"] is None or event in ("unknown_username", "disabled_account"):
                detail = f"Username typed: {r['username'] or '(blank)'}"
            events.append({
                "at": r["created_at"], "user_id": r["user_id"], "kind": "Sign-in",
                "what": SIGN_IN_EVENTS.get(event, _humanise(event)), "detail": detail,
                "failed": event in FAILED_SIGN_INS, "ip": r["ip"] or "",
                "device": describe_device(r["user_agent"]), "link": None,
            })

    # Pages opened, forms submitted, files downloaded
    visit_rows = []
    if kind in ("all", "pages") and not failed_only:
        clause, params = _user_clause("user_id")
        visit_rows = db.execute(
            f"""SELECT * FROM user_page_visits WHERE created_at >= ? AND created_at < ?{clause}
                ORDER BY created_at DESC LIMIT ?""",
            [lo_s, hi_s, *params, TIMELINE_LIMIT + 1],
        ).fetchall()

    # Changes (existing change logs)
    change_rows = []
    if kind in ("all", "changes") and not failed_only:
        clause, params = _user_clause("al.user_id")
        change_rows = db.execute(
            f"""SELECT al.* FROM audit_logs al WHERE al.timestamp >= ? AND al.timestamp < ?{clause}
                ORDER BY al.timestamp DESC LIMIT ?""",
            [lo_s, hi_s, *params, TIMELINE_LIMIT + 1],
        ).fetchall()

    wanted: dict[str, set[int]] = {}
    parsed_args = []
    for r in visit_rows:
        try:
            args = json.loads(r["view_args"]) if r["view_args"] else {}
        except (TypeError, ValueError):
            args = {}
        refs = _subject_refs(args)
        for subject_kind, ident in refs:
            wanted.setdefault(subject_kind, set()).add(ident)
        parsed_args.append(refs)
    for r in change_rows:
        if r["lead_id"]:
            wanted.setdefault("lead", set()).add(int(r["lead_id"]))
        if r["application_id"]:
            wanted.setdefault("application", set()).add(int(r["application_id"]))
    names = _resolve_subjects(db, wanted)

    for r, refs in zip(visit_rows, parsed_args):
        subject = ", ".join(names.get(ref, f"{ref[0]} #{ref[1]}") for ref in refs)
        status = r["status"] or 0
        notes = []
        if status in (401, 403):
            notes.append("Refused: no permission")
        elif status >= 400:
            notes.append(f"Error {status}")
        if r["via_platform"]:
            notes.append("Opened by a platform operator signed in as this person")
        events.append({
            "at": r["created_at"], "user_id": r["user_id"], "kind": "Page",
            "what": _page_label(r), "detail": " · ".join(p for p in (subject, *notes) if p),
            "path": r["path"], "failed": status >= 400, "ip": r["ip"] or "",
            "device": describe_device(r["user_agent"]),
            "link": r["path"] if r["method"] == "GET" and status < 400 else None,
        })

    for r in change_rows:
        action = r["action"] or ""
        if action == "table_export":
            what, detail = "Exported a table", _export_detail(r["new_value"])
        else:
            what = _humanise(action)
            pieces = []
            if r["old_value"] not in (None, ""):
                pieces.append(f"from {_short(r['old_value'], 80)}")
            if r["new_value"] not in (None, ""):
                pieces.append(f"to {_short(r['new_value'], 120)}")
            detail = " ".join(pieces)
        subject = []
        link = None
        if r["application_id"]:
            subject.append(names.get(("application", int(r["application_id"])), f"Application #{r['application_id']}"))
            link = f"/applications/{r['application_id']}"
        if r["lead_id"]:
            subject.append(names.get(("lead", int(r["lead_id"])), f"Lead #{r['lead_id']}"))
            link = link or f"/leads/{r['lead_id']}"
        events.append({
            "at": r["timestamp"], "user_id": r["user_id"], "kind": "Change",
            "what": what, "detail": " · ".join(p for p in (", ".join(subject), detail) if p),
            "failed": False, "ip": "", "device": "", "link": link,
        })

    if kind in ("all", "changes") and not failed_only:
        for table, label, show_payload in (("reception_audit", "Reception", True), ("hr_audit", "HR", False)):
            if not _table_exists(db, table):
                continue
            clause, params = _user_clause("actor_id")
            for r in db.execute(
                f"""SELECT * FROM {table} WHERE created_at >= ? AND created_at < ?{clause}
                    ORDER BY created_at DESC LIMIT ?""",
                [lo_wide, hi_wide, *params, TIMELINE_LIMIT + 1],
            ).fetchall():
                events.append({
                    "at": r["created_at"], "user_id": r["actor_id"], "kind": "Change",
                    "what": f"{label}: {_humanise(r['action'] or '')}",
                    "detail": _short(r["payload"], 160) if show_payload else "",
                    "failed": False, "ip": "", "device": "", "link": None,
                })

    # Exact range, newest first, normalised timestamps.
    kept = []
    for e in events:
        stamp = _parse_stamp(e["at"])
        if stamp is None or not (lo <= stamp < hi):
            continue
        e["at_utc"] = stamp
        e["at"] = stamp.strftime("%Y-%m-%d %H:%M:%S")
        person = staff.get(int(e["user_id"])) if e["user_id"] is not None else None
        if person:
            e["person"] = person["full_name"]
        elif e["user_id"] is not None:
            e["person"] = f"User #{e['user_id']}"
        else:  # a change with no person is the system's own work (scheduled jobs)
            e["person"] = "Unknown person" if e["kind"] == "Sign-in" else "System"
        kept.append(e)
    kept.sort(key=lambda e: e["at_utc"], reverse=True)
    truncated = len(kept) > TIMELINE_LIMIT

    return {
        "events": kept[:TIMELINE_LIMIT],
        "truncated": truncated,
        "summary": _summary(db, staff, lo_s, hi_s, user_id),
        "staff": list(staff.values()),
        "unknown_attempts": _count(db,
            "SELECT COUNT(*) FROM user_sign_ins WHERE user_id IS NULL AND created_at >= ? AND created_at < ?",
            [lo_s, hi_s]),
    }


def _count(db, sql, params) -> int:
    row = db.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _summary(db, staff: dict, lo_s: str, hi_s: str, user_id) -> list[dict]:
    """One row per person: who used the system in the range, and how."""
    failed = ",".join(f"'{e}'" for e in sorted(FAILED_SIGN_INS))
    by_user: dict[int, dict] = {}

    def _row(uid):
        return by_user.setdefault(int(uid), {
            "sign_ins": 0, "failed": 0, "pages": 0, "changes": 0, "exports": 0,
            "last_seen": None, "last_ip": "", "last_device": "",
        })

    for r in db.execute(
        f"""SELECT user_id,
                   SUM(CASE WHEN event = 'signed_in' THEN 1 ELSE 0 END) AS sign_ins,
                   SUM(CASE WHEN event IN ({failed}) THEN 1 ELSE 0 END) AS failed,
                   MAX(created_at) AS last_at
            FROM user_sign_ins WHERE user_id IS NOT NULL AND created_at >= ? AND created_at < ?
            GROUP BY user_id""",
        (lo_s, hi_s),
    ).fetchall():
        item = _row(r["user_id"])
        item["sign_ins"], item["failed"] = int(r["sign_ins"] or 0), int(r["failed"] or 0)
        item["last_seen"] = max(filter(None, (item["last_seen"], r["last_at"])), default=None)

    for r in db.execute(
        """SELECT user_id, COUNT(*) AS pages, MAX(created_at) AS last_at
           FROM user_page_visits WHERE created_at >= ? AND created_at < ? GROUP BY user_id""",
        (lo_s, hi_s),
    ).fetchall():
        item = _row(r["user_id"])
        item["pages"] = int(r["pages"] or 0)
        item["last_seen"] = max(filter(None, (item["last_seen"], r["last_at"])), default=None)

    for r in db.execute(
        """SELECT user_id, COUNT(*) AS changes,
                  SUM(CASE WHEN action = 'table_export' THEN 1 ELSE 0 END) AS exports,
                  MAX(timestamp) AS last_at
           FROM audit_logs WHERE user_id IS NOT NULL AND timestamp >= ? AND timestamp < ?
           GROUP BY user_id""",
        (lo_s, hi_s),
    ).fetchall():
        item = _row(r["user_id"])
        exports = int(r["exports"] or 0)
        item["exports"] = exports
        item["changes"] = int(r["changes"] or 0) - exports
        last = _parse_stamp(r["last_at"])
        if last:
            last_s = last.strftime("%Y-%m-%d %H:%M:%S")
            item["last_seen"] = max(filter(None, (item["last_seen"], last_s)), default=None)

    # Where and on what each person last signed in from (any date).
    for r in db.execute(
        """SELECT s.user_id, s.ip, s.user_agent FROM user_sign_ins s
           WHERE s.event = 'signed_in' AND s.id = (
               SELECT MAX(s2.id) FROM user_sign_ins s2
               WHERE s2.user_id = s.user_id AND s2.event = 'signed_in')"""
    ).fetchall():
        if int(r["user_id"]) in by_user:
            by_user[int(r["user_id"])]["last_ip"] = r["ip"] or ""
            by_user[int(r["user_id"])]["last_device"] = describe_device(r["user_agent"])

    rows = []
    for uid, person in staff.items():
        if user_id and uid != user_id:
            continue
        item = by_user.get(uid)
        if item is None and not person["active"]:
            continue  # disabled and idle: nothing to show
        item = item or _row(uid)
        rows.append({
            "user_id": uid, "name": person["full_name"], "username": person["username"],
            "role": person["role"], "active": bool(person["active"]),
            "last_login": person["last_login"], **item,
            "used": bool(item["sign_ins"] or item["pages"] or item["changes"] or item["exports"]),
        })
    rows.sort(key=lambda r: (not r["used"], -(r["pages"] + r["changes"]), r["name"] or ""))
    return rows
