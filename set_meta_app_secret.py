#!/usr/bin/env python3
"""Set the Meta App Secret used by this checkout's Facebook/WhatsApp webhooks.

Meta does not expose App Secrets through its Graph API. Copy the secret from
Meta for Developers > your app > App settings > Basic, then run this helper.
The secret is entered without echo and is written only to this checkout's
git-ignored .env file. This script does not contact Meta or print the secret.
"""

from __future__ import annotations

import os
import re
import tempfile
from getpass import getpass
from pathlib import Path


ENV_FILE = Path(__file__).resolve().with_name(".env")
SETTING = "FB_APP_SECRET"


def main() -> int:
    secret = getpass("Paste the Meta App Secret (input hidden): ").strip()
    if not secret:
        print("No secret entered; .env was not changed.")
        return 1
    if "\n" in secret or "\r" in secret:
        print("The secret must be a single line; .env was not changed.")
        return 1

    old_text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    newline = "\r\n" if "\r\n" in old_text else "\n"
    lines = old_text.splitlines()
    assignment = f"{SETTING}={secret}"
    setting_re = re.compile(rf"^\s*{SETTING}\s*=")

    replaced = False
    new_lines = []
    for line in lines:
        if setting_re.match(line):
            if not replaced:
                new_lines.append(assignment)
                replaced = True
            # Remove duplicate FB_APP_SECRET entries to keep one active value.
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(assignment)

    new_text = newline.join(new_lines) + newline
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=ENV_FILE.parent,
            prefix=".env.tmp-",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(new_text)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            # Windows ACLs control access there; chmod is best-effort only.
            pass
        os.replace(temp_path, ENV_FILE)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()

    print(f"{SETTING} saved to {ENV_FILE.name}; the value was not displayed.")
    print("Restart the app for the new setting to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
