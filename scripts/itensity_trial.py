"""Run one Itensity step at a time for a single application, for a supervised trial.

    python -m scripts.itensity_trial check 2148       # 1Cpace only
    python -m scripts.itensity_trial preview 2148     # fills the form, never presses Submit
    python -m scripts.itensity_trial push 2148 --user 1 --confirm 2148
    python -m scripts.itensity_trial documents 2148 --user 1 --confirm 2148
    python -m scripts.itensity_trial reg-fee 2148 --user 1 --confirm 2148

Anything that writes to Itensity needs --confirm with the same application id,
and that id must be in ITENSITY_PUSH_ALLOWED. Output never holds member details.
"""
import argparse
import os
import sys

# A second process must not start the app's background jobs.
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["WHATSAPP_WEB_ENABLED"] = "0"
os.environ["TURNSTILE_ENABLED"] = "0"

from onecpase import create_app
from onecpase.applications import _background_itensity_push, _itensity_hard_blockers, _mark_itensity_push_started
from onecpase.database import get_db
from onecpase.encryption import decrypt_member
from onecpase.integrations import (
    _prepare_member_for_itensity,
    bind_itensity_tenant,
    itensity_writes_allowed,
    preview_member_push,
)
from onecpase.itensity_followups import reg_fee_to_send, run_followups
from onecpase.platform_db import get_platform_db

WRITE_STEPS = ("push", "documents", "reg-fee")
# What the push needs. Yes or no only.
PUSH_FIELDS = ("first_name", "last_name", "id_number", "contact", "join_date", "tariff", "contract_duration",
               "payment_type", "bank", "account_number", "branch_code", "account_type", "debit_order_date",
               "date_of_birth", "gender", "email", "street_name", "city")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=("check", "preview", *WRITE_STEPS))
    parser.add_argument("application", type=int)
    parser.add_argument("--user", type=int, help="1Cpace user making the change")
    parser.add_argument("--confirm", type=int, help="repeat the application id to allow a write")
    args = parser.parse_args()

    if args.step in WRITE_STEPS and (args.confirm != args.application or not args.user):
        print("Refused: this writes to the real Itensity, so it needs --user and --confirm <application id>.")
        return 2

    app = create_app()
    with app.app_context():
        tenant = get_platform_db().execute(
            "SELECT db_path FROM tenants WHERE slug=? AND active=1", (app.config["ITENSITY_TENANT_SLUG"],)
        ).fetchone()
        db_path = tenant["db_path"]
        bind_itensity_tenant(db_path)
        db = get_db()
        application = db.execute(
            "SELECT id,member_id,application_status,push_status FROM membership_applications WHERE id=?",
            (args.application,),
        ).fetchone()
        if not application:
            print("No such application.")
            return 2
        member_id = application["member_id"]
        member = _prepare_member_for_itensity(
            decrypt_member(db.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone())
        )
        print(f"Application {application['id']}: {application['application_status']} / {application['push_status']}")

        if args.step == "check":
            print("Push allowed by ITENSITY_PUSH_ALLOWED:", itensity_writes_allowed(args.application))
            print("Filled:", {field: bool(str(member.get(field) or "").strip()) for field in PUSH_FIELDS})
            print("Blockers:", _itensity_hard_blockers(db, args.application, member_id))
            print("Documents:", [
                (row["document_type"], row["original_name"].rsplit(".", 1)[-1].lower())
                for row in db.execute("SELECT document_type,original_name FROM member_documents WHERE member_id=?",
                                      (member_id,))
            ])
            print("Member signature on file:", bool(db.execute(
                "SELECT 1 FROM contract_signatures WHERE member_id=? AND signer_role='member'", (member_id,)
            ).fetchone()))
            fee, reason = reg_fee_to_send(db, member_id)
            print("Reg fee:", {k: fee[k] for k in ("amount", "paid_on", "payment_type")} if fee else reason)
            return 0

        if args.step == "preview":
            report, error = preview_member_push(member)
            print("Stopped:", error) if error else print("Report:", report)
            return 1 if error or report["wrong"] or report["invalid_required"] else 0

        if not itensity_writes_allowed(args.application):
            print("Refused: this application is not in ITENSITY_PUSH_ALLOWED.")
            return 2

        if args.step == "push":
            if not _mark_itensity_push_started(db, args.application):
                print("Refused: this application can't be pushed in its current state.")
                return 2
            db.commit()
            _background_itensity_push(
                app, db_path, args.application, member_id, member.get("itensity_ref"), False, [], args.user,
            )
            row = db.execute("SELECT application_status,push_status,itensity_confirmed_ref FROM membership_applications WHERE id=?",
                             (args.application,)).fetchone()
            latest = db.execute(
                "SELECT action,new_value FROM audit_logs WHERE application_id=? ORDER BY id DESC LIMIT 1",
                (args.application,),
            ).fetchone()
            print("Result:", dict(row))
            print("Latest audit:", dict(latest) if latest else None)
            return 0 if row["push_status"] == "pushed" else 1

        part = "documents" if args.step == "documents" else "reg_fee"
        outcome = run_followups(app, db_path, args.application, member_id, args.user, (part,))
        print("Result:", outcome)
        return 1 if outcome.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
