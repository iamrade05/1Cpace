"""Server-side rehearsal, install and verification of the contract signing release."""
from pathlib import Path
import argparse,gzip,hashlib,json,os,shutil,sqlite3
from datetime import datetime,timedelta,timezone
from urllib.request import urlopen
import query_funnel_remote as funnel
import workflow_release_remote as workflow
core=funnel.core
reporting=funnel.reporting
VERSION='funnel-buttons-20261102'
NEW_COLUMNS=('signature_id','debit_signature_id','signed_ip','signed_user_agent','read_to_end_at','debicheck_accepted_at')
FILES={'onecpase/'+n for n in ('collections.py','collections_engine.py','contracts.py','dashboard_access.py','database.py','debicheck.py','first_month_collections.py','members.py','ptp.py','tt3.py','static/css/components.css','static/js/contract-sign.js','static/js/document-upload.js')}|{'onecpase/templates/'+n for n in ('base.html','collections/first_month.html','contracts/share_response.html','contracts/view.html','debicheck/_document_upload.html','leads/detail.html','members/detail.html')}|{'scripts/import_itensity_members.py'}

def validate(stage,manifest,installed=False):
    assert {r['path'] for r in manifest}==FILES
    for r in manifest:
        relative=Path(r['path'])
        assert '..' not in relative.parts and (core.ROOT/relative).resolve().is_relative_to(core.ROOT)
        assert core.digest(stage/'incoming'/relative)==r['sha256']
        assert core.digest(core.ROOT/relative)==r['sha256' if installed else 'before_sha256'],'Concurrent server edit: '+r['path']
        if relative.suffix=='.py':compile((stage/'incoming'/relative).read_bytes(),str(relative),'exec')

# Walk every change in this release through the disposable-data fixture.
marker="            tenant=dict(slug='elev8',name='ELEv8 Health & Fitness',logo_filename=None)"
extra='''            import base64
            from onecpase import tt3 as tt3_module
            from onecpase.encryption import encrypt
            from onecpase.ptp import _run_collections_automations,bulk_member_arrears
            today=date.today().isoformat();soon=(date.today()+timedelta(days=20)).isoformat();past=(date.today()-timedelta(days=40)).isoformat()
            with app.app_context():
                db=get_db()
                db.execute("""INSERT INTO members(id,first_name,last_name,id_number,contact,member_ref,member_status,join_date,payment_type,bank,account_type,account_number,branch_code,monthly_installment,debit_order_date)
                              VALUES(5,'Example','Signer',?,'0712345678','UI-005','Active',?,'Debit Order','Standard Bank','Cheque/Current',?,?,300,'1')""",(encrypt('9001015009087'),today,encrypt('123456789'),encrypt('051001')))
                db.execute("INSERT INTO members(id,first_name,last_name,id_number,member_ref,member_status,monthly_installment) VALUES(6,'Example','Paid','ui-release-paid','UI-006','Active',300)")
                db.execute("INSERT INTO collections(member_id,outstanding_balance,amount_paid,collection_date,method,status,notes) VALUES(6,300,0,?,'debit','pending','Recurring Fee')",(soon,))
                db.execute("INSERT INTO members(id,first_name,last_name,id_number,member_ref,member_status,monthly_installment,join_date) VALUES(7,'Example','Owing','ui-release-owing','UI-007','Active',300,'2020-01-01')")
                db.execute("INSERT INTO collections(member_id,outstanding_balance,amount_paid,collection_date,method,status,notes) VALUES(7,300,0,?,'debit','pending','Ledger | type=Recurring Fee | Monthly fee')",(past,))
                arrears=bulk_member_arrears(db,[6,7]);assert arrears[6]['total_arrears']==0 and arrears[7]['total_arrears']>0
                for _ in range(3):
                    _run_collections_automations(db,today);db.commit()
                assert db.execute('SELECT COUNT(*) FROM collections_cases WHERE member_id=6').fetchone()[0]==0
                assert db.execute('SELECT COUNT(*) FROM access_decisions WHERE member_id=6').fetchone()[0]==0
                assert db.execute('SELECT COUNT(*) FROM collections_cases WHERE member_id=7').fetchone()[0]==1
                assert db.execute('SELECT COUNT(*) FROM access_decisions WHERE member_id=7').fetchone()[0]==1
            pages.append('sweep-writes-nothing-when-nothing-changed')
            login(1)
            html=client.post('/contracts/5/share',data=dict(channel='link'),follow_redirects=True).get_data(as_text=True)
            assert 'Send Contract to Sign' in html
            link=re.search(r'/contracts/share/[\\w-]+/[\\w-]+',html).group(0)
            phone=app.test_client()
            html=phone.get(link).get_data(as_text=True)
            for private in ('Standard Bank','9001015009087','Example Signer'):assert private not in html,private
            assert 'last 6 digits of your ID number' in html
            assert 'Standard Bank' not in phone.post(link,data=dict(action='verify_code',code='111111')).get_data(as_text=True)
            html=phone.post(link,data=dict(action='verify_code',code='009087')).get_data(as_text=True)
            assert 'contractDebitPad' in html and '123****789' in html and '123456789' not in html
            assert app.test_client().post(link,data=dict(action='sign')).status_code==403
            mark='data:image/png;base64,'+base64.b64encode(b'\\x89PNG'+b'0'*400).decode()
            signed=dict(action='sign',read_to_end='1',accepted='1',image_data=mark,debicheck_accepted='1',third_party_payer='1',payer_name='Example Payer',payer_id_number='7507074800085')
            assert phone.post(link,data=signed).status_code==400
            html=phone.post(link,data=dict(signed,debit_signature_data=mark)).get_data(as_text=True)
            assert 'Contract signed' in html
            assert phone.get(link).status_code==410
            with app.app_context():
                db=get_db();token=db.execute('SELECT * FROM contract_share_tokens ORDER BY id DESC LIMIT 1').fetchone()
                assert token['status']=='approved' and token['debit_signature_id'] and token['read_to_end_at'] and token['debicheck_accepted_at']
                assert db.execute("SELECT COUNT(*) FROM member_documents WHERE member_id=5 AND document_type='Signed Contract'").fetchone()[0]==1
            html=client.get('/contracts/5').get_data(as_text=True)
            assert 'Contract signed' in html and '/signature/debit_authoriser' in html
            pages.extend(('contract-link-digits','contract-link-sign','contract-staff-view'))
            spawned=[];tt3_module._spawn=lambda *a:spawned.append(a)
            response=client.post('/members/5/documents',data={'document_type':'TT3 (Manual DebiCheck)','document':(io.BytesIO(b'%PDF-1.4 synthetic'),'tt3.pdf')},content_type='multipart/form-data',follow_redirects=True)
            assert response.status_code==200 and 'Checking NuPay' in response.get_data(as_text=True) and len(spawned)==1
            html=client.get('/members/5').get_data(as_text=True)
            assert 'Check NuPay for TT3' in html
            pages.append('tt3-upload')
            html=client.get('/sales/first-month-failed-debits/').get_data(as_text=True)
            assert 'Current cycle' in html and 'Signed by' in html
            pages.append('first-month-cycle')
            with app.app_context():
                db=get_db()
                db.execute("INSERT INTO leads(id,full_name,phone,lead_status,source,original_source,assigned_to) VALUES(9,'Example Prospect','0820000000','assigned','Referral','Referral',1)");db.commit()
            html=client.get('/leads/9').get_data(as_text=True)
            assert 'funnel-btn' in html and VERSION_MARK in html
            pages.append('lead-funnel-buttons')
            tenant=dict(slug='elev8',name='ELEv8 Health & Fitness',logo_filename=None)'''
extra=extra.replace('VERSION_MARK',repr(VERSION))
assert funnel.source.count(marker)==1
source=funnel.source.replace(marker,extra)
exec(compile(source,'contract_signing_rehearsal','exec'),core.__dict__)

def tenant_columns():
    platform,tenants,paths=workflow.database_paths()
    found={}
    for tenant in tenants:
        conn=sqlite3.connect('file:'+tenant['db_path']+'?mode=ro',uri=True)
        try:found[tenant['slug']]={r[1] for r in conn.execute('PRAGMA table_info(contract_share_tokens)')}
        finally:conn.close()
    return found

def verify(stage,manifest):
    validate(stage,manifest,installed=True);assert core.health()
    public={}
    for host in ('1cpace.co.za','beta.1cpace.co.za'):
        with urlopen('https://'+host+'/healthz',timeout=20) as response:
            assert response.status==200 and json.load(response)['status']=='ok'
        public[host]='ok'
        with urlopen('https://'+host+'/static/css/components.css?v='+VERSION,timeout=20) as response:
            assert response.status==200 and b'.btn.funnel-btn--warn' in response.read()
        with urlopen('https://'+host+'/static/js/contract-sign.js',timeout=20) as response:
            assert response.status==200 and b'contractDebitPad' in response.read()
        with urlopen('https://'+host+'/static/js/document-upload.js?v=upload-result-20261102',timeout=20) as response:
            assert response.status==200 and b'documentUploadResult' in response.read()
        with urlopen('https://'+host+'/login',timeout=20) as response:
            assert response.status==200 and VERSION in response.read().decode()
    columns=tenant_columns()
    for slug,present in columns.items():
        assert set(NEW_COLUMNS)<=present,(slug,set(NEW_COLUMNS)-present)
    return dict(passed=True,health=public,installed_file_hashes_verified=len(manifest),public_assets_verified=8,new_columns_on_tenants=sorted(columns),provider_actions=0)

def migration_check():
    """Add the new columns to an empty copy of the live table shapes; no customer data."""
    platform,tenants,paths=workflow.database_paths()
    checked=[]
    for tenant in tenants:
        original=sqlite3.connect('file:'+tenant['db_path']+'?mode=ro',uri=True)
        try:definition=original.execute("SELECT sql FROM sqlite_master WHERE name='contract_share_tokens'").fetchone()[0]
        finally:original.close()
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE members(id INTEGER PRIMARY KEY)');db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY)');db.execute(definition)
            present={r[1] for r in db.execute('PRAGMA table_info(contract_share_tokens)')}
            for column in NEW_COLUMNS:
                if column not in present:db.execute('ALTER TABLE contract_share_tokens ADD COLUMN '+column+(' INTEGER' if column.endswith('_id') else ' TEXT'))
        finally:db.close()
        checked.append(tenant['slug'])
    return dict(passed=True,tenants=checked,additive_only=True,live_database_writes=False)

def assert_payment_sync_idle():
    # A restart must not interrupt a running payment sync.
    _,tenants,_=workflow.database_paths()
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
    for tenant in tenants:
        conn=sqlite3.connect('file:'+tenant['db_path']+'?mode=ro',uri=True)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='payment_sync_state'").fetchone():
                assert not conn.execute("SELECT 1 FROM payment_sync_state WHERE status='running' AND started_at>?",(cutoff,)).fetchone(),'Payment sync is active; deploy after it finishes'
        finally:conn.close()

def digest_stream(handle):
    h=hashlib.sha256()
    for chunk in iter(lambda:handle.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def compress(path):
    target=path.with_name(path.name+'.gz');temporary=target.with_name(target.name+'.new')
    with path.open('rb') as handle:expected=digest_stream(handle)
    with path.open('rb') as incoming,gzip.open(temporary,'wb',compresslevel=6) as outgoing:shutil.copyfileobj(incoming,outgoing,1024*1024)
    with gzip.open(temporary,'rb') as handle:assert digest_stream(handle)==expected,'Compressed backup failed verification'
    os.chmod(temporary,0o600);os.replace(temporary,target);size=path.stat().st_size;path.unlink()
    return dict(backup=str(target),compression='gzip',uncompressed_sha256=expected,uncompressed_bytes=size)

reporting.validate=validate;core.validate=validate;core.verify=verify
core.RELEASE_TYPE='contract-signing-additive-columns'

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['rehearse','apply','verify']);parser.add_argument('stage')
    args=parser.parse_args();stage=Path(args.stage).resolve()
    assert stage.parent==core.ROOT and stage.name.startswith('.workflow-release-contract_signing_')
    manifest=json.loads((stage/'manifest.json').read_text())
    if args.action=='rehearse':
        validate(stage,manifest);migration=migration_check()
        result=core.render_check(stage,manifest);result['migration']=migration
        (stage/'rehearsal-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    elif args.action=='apply':
        validate(stage,manifest);assert json.loads((stage/'rehearsal-result.json').read_text())['migration']['passed']
        assert_payment_sync_idle()
        platform,tenants,paths=workflow.database_paths()
        backup=core.BACKUPS/('contract_database_'+stage.name.rsplit('contract_signing_',1)[1]);backup.mkdir(mode=0o700)
        saved=[]
        for index,path in enumerate(paths):
            target=backup/(str(index)+'-'+path.name);workflow.backup_sqlite(path,target);saved.append(dict(source=str(path),backup=str(target)))
        (backup/'manifest.json').write_text(json.dumps(saved,indent=2))
        core.apply(stage,manifest)
        # The code is healthy on the new columns; only now shrink the safety copy to free the disk.
        for row in saved:
            copy=Path(row['backup'])
            if copy.stat().st_size>100*1024*1024:row.update(compress(copy))
        (backup/'manifest.json').write_text(json.dumps(saved,indent=2))
        result=json.loads((stage/'deployment-result.json').read_text())
        result.update(database_changes='additive contract_share_tokens columns added by the app at startup',database_backup=str(backup),rollback_policy='Restore code only; preserve live data and the additive columns.')
        scope_path=Path(result['backup'])/'scope.json';scope=json.loads(scope_path.read_text());scope.update(database_changes=True,additive_only=True,rollback='Code only; preserve live data');scope_path.write_text(json.dumps(scope,indent=2))
        (stage/'deployment-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    else:
        validate(stage,manifest,installed=True)
        result=dict(**verify(stage,manifest),render_check=core.render_check(stage,manifest,installed=True))
        (stage/'verification-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
