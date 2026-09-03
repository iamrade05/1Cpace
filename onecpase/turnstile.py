"""USB HID turnstile detection and read-only event monitoring.

The connected controller is vendor-defined HID hardware. Until its output
protocol is confirmed, this module intentionally never writes to the device.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "enabled": False,
    "connected": False,
    "vendor_id": None,
    "product_id": None,
    "manufacturer": None,
    "product": None,
    "serial_number": None,
    "path": None,
    "last_error": None,
    "last_report_hex": None,
    "last_credential": None,
    "last_event_at": None,
    "reports_received": 0,
    "output_enabled": False,
    "output_protocol_confirmed": False,
}
_monitor_thread: threading.Thread | None = None


def _set_state(**values) -> None:
    with _state_lock:
        _state.update(values)


def get_turnstile_status() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


def enumerate_turnstile(vendor_id: int, product_id: int) -> dict[str, Any] | None:
    try:
        import hid
    except ImportError:
        _set_state(last_error="hidapi is not installed")
        return None
    devices = hid.enumerate(vendor_id, product_id)
    if not devices:
        return None
    return devices[0]


def decode_credential(report: bytes) -> str:
    """Create a stable credential token without assuming a vendor layout."""
    payload = report.rstrip(b"\x00")
    if not payload:
        return ""
    printable = bytes(byte for byte in payload if byte not in (0, 10, 13))
    try:
        text = printable.decode("ascii").strip()
    except UnicodeDecodeError:
        text = ""
    if text and all(character.isprintable() for character in text):
        return text
    return payload.hex().upper()


def evaluate_access(connection, credential: str):
    if not credential:
        return None, "unmatched", "Empty HID report"
    member = connection.execute(
        """SELECT id, first_name, last_name, member_status, gym_access_status,
                  access_blocked_until
           FROM members
           WHERE access_credential = ? OR itensity_ref = ?
           ORDER BY CASE WHEN access_credential = ? THEN 0 ELSE 1 END
           LIMIT 1""",
        (credential, credential, credential),
    ).fetchone()
    if member is None:
        return None, "unmatched", "Credential is not linked to a member"
    if str(member["member_status"] or "").lower() != "active":
        return member, "denied", f"Membership status is {member['member_status'] or 'unknown'}"
    access_status = member["gym_access_status"] or "allowed"
    # Collections policy is evaluated live at the gate so a stale member row
    # cannot accidentally grant access after an arrears threshold is crossed.
    try:
        from .collections_engine import evaluate_member_access
        live = evaluate_member_access(connection, member["id"])
        if live.get("access") == "BLOCKED":
            return member, "denied", f"Collections rule: {live.get('action', 'access restricted')}"
    except Exception:
        # Do not grant access on a collections-policy evaluation failure.
        return member, "denied", "Collections access policy could not be verified"
    if access_status == "blocked":
        return member, "denied", "Gym access is blocked"
    if access_status == "temp_unblocked":
        blocked_until = str(member["access_blocked_until"] or "")[:10]
        if blocked_until and blocked_until < date.today().isoformat():
            return member, "denied", "Temporary access has expired"
    return member, "granted", "Active member with allowed access"


def _record_report(database_path: str, serial_number: str, report: bytes, credential: str) -> None:
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        member, decision, reason = evaluate_access(connection, credential)
        connection.execute(
            """INSERT INTO turnstile_events
               (device_serial, raw_report_hex, credential, member_id, decision, reason, direction)
               VALUES (?, ?, ?, ?, ?, ?, 'unknown')""",
            (
                serial_number, report.hex().upper(), credential or None,
                member["id"] if member else None, decision, reason,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _monitor(config: dict[str, Any]) -> None:
    import hid

    vendor_id = config["vendor_id"]
    product_id = config["product_id"]
    database_path = config["database_path"]
    last_report = b""
    last_report_at = 0.0

    while True:
        device = None
        try:
            info = enumerate_turnstile(vendor_id, product_id)
            if info is None:
                _set_state(connected=False, last_error="Turnstile not connected")
                time.sleep(3)
                continue

            device = hid.device()
            device.open_path(info["path"])
            device.set_nonblocking(False)
            path = info["path"]
            if isinstance(path, bytes):
                path = path.decode(errors="replace")
            _set_state(
                connected=True,
                manufacturer=info.get("manufacturer_string"),
                product=info.get("product_string"),
                serial_number=info.get("serial_number"),
                path=path,
                last_error=None,
            )

            while True:
                data = device.read(64, 1000)
                if not data:
                    continue
                report = bytes(data)
                now = time.monotonic()
                if report == last_report and now - last_report_at < 0.75:
                    continue
                last_report = report
                last_report_at = now
                credential = decode_credential(report)
                _record_report(database_path, str(info.get("serial_number") or ""), report, credential)
                status = get_turnstile_status()
                _set_state(
                    last_report_hex=report.hex().upper(),
                    last_credential=credential,
                    last_event_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    reports_received=int(status.get("reports_received") or 0) + 1,
                )
        except Exception as exc:
            logger.warning("Turnstile monitor disconnected: %s", exc)
            _set_state(connected=False, last_error=str(exc))
            time.sleep(2)
        finally:
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass


def start_turnstile_monitor(app) -> None:
    """Start one daemon monitor for the current application process."""
    global _monitor_thread
    enabled = bool(app.config.get("TURNSTILE_ENABLED"))
    vendor_id = int(app.config.get("TURNSTILE_VENDOR_ID", 0x4825))
    product_id = int(app.config.get("TURNSTILE_PRODUCT_ID", 0x8701))
    output_enabled = bool(app.config.get("TURNSTILE_OUTPUT_ENABLED"))
    _set_state(
        enabled=enabled,
        vendor_id=f"{vendor_id:04X}",
        product_id=f"{product_id:04X}",
        output_enabled=output_enabled,
        output_protocol_confirmed=False,
    )
    if not enabled or app.testing or app.config.get("DATABASE_URL"):
        return
    if _monitor_thread and _monitor_thread.is_alive():
        return
    database_path = str(Path(app.config["DATABASE_PATH"]).resolve())
    _monitor_thread = threading.Thread(
        target=_monitor,
        args=({
            "vendor_id": vendor_id,
            "product_id": product_id,
            "database_path": database_path,
        },),
        name="turnstile-hid-monitor",
        daemon=True,
    )
    _monitor_thread.start()
