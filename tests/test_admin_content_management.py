from onecpase.database import get_db


def _login_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"


def test_admin_can_manage_contract_membership_and_report_content(app):
    with app.test_client() as client:
        _login_admin(client)

        assert client.get("/admin/contract-templates/add").status_code == 200
        assert client.get("/admin/report-definitions/add").status_code == 200

        contract_response = client.post(
            "/admin/contract-templates/add",
            data={
                "name": "Premium Agreement 2026",
                "gym_name": "ELEV8 Test Gym",
                "agreement_title": "Premium Membership Agreement",
                "terms_text": "First editable term.\nSecond editable term.",
                "footer_text": "Editable footer.",
                "active": "1",
                "is_default": "1",
            },
            follow_redirects=True,
        )
        assert contract_response.status_code == 200
        assert b"Premium Agreement 2026" in contract_response.data

        plan_response = client.post(
            "/admin/tariffs/add",
            data={
                "name": "Test Flex Membership",
                "duration_months": "12",
                "monthly_amount": "299.00",
                "sort_order": "20",
            },
            follow_redirects=True,
        )
        assert plan_response.status_code == 200
        assert b"Test Flex Membership" in plan_response.data
        assert b"Membership Plans" in plan_response.data

        report_response = client.post(
            "/admin/report-definitions/add",
            data={
                "name": "Active Member Contacts",
                "description": "Reusable member contact list",
                "table": "members",
                "cols": ["first_name", "last_name", "contact", "member_status"],
                "f1_field": "member_status",
                "f1_op": "eq",
                "f1_val": "Active",
                "sort_by": "last_name",
                "sort_dir": "asc",
                "limit": "200",
                "active": "1",
            },
            follow_redirects=True,
        )
        assert report_response.status_code == 200
        assert b"Active Member Contacts" in report_response.data

        with app.app_context():
            db = get_db()
            default_contract = db.execute(
                "SELECT * FROM contract_templates WHERE is_default=1"
            ).fetchone()
            saved_report = db.execute(
                "SELECT * FROM report_definitions WHERE name='Active Member Contacts'"
            ).fetchone()
            cursor = db.execute(
                """INSERT INTO members
                   (member_ref, first_name, last_name, id_number, tariff,
                    contract_duration, monthly_installment)
                   VALUES ('TST-0000000001', 'Admin', 'Preview', '9001010000001',
                           'Test Flex Membership', '12 months', 299)"""
            )
            db.commit()
            member_id = cursor.lastrowid
            assert default_contract["agreement_title"] == "Premium Membership Agreement"
            assert saved_report["table_name"] == "members"

        contract_preview = client.get(f"/contracts/{member_id}")
        assert contract_preview.status_code == 200
        assert b"Premium Membership Agreement" in contract_preview.data
        assert b"First editable term." in contract_preview.data

        loaded_report = client.get(
            f"/reports/?tab=custom&saved_report={saved_report['id']}"
        )
        assert loaded_report.status_code == 200
        assert b"Loaded:" in loaded_report.data
        assert b"Active Member Contacts" in loaded_report.data
