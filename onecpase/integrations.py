"""External service integrations (Itensity, etc.).

Itensity has no supported public API — membership is entered through a legacy
PHP web form. Pushes are automated with the Playwright driver in itensity_driver/
(RADEPULSE_PATH) instead of a REST client, the same way debicheck.py hands
DebiCheck mandates off to the NuPay driver.
"""
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import current_app, g, has_app_context

from .playwright_lock import PLAYWRIGHT_LOCK


def itensity_tenant_enabled():
    return bool(has_app_context() and g.get('tenant_slug') and
                g.tenant_slug == current_app.config.get('ITENSITY_TENANT_SLUG', 'elev8'))


def bind_itensity_tenant(db_path):
    """Bind background work to its database's registered integration owner."""
    from .platform_db import get_platform_db
    target = Path(db_path).resolve()
    owners = [dict(row) for row in get_platform_db().execute('SELECT * FROM tenants WHERE active=1')
              if Path(row['db_path']).resolve() == target]
    if len(owners) != 1:
        raise PermissionError('The integration database has no unique active tenant owner.')
    g.tenant, g.tenant_slug = owners[0], owners[0]['slug']
    if not itensity_tenant_enabled():
        raise PermissionError('The shared Itensity integration is not configured for this gym.')


class ItensityNotSubmitted(str):
    """A push failure from before Submit: no member can exist, so a retry is safe."""


def itensity_writes_allowed(application_id) -> bool:
    """Whether this application may write to the real Itensity."""
    allowed = str(current_app.config.get("ITENSITY_PUSH_ALLOWED") or "").strip().lower()
    if allowed == "all":
        return True
    return str(application_id) in {part.strip() for part in allowed.split(",") if part.strip()}


def _load_driver(module_name: str, filename: str):
    """Dynamically import one of the Itensity push drivers and load its .env
    for credentials — mirrors debicheck.py's NuPay driver hand-off."""
    import importlib.util

    if not itensity_tenant_enabled():
        return None, 'The shared Itensity integration is not configured for this gym.'

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
        return False, None, ItensityNotSubmitted(
            "Itensity push isn't supported yet for third-party payer mandates — "
            "the driver assumes the payer is always the member. Push manually in Itensity."
        )

    module, err = _load_driver("onecpase_itensity_push", "push_to_itensity.py")
    if module is None:
        return False, None, ItensityNotSubmitted(err)

    data = _prepare_member_for_itensity(member_data)
    validation_errors = module.validate_member(data)
    if validation_errors:
        return False, None, ItensityNotSubmitted("; ".join(validation_errors))

    # The driver hands the new reference to _save_itensity_ref; capture it here
    # so the caller records it in this tenant's database.
    captured: Dict[str, str] = {}
    PLAYWRIGHT_LOCK.acquire()
    original_save = module._save_itensity_ref
    module._save_itensity_ref = lambda member_id, ref: captured.update(ref=ref)
    try:
        module.push(data, headless=True, submit=True)
    except module.ItensityPushError as exc:
        if getattr(exc, "submitted", True):
            return False, None, str(exc)
        return False, None, ItensityNotSubmitted(str(exc))
    except Exception as exc:
        return False, None, f"Itensity push failed: {exc}"
    finally:
        module._save_itensity_ref = original_save
        PLAYWRIGHT_LOCK.release()

    ref = captured.get("ref")
    if not ref:
        return False, None, "Itensity did not confirm a member reference after submission."
    return True, str(ref), None


def preview_member_push(member_data: Dict) -> Tuple[Optional[Dict], Optional[str]]:
    """Fill Add Member without pressing Submit; nothing is saved in Itensity.

    Returns (report of field names and problems, error message).
    """
    module, err = _load_driver("onecpase_itensity_push", "push_to_itensity.py")
    if module is None:
        return None, err

    data = _prepare_member_for_itensity(member_data)
    validation_errors = module.validate_member(data)
    if validation_errors:
        return None, "; ".join(validation_errors)

    PLAYWRIGHT_LOCK.acquire()
    try:
        return module.push(data, headless=True, submit=False), None
    except module.ItensityPushError as exc:
        return None, str(exc)
    finally:
        PLAYWRIGHT_LOCK.release()


def attach_member_documents_to_itensity(itensity_ref: str, surname: str, documents: List[Dict]) -> List[Dict]:
    """Upload files to an Itensity profile, one at a time.

    `documents` hold document_id, path, title and kind ('contract' or
    'compliance'). Every document gets a result with status 'sent',
    'already_attached', 'failed' (nothing reached Itensity) or 'unknown' (it may
    have), so one bad file never loses the others.
    """
    module, err = _load_driver("onecpase_itensity_push", "push_to_itensity.py")
    if module is None:
        return [{"document_id": doc["document_id"], "status": "failed", "error": err} for doc in documents]

    results = []
    PLAYWRIGHT_LOCK.acquire()
    try:
        with module.itensity_session(headless=True) as page:
            for doc in documents:
                try:
                    status = module.attach_document(
                        page, itensity_ref, surname, doc["path"], doc["title"], doc["kind"]
                    )
                    results.append({"document_id": doc["document_id"], "status": status, "error": None})
                except module.ItensityPushError as exc:
                    results.append({
                        "document_id": doc["document_id"],
                        "status": "unknown" if exc.submitted else "failed",
                        "error": str(exc),
                    })
                except Exception as exc:
                    results.append({
                        "document_id": doc["document_id"], "status": "unknown",
                        "error": f"Upload stopped: {type(exc).__name__}",
                    })
    except module.ItensityPushError as exc:
        done = {result["document_id"] for result in results}
        results += [{"document_id": doc["document_id"], "status": "failed", "error": str(exc)}
                    for doc in documents if doc["document_id"] not in done]
    except Exception as exc:
        done = {result["document_id"] for result in results}
        results += [{"document_id": doc["document_id"], "status": "unknown",
                     "error": f"Upload stopped: {type(exc).__name__}"}
                    for doc in documents if doc["document_id"] not in done]
    finally:
        PLAYWRIGHT_LOCK.release()
    return results


def record_reg_fee_in_itensity(itensity_ref: str, surname: str, collection: Dict) -> Dict:
    """Record a paid reg fee on the Itensity Payment Profile, once.

    `collection` holds amount, receipt, paid_on and payment_type. Returns status
    'sent', 'already_in_itensity', 'failed' (nothing written) or 'unknown' (it
    may have been written: never resend without checking Itensity).
    """
    module, err = _load_driver("onecpase_itensity_push", "push_to_itensity.py")
    if module is None:
        return {"status": "failed", "fee_detected": False, "error": err}

    PLAYWRIGHT_LOCK.acquire()
    try:
        with module.itensity_session(headless=True) as page:
            result = module.record_reg_fee(
                page, itensity_ref, surname, collection["amount"], collection["receipt"],
                collection["paid_on"], collection["payment_type"],
                add_fee_first=bool(current_app.config.get("ITENSITY_REG_FEE_ADD_FEE_FIRST")),
            )
            return {**result, "error": None}
    except module.ItensityPushError as exc:
        return {"status": "unknown" if exc.submitted else "failed", "fee_detected": False, "error": str(exc)}
    except Exception as exc:
        return {"status": "unknown", "fee_detected": False, "error": f"Payment stopped: {type(exc).__name__}"}
    finally:
        PLAYWRIGHT_LOCK.release()


def html_to_pdf(html: str, output_path: str) -> Optional[str]:
    """Render HTML to a PDF file. Returns an error message, or None."""
    module, err = _load_driver("onecpase_itensity_push", "push_to_itensity.py")
    if module is None:
        return err
    PLAYWRIGHT_LOCK.acquire()
    try:
        module.html_to_pdf(html, output_path)
        return None
    except Exception as exc:
        return f"Could not make a PDF: {type(exc).__name__}"
    finally:
        PLAYWRIGHT_LOCK.release()


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

    PLAYWRIGHT_LOCK.acquire()
    original_member_from_args = module.member_from_args
    module.member_from_args = lambda _args: dict(member_data)
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
