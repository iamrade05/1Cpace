"""Create or reset the platform Power User.

The Power User signs in at ``/platform/login`` and administers the whole
1Cpase installation: it provisions tenants and can open any active tenant
without knowing that tenant's own administrator password. It is deliberately
not a tenant user, so it cannot be created from any in-app screen — this
script is the only way in, and the account it writes is the root of trust for
every tenant on the install.

Create the first Power User::

    $env:PLATFORM_ADMIN_USERNAME='owner'
    $env:PLATFORM_ADMIN_PASSWORD='use-a-strong-unique-password'
    python seed_platform_admin.py

Set a new password for an existing Power User::

    python seed_platform_admin.py --reset

The credentials are read from the environment rather than argv so the
password never lands in shell history or the process list.
"""
from __future__ import annotations

import argparse
import os
import sys

from flask import Flask
from werkzeug.security import generate_password_hash

from onecpase.config import Config
from onecpase.platform_db import get_platform_db, init_platform_db


# Matches the in-app password rule in onecpase/auth.py:change_password.
MIN_PASSWORD_LENGTH = 8
# Below this the account still works; the Power User reaches every tenant's
# data, so a short password is worth saying out loud.
ADVISED_PASSWORD_LENGTH = 12


class SeedError(Exception):
    """A problem the operator has to resolve, reported without a traceback."""


def normalise_username(raw: str) -> str:
    """Match how /platform/login looks the account up."""
    username = str(raw or "").strip().lower()
    if not username:
        raise SeedError("PLATFORM_ADMIN_USERNAME is required.")
    if any(character.isspace() for character in username):
        raise SeedError("PLATFORM_ADMIN_USERNAME cannot contain spaces.")
    return username


def validate_password(password: str) -> None:
    if not password:
        raise SeedError("PLATFORM_ADMIN_PASSWORD is required.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SeedError(
            f"PLATFORM_ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters."
        )


def upsert_power_user(db, username: str, password: str, *, reset: bool = False) -> str:
    """Create the Power User, or replace its password when *reset* is set.

    Returns the message to show the operator. Raises SeedError when the
    request contradicts what is already in the registry, so an accidental
    re-run can never silently take over an existing account.
    """
    username = normalise_username(username)
    validate_password(password)

    existing = db.execute(
        "SELECT id FROM platform_admins WHERE username = ?", (username,)
    ).fetchone()

    if reset:
        if existing is None:
            raise SeedError(
                f"No Power User named '{username}' exists. "
                "Run without --reset to create it."
            )
        db.execute(
            "UPDATE platform_admins SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), existing["id"]),
        )
        db.commit()
        return f"Password reset for Power User '{username}'."

    if existing is not None:
        raise SeedError(
            f"A Power User named '{username}' already exists. "
            "Use --reset to set a new password for it."
        )
    db.execute(
        "INSERT INTO platform_admins (username, password_hash) VALUES (?, ?)",
        (username, generate_password_hash(password)),
    )
    db.commit()
    return f"Power User '{username}' created. Sign in at /platform/login."


def _platform_app() -> Flask:
    """A config-only app.

    create_app() would also open every tenant database and start the turnstile
    monitor and scheduler threads, none of which a one-shot CLI wants.
    """
    app = Flask(__name__)
    app.config.from_object(Config)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or reset the 1Cpase platform Power User.",
        epilog="Credentials are read from PLATFORM_ADMIN_USERNAME and PLATFORM_ADMIN_PASSWORD.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="set a new password for an existing Power User instead of creating one",
    )
    args = parser.parse_args(argv)

    username = os.environ.get("PLATFORM_ADMIN_USERNAME", "")
    password = os.environ.get("PLATFORM_ADMIN_PASSWORD", "")

    app = _platform_app()
    try:
        with app.app_context():
            # Creates the registry tables if this is a fresh install, exactly
            # as the running application does at startup.
            init_platform_db()
            message = upsert_power_user(
                get_platform_db(), username, password, reset=args.reset
            )
    except SeedError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(message)
    if len(password) < ADVISED_PASSWORD_LENGTH:
        print(
            f"warning: this password is under {ADVISED_PASSWORD_LENGTH} characters, "
            "and the Power User can open every tenant on this install.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
