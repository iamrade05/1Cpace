"""
PostgreSQL compatibility adapter for 1Cpase.

Wraps a psycopg2 connection so it behaves like sqlite3 for the rest of the
codebase:
  - Translates ? placeholders → %s
  - Translates SQLite date/time functions → PostgreSQL equivalents at runtime
  - Translates INSERT OR IGNORE → INSERT … ON CONFLICT DO NOTHING
  - Provides dict-style + integer-index row access (like sqlite3.Row)
  - Exposes .lastrowid on INSERT via RETURNING id

Zero changes are required in route files when switching from SQLite to PostgreSQL.
"""

import re
from psycopg2.extras import RealDictCursor

# ── SQL translation rules (applied in order) ─────────────────────────────────

_TRANSLATIONS = [
    # INSERT OR IGNORE → INSERT (the ON CONFLICT clause is appended later)
    (re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.IGNORECASE), "INSERT /*_OCI_*/"),

    # datetime('now') variants → NOW()
    (re.compile(r"datetime\(\s*'now'\s*,\s*'localtime'\s*\)", re.IGNORECASE), "NOW()"),
    (re.compile(r"datetime\(\s*'now'\s*,\s*'utc'\s*\)", re.IGNORECASE), "NOW()"),
    (re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE), "NOW()"),

    # date('now', '+N day') → (CURRENT_DATE + INTERVAL 'N day')
    (re.compile(r"date\(\s*'now'\s*,\s*'\+(\d+)\s+day'\s*\)", re.IGNORECASE),
     r"(CURRENT_DATE + INTERVAL '\1 day')"),

    # date('now') → CURRENT_DATE
    (re.compile(r"date\(\s*'now'\s*\)", re.IGNORECASE), "CURRENT_DATE"),

    # strftime('%Y-%m', expr) → TO_CHAR(expr::date, 'YYYY-MM')
    (re.compile(r"strftime\(\s*'%Y-%m'\s*,\s*([^)]+)\)", re.IGNORECASE),
     r"TO_CHAR((\1)::date, 'YYYY-MM')"),

    # strftime('%Y', expr) → TO_CHAR(expr::date, 'YYYY')
    (re.compile(r"strftime\(\s*'%Y'\s*,\s*([^)]+)\)", re.IGNORECASE),
     r"TO_CHAR((\1)::date, 'YYYY')"),

    # strftime('%m', expr) → TO_CHAR(expr::date, 'MM')
    (re.compile(r"strftime\(\s*'%m'\s*,\s*([^)]+)\)", re.IGNORECASE),
     r"TO_CHAR((\1)::date, 'MM')"),

    # strftime('%d', expr) → TO_CHAR(expr::date, 'DD')
    (re.compile(r"strftime\(\s*'%d'\s*,\s*([^)]+)\)", re.IGNORECASE),
     r"TO_CHAR((\1)::date, 'DD')"),
]

_OCI_MARKER = "/*_OCI_*/"  # marks queries that started as INSERT OR IGNORE


def _translate(query: str) -> str:
    """Translate SQLite-specific SQL syntax to PostgreSQL."""
    for pattern, replacement in _TRANSLATIONS:
        query = pattern.sub(replacement, query)
    # ? → %s (only outside quoted strings — simple approach works for this codebase)
    query = query.replace("?", "%s")
    return query


def _finalise_insert_or_ignore(query: str) -> str:
    """Append ON CONFLICT DO NOTHING if the query was INSERT OR IGNORE."""
    if _OCI_MARKER in query:
        query = query.replace(_OCI_MARKER, "").rstrip().rstrip(";")
        if "ON CONFLICT" not in query.upper():
            query += " ON CONFLICT DO NOTHING"
    return query


# ── Row wrapper ───────────────────────────────────────────────────────────────

class _Row(dict):
    """Dict that also supports integer-index access (sqlite3.Row compatibility)."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        # PostgreSQL returns lowercase column names; try exact then lowercase
        try:
            return super().__getitem__(key)
        except KeyError:
            return super().__getitem__(key.lower())

    def keys(self):
        return super().keys()


# ── Cursor wrapper ────────────────────────────────────────────────────────────

class _CursorWrapper:
    """Wraps psycopg2 cursor to look like sqlite3 cursor."""

    def __init__(self, pg_conn):
        self._conn = pg_conn
        self._cur = None
        self.lastrowid = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _wrap_rows(self, rows):
        if rows is None:
            return None
        if isinstance(rows, list):
            return [_Row(r) for r in rows]
        return _Row(rows)

    def _get_lastrowid(self):
        """Use a fresh cursor to read lastval() — safe since it's session-scoped."""
        try:
            with self._conn.cursor() as lv:
                lv.execute("SELECT lastval()")
                row = lv.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    # ── Public interface ──────────────────────────────────────────────────────

    def execute(self, query, params=()):
        translated = _translate(query)
        is_oci = _OCI_MARKER in translated
        translated = _finalise_insert_or_ignore(translated)

        self._cur = self._conn.cursor(cursor_factory=RealDictCursor)
        self._cur.execute(translated, params if params else None)

        is_insert = bool(re.match(r"\s*INSERT\b", translated.replace(_OCI_MARKER, ""),
                                  re.IGNORECASE))
        if is_insert and not is_oci:
            # Only fetch lastrowid for plain INSERTs (not ON CONFLICT DO NOTHING)
            self.lastrowid = self._get_lastrowid()
        else:
            self.lastrowid = None

        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return _Row(row) if row is not None else None

    def fetchall(self):
        return [_Row(r) for r in (self._cur.fetchall() or [])]

    def __iter__(self):
        return iter(self.fetchall())

    def __getitem__(self, index):
        return self.fetchall()[index]

    @property
    def rowcount(self):
        return self._cur.rowcount if self._cur else -1


# ── Connection wrapper ────────────────────────────────────────────────────────

class DbAdapter:
    """
    Wraps a psycopg2 connection to expose the same interface as sqlite3.Connection,
    translating SQLite-specific SQL on the fly.
    """

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, query, params=()):
        cur = _CursorWrapper(self._conn)
        return cur.execute(query, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self):
        return _CursorWrapper(self._conn)


# ── PRAGMA emulation for schema inspection ────────────────────────────────────

def pg_table_columns(conn, table: str) -> list[str]:
    """Return column names for *table* using information_schema (replaces PRAGMA)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = %s""",
            (table,),
        )
        return [row[0] for row in cur.fetchall()]
