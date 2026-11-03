"""Server-side rehearsal, install and verification of the managers-only NuPay review button. No database changes."""
from pathlib import Path
import argparse,json
from urllib.request import urlopen
import contract_signing_remote as contract
core=contract.core
FILES={'onecpase/debicheck_routes.py','onecpase/templates/debicheck/detail.html','onecpase/templates/members/detail.html'}

def validate(stage,manifest,installed=False):
    assert {r['path'] for r in manifest}<=FILES and manifest
    for r in manifest:
        relative=Path(r['path'])
        assert '..' not in relative.parts and (core.ROOT/relative).resolve().is_relative_to(core.ROOT)
        assert core.digest(stage/'incoming'/relative)==r['sha256']
        assert core.digest(core.ROOT/relative)==r['sha256' if installed else 'before_sha256'],'Concurrent server edit: '+r['path']
        if relative.suffix=='.py':compile((stage/'incoming'/relative).read_bytes(),str(relative),'exec')

# Sales must keep a way to send the mandate; a manager is never made to send one here.
marker="            tenant=dict(slug='elev8',name='ELEv8 Health & Fitness',logo_filename=None)"
extra='''            with app.app_context():
                db=get_db()
                db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,is_sales_consultant,active) VALUES(20,'release20','x','Example Sales','staff','Sales',1,1)")
                db.execute("INSERT INTO members(id,first_name,last_name,id_number,member_ref,member_status,monthly_installment) VALUES(20,'Example','Mandate','9001015009087','UI-020','Active',500)")
                db.execute("""INSERT INTO debicheck_mandates(id,member_id,merchant_id,client_ref1,client_ref2,account_name,account_type,account_number,branch_code,frequency,no_installments,tracking,submit_date,id_type,id_number,instalment_rands,instalment_cents,instalment_amount,payer_type,status)
                              VALUES(20,20,'M1','FIT-1','1234','E Mandate','1','123456','123456','3',12,'14','2099-01-01','0','9001015009087',500,0,500.0,'self','pending')""")
                application=db.execute('INSERT INTO membership_applications(member_id) VALUES(20)').lastrowid
                db.execute("INSERT INTO compliance_checklists(application_id,id_copy_status,bank_statement_status) VALUES(?,'not_uploaded','analysed')",(application,))
                db.execute('INSERT INTO statement_analyses(application_id,verification_qualified) VALUES(?,1)',(application,))
                db.execute("""INSERT INTO member_documents(member_id,document_type,original_name,stored_name,analysis_json) VALUES(20,'Bank Statement','s.pdf','s.pdf',?)""",('{"estimated_income":10000,"income_detected":true,"avg_balance":2000,"max_balance":5000,"returned_debits":0,"months_seen":2,"rating":"good","verification_qualified":true,"cashflow":{"months":2,"enough_months":true}}',))
                db.commit()
            def signed_in_as(uid,role):
                client.set_cookie('onecpase_tenant','elev8')
                with client.session_transaction() as s:s.update(user_id=uid,role=role,tenant_slug='elev8',permissions=['new_debicheck','debicheck_mandates','all_members'])
            def mandate_status():
                with app.app_context():return get_db().execute('SELECT status FROM debicheck_mandates WHERE id=20').fetchone()[0]
            button='Review &amp; Submit on NuPay'
            signed_in_as(20,'staff')
            profile=client.get('/members/20');mandate=client.get('/debicheck/20')
            assert profile.status_code==200 and mandate.status_code==200
            assert button not in profile.get_data(as_text=True) and button not in mandate.get_data(as_text=True)
            assert 'Open DebiCheck Mandate' in profile.get_data(as_text=True) and 'href="/debicheck/20"' in profile.get_data(as_text=True)
            assert 'Resend Automatically' in mandate.get_data(as_text=True)
            refused=client.post('/debicheck/20/review-on-nupay')
            assert refused.status_code==302 and '/debicheck/20' not in refused.headers['Location'] and mandate_status()=='pending'
            for uid,role in ((4,'manager'),(1,'admin')):
                signed_in_as(uid,role)
                for page in ('/members/20','/debicheck/20'):
                    response=client.get(page)
                    assert response.status_code==200 and button in response.get_data(as_text=True),(role,page)
            assert mandate_status()=='pending'
            pages.extend(('nupay-review-hidden-from-sales','nupay-review-refused-for-sales','nupay-review-shown-to-managers'))
            login(1)
            tenant=dict(slug='elev8',name='ELEv8 Health & Fitness',logo_filename=None)'''
assert contract.source.count(marker)==1
exec(compile(contract.source.replace(marker,extra),'nupay_review_rehearsal','exec'),core.__dict__)

def verify(stage,manifest):
    validate(stage,manifest,installed=True);assert core.health()
    public={}
    for host in ('1cpace.co.za','beta.1cpace.co.za'):
        with urlopen('https://'+host+'/healthz',timeout=20) as response:
            assert response.status==200 and json.load(response)['status']=='ok'
        public[host]='ok'
        with urlopen('https://'+host+'/login',timeout=20) as response:
            assert response.status==200
    return dict(passed=True,health=public,installed_file_hashes_verified=len(manifest),provider_actions=0)

core.validate=validate;core.verify=verify
core.RELEASE_TYPE='nupay-review-managers-only'

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['rehearse','apply','verify']);parser.add_argument('stage')
    args=parser.parse_args();stage=Path(args.stage).resolve()
    assert stage.parent==core.ROOT and stage.name.startswith('.workflow-release-nupay_review_managers_')
    manifest=json.loads((stage/'manifest.json').read_text())
    if args.action=='rehearse':
        validate(stage,manifest)
        result=core.render_check(stage,manifest)
        (stage/'rehearsal-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    elif args.action=='apply':
        contract.assert_payment_sync_idle()
        core.apply(stage,manifest)
    else:
        result=dict(**verify(stage,manifest),render_check=core.render_check(stage,manifest,installed=True))
        (stage/'verification-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
