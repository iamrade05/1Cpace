"""Read-only access to the Elev8 source SQL Server data.

The Elev8 SQL Server database is the authoritative source for the current
member roster/count. 1Cpace keeps its own operational copy, but UI totals
must not silently become stale when that copy has not been synchronised.
"""

import os


def _connection_string() -> str:
    return (
        os.environ.get("ELEV8_SQL_CONNECTION_STRING")
        or "DRIVER={ODBC Driver 17 for SQL Server};"
           "SERVER=tcp:RADE,1433;"
           "DATABASE=ELEV8_;"
           "Trusted_Connection=yes;"
           "Encrypt=no;"
           "TrustServerCertificate=yes;"
    )


def get_elev8_member_count() -> int | None:
    """Return the authoritative current member count from dbo.Members.

    Returns None when the external database is unavailable so callers can
    retain a safe local-data fallback rather than displaying a fabricated 0.
    """
    try:
        import pyodbc
        conn = pyodbc.connect(_connection_string(), timeout=8)
        try:
            row = conn.cursor().execute("SELECT COUNT(*) FROM dbo.Members").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return None
