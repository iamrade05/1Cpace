"""
Connection + schema management for platform.db — the small tenant registry
that lists every gym on this 1Cpase install (slug, name, logo, which SQLite
file holds its data). Never holds any gym's actual data.
"""
import sqlite3
from pathlib import Path

from flask import current_app, g

from .schema import PLATFORM_SCHEMA_SQL


def get_platform_db():
    if "platform_db" not in g:
        path = Path(current_app.config["PLATFORM_DATABASE_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        g.platform_db = conn
    return g.platform_db


def close_platform_db(e=None):
    db = g.pop("platform_db", None)
    if db is not None:
        db.close()


def resolve_integration_tenant(provider: str, external_id: str):
    """Resolve a public webhook to its owning tenant using provider identity.

    This intentionally does not consult the browser/session tenant cookie.
    Returns a tenant row or None when the provider identity is unknown/inactive.
    """
    provider = str(provider or "").strip().lower()
    external_id = str(external_id or "").strip()
    if not provider or not external_id:
        return None
    row = get_platform_db().execute(
        """SELECT t.* FROM tenant_integrations ti
           JOIN tenants t ON t.id = ti.tenant_id
          WHERE ti.provider=? AND ti.external_id=?
            AND ti.active=1 AND t.active=1""",
        (provider, external_id),
    ).fetchone()
    return dict(row) if row else None


def register_integration(provider: str, external_id: str, tenant_slug: str, integration_name: str = "") -> bool:
    """Register a provider identity for a tenant. Used by controlled setup/migration code."""
    tenant = get_platform_db().execute(
        "SELECT * FROM tenants WHERE slug=? AND active=1", (tenant_slug,)
    ).fetchone()
    if not tenant or not provider or not external_id:
        return False
    db = get_platform_db()
    db.execute(
        """INSERT INTO tenant_integrations
           (tenant_id, provider, external_id, integration_name, active)
           VALUES (?,?,?,?,1)
           ON CONFLICT(provider, external_id) DO UPDATE SET
             tenant_id=excluded.tenant_id, integration_name=excluded.integration_name, active=1""",
        (tenant["id"], str(provider).strip().lower(), str(external_id).strip(), integration_name or None),
    )
    db.commit()
    return True

def init_platform_db() -> None:
    """Create the registry tables, then seed the first tenant (Elev8) so an
    existing single-tenant install upgrades in place — the existing
    DATABASE_PATH file is left untouched, just registered."""
    db = get_platform_db()
    for statement in PLATFORM_SCHEMA_SQL.split(";"):
        if statement.strip():
            db.execute(statement)
    db.commit()

    existing = db.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
    if existing == 0:
        db.execute(
            """INSERT INTO tenants (slug, name, db_path, active)
               VALUES (?, ?, ?, 1)""",
            ("elev8", "Elev8 Health and Fitness Gym", current_app.config["DATABASE_PATH"]),
        )
        db.commit()

    # Backward-compatible bootstrap for the existing single-tenant deployment.
    # Once tenant integrations are managed explicitly, these environment-backed
    # identities are simply the initial registry entries; webhook handlers never
    # use the browser tenant cookie to resolve them.
    tenant_slug = current_app.config.get("ACCESS_REPORT_TENANT_SLUG", "elev8")
    configured = [
        ("whatsapp", current_app.config.get("WA_RECEPTION_PHONE_ID", ""), "reception"),
        ("whatsapp", current_app.config.get("WA_SALES_PHONE_ID", ""), "sales"),
        ("facebook", current_app.config.get("FB_PAGE_ID", ""), "facebook page"),
    ]
    tenant = db.execute("SELECT id FROM tenants WHERE slug=? AND active=1", (tenant_slug,)).fetchone()
    if tenant:
        for provider, external_id, name in configured:
            external_id = str(external_id or "").strip()
            if not external_id:
                continue
            db.execute(
                """INSERT INTO tenant_integrations
                   (tenant_id, provider, external_id, integration_name, active)
                   VALUES (?,?,?,?,1)
                   ON CONFLICT(provider, external_id) DO UPDATE SET
                     tenant_id=excluded.tenant_id, integration_name=excluded.integration_name, active=1""",
                (tenant["id"], provider, external_id, name),
            )
        db.commit()
