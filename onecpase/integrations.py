"""External service integrations (Itensity, etc.).

Itensity has no supported public API — membership is entered through a legacy
PHP web form. Pushes are automated with the proven Playwright driver from the
sibling 1Cpace app (RADEPULSE_PATH) instead of a REST client, the same way
debicheck.py hands DebiCheck mandates off to the NuPay driver.
"""
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv
from flask import current_app

from .playwright_lock import PLAYWRIGHT_LOCK


def _load_driver(module_name: str, filename: str):
    """Dynamically import one of the sibling 1Cpace push drivers and load its
    .env for credentials — mirrors debicheck.py's NuPay driver hand-off."""
    import importlib.util

    driver_root = Path(current_app.config["RADEPULSE_PATH"])
    driver_path = driver_root / filename
    if not driver_path.exists():
        return None, f"Driver not found at {driver_path}"

    load_dotenv(driver_root / ".env", override=False)

    # The reference scripts do a bare `import encryption` — make their folder
    # importable so that resolves. Appended (not prepended) to avoid shadowing
    # any same-named module this app already relies on.
    if str(driver_root) not in sys.path:
        sys.path.append(str(driver_root))

    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, driver_path)
        if spec is None or spec.loader is None:
            return None, "Unable to load driver"
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            del sys.modules[module_name]
            return None, f"Failed to load {filename}: {exc}"
    return module, None


def _consultant_name(member_data: Dict) -> str:
    uploaded_by_id = member_data.get("uploaded_by_id")
    if uploaded_by_id:
        try:
            from .database import get_db
            row = get_db().execute(
                "SELECT full_name FROM users WHERE id = ?", (uploaded_by_id,)
            ).fetchone()
            if row and row["full_name"]:
                return row["full_name"]
        except Exception:
            pass
    return "Mvuleni Radebe"  # same fallback the reference driver was built around


def _prepare_member_for_itensity(member_data: Dict) -> Dict:
    """Adapt a 1Cpase member row to what push_to_itensity.py expects."""
    data = dict(member_data)
    # Our opt_in is stored as the string "yes"/"no"; the driver expects a
    # plain truthy/falsy value.
    data["opt_in"] = str(data.get("opt_in", "yes")).strip().lower() not in ("no", "0", "false", "")
    data.setdefault("sales_person", None)
    if not data["sales_person"]:
        data["sales_person"] = _consultant_name(member_data)
    return data


def push_member_to_itensity(member_data: Dict) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Push a new member to Itensity via the proven browser-automation driver.

    Returns:
        Tuple of (success: bool, itensity_ref: Optional[str], error_message: Optional[str])
    """
    if member_data.get("payment_type") == "Third-Party Debit Order":
        return False, None, (
            "Itensity push isn't supported yet for third-party payer mandates — "
            "the driver assumes the payer is always the member. Push manually in Itensity."
        )

    module, err = _load_driver("onecpase_itensity_push", "push_to_itensity.py")
    if module is None:
        return False, None, err

    data = _prepare_member_for_itensity(member_data)
    validation_errors = module.validate_member(data)
    if validation_errors:
        return False, None, "; ".join(validation_errors)

    # push() saves the Itensity ref into the *reference app's own* database
    # (1Cpace.db) via _save_itensity_ref — not ours. Intercept that call
    # instead of letting it write to the wrong database.
    captured: Dict[str, str] = {}
    original_save = module._save_itensity_ref
    module._save_itensity_ref = lambda member_id, ref: captured.update(ref=ref)
    PLAYWRIGHT_LOCK.acquire()
    try:
        module.push(data, headless=True, submit=True)
    except module.ItensityPushError as exc:
        return False, None, str(exc)
    except Exception as exc:
        return False, None, f"Itensity push failed: {exc}"
    finally:
        module._save_itensity_ref = original_save
        PLAYWRIGHT_LOCK.release()

    ref = captured.get("ref")
    if not ref:
        return False, None, "Itensity did not confirm a member reference after submission."
    return True, str(ref), None


def update_member_in_itensity(itensity_ref: str, member_data: Dict) -> Tuple[bool, Optional[str]]:
    """
    The current driver (push_to_itensity.py) only automates the Add Member
    flow — there's no reference implementation for editing an existing
    Itensity member, so this is intentionally unsupported rather than guessed.
    """
    return False, (
        "Updating an existing Itensity member isn't supported by the current driver. "
        "Update the member directly in Itensity."
    )


def push_pos_payment_to_itensity(
    member_data: Dict, amount: float, description: str = "Member payment",
    payment_type: str = "Cash", reference: str = "", pos: str = "Reception",
) -> Tuple[bool, Optional[str]]:
    """
    Record a POS payment against a member's Itensity account via the proven
    browser-automation driver.

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    module, err = _load_driver("onecpase_itensity_pos_push", "push_pos_to_itensity.py")
    if module is None:
        return False, err

    if not (member_data.get("itensity_ref") or member_data.get("unique_ref")):
        return False, "Member has no Itensity reference — push the member to Itensity first."

    import argparse
    args = argparse.Namespace(
        member_id=member_data.get("id"),
        amount=amount,
        description=description,
        payment_type=payment_type,
        reference=reference,
        pos=pos,
        statement_entry_id="",
        itensity_ref="",
        headless=True,
        smoke=False,
    )

    original_member_from_args = module.member_from_args
    module.member_from_args = lambda _args: dict(member_data)
    PLAYWRIGHT_LOCK.acquire()
    try:
        module.push_pos(args)
    except module.ItensityPosError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Itensity POS push failed: {exc}"
    finally:
        module.member_from_args = original_member_from_args
        PLAYWRIGHT_LOCK.release()

    return True, None
