"""The Itensity push reads the same statement verdict as NuPay submission."""
import json

from onecpase.applications import _push_analysis
from onecpase.database import get_db

from test_debicheck_routes import _ready_to_submit


def test_a_passing_statement_counts_even_when_the_saved_analysis_says_no(client):
    member_id, _mandate_id = _ready_to_submit(client)
    with client.application.app_context():
        db = get_db()
        db.execute("UPDATE statement_analyses SET verification_qualified=0")
        stale = db.execute("SELECT * FROM statement_analyses ORDER BY id DESC LIMIT 1").fetchone()
        assert _push_analysis(db, member_id, stale) == {"verification_qualified": 1}

        row = db.execute("SELECT id, analysis_json FROM member_documents WHERE member_id=?", (member_id,)).fetchone()
        failing = {**json.loads(row["analysis_json"]), "verification_qualified": False}
        db.execute("UPDATE member_documents SET analysis_json=? WHERE id=?", (json.dumps(failing), row["id"]))
        assert _push_analysis(db, member_id, stale) is stale
