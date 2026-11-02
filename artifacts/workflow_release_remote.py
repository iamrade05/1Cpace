"""Run on the deployment host: isolated migration rehearsal, then scoped apply."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT=Path('/opt/onecpase')

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

def read_config():
    from dotenv import dotenv_values
    values=dotenv_values(ROOT/'.env')
    # The effective database paths are obtained without exposing credentials.
    for key,value in values.items():
        if value is not None:os.environ.setdefault(key,value)
    return values

def backup_sqlite(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    assert not target.exists(), 'Backup destination already exists'
    retention_policy().ensure_backup_capacity(source,target.parent)
    src=dst=None;complete=False
    try:
        src=sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True,timeout=30)
        dst=sqlite3.connect(target)
        src.backup(dst)
        assert dst.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        complete=True
    finally:
        if dst is not None:dst.close()
        if src is not None:src.close()
        if not complete and target.exists():target.unlink()
    os.chmod(target,0o600)


def retention_policy():
    spec=importlib.util.spec_from_file_location('release_backup_retention',ROOT/'onecpase/backup_retention.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def cleanup_rehearsal(stage):
    stage=Path(stage).resolve()
    assert stage.parent==ROOT.resolve() and stage.name.startswith('.workflow-release-')
    for name in ('database-copies','rehearsal'):
        target=stage/name
        assert not target.is_symlink() and target.resolve().parent==stage
        if target.exists():shutil.rmtree(target)

def database_paths():
    # Read existing config before selecting the staged package in rehearsal.
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    values=read_config()
    platform=Path(os.environ.get('PLATFORM_DATABASE_PATH') or ROOT/'platform.db')
    assert platform.exists(), 'Registry not found'
    conn=sqlite3.connect('file:'+str(platform)+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:tenants=[dict(row) for row in conn.execute('SELECT * FROM tenants')]
    finally:conn.close()
    paths={platform}
    default_database=Path(os.environ.get('DATABASE_PATH') or ROOT/'1cpase.db')
    if default_database.exists():paths.add(default_database)
    for tenant in tenants:
        path=Path(tenant['db_path'])
        assert path.is_absolute() and path.exists(), 'Tenant database missing'
        paths.add(path)
    return platform,tenants,sorted(paths)

def validate(stage,manifest):
    for entry in manifest:
        relative=entry['path'];target=ROOT/relative;incoming=stage/'incoming'/relative
        assert target.resolve().is_relative_to(ROOT),relative
        assert digest(incoming)==entry['sha256'],'Staged hash mismatch: '+relative
        assert digest(target)==entry['before_sha256'],'Concurrent server edit: '+relative
        if relative.endswith('.py'):compile(incoming.read_bytes(),str(incoming),'exec')

def rehearse(stage,manifest):
    platform,tenants,paths=database_paths()
    tree=stage/'rehearsal'
    for temporary in (tree,stage/'database-copies'):
        if temporary.exists():
            assert temporary.resolve().is_relative_to(stage.resolve())
            shutil.rmtree(temporary)
    tree.mkdir()
    shutil.copytree(ROOT/'onecpase',tree/'onecpase',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for entry in manifest:
        target=tree/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(stage/'incoming'/entry['path'],target)
    copies=stage/'database-copies';copies.mkdir(mode=0o700)
    platform_copy=copies/'platform.db';backup_sqlite(platform,platform_copy)
    registry=sqlite3.connect(platform_copy)
    default_copy=None
    for index,tenant in enumerate(tenants):
        copied=copies/f'tenant-{index}.db';backup_sqlite(Path(tenant['db_path']),copied)
        if tenant['active'] and default_copy is None:default_copy=copied
        registry.execute('UPDATE tenants SET db_path=? WHERE id=?',(str(copied),tenant['id']))
    registry.commit();registry.close()
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    os.environ['APP_ENV']='testing'
    os.environ['SCHEDULER_ENABLED']='0';os.environ['TURNSTILE_ENABLED']='0';os.environ['WHATSAPP_WEB_ENABLED']='0'
    sys.path.insert(0,str(tree))
    from onecpase import create_app
    from onecpase.fitness_journey import ensure_journey
    assert default_copy is not None
    app=create_app(dict(TESTING=True,DATABASE_URL='',DATABASE_PATH=str(default_copy),
        PLATFORM_DATABASE_PATH=str(platform_copy),TENANTS_DIR=str(copies/'tenants'),UPLOAD_FOLDER=str(copies/'uploads'),
        SCHEDULER_ENABLED=False,TURNSTILE_ENABLED=False,WHATSAPP_WEB_ENABLED=False,
        MAIL_SUPPRESS_SEND=True,WTF_CSRF_ENABLED=False,ACTIVE_PHASE='all',SECRET_KEY=os.urandom(32)))
    from flask import g
    from onecpase.database import get_db
    rendered=0
    for tenant in tenants:
        if not tenant['active']:continue
        with app.app_context():
            registry=sqlite3.connect(platform_copy);registry.row_factory=sqlite3.Row
            migrated=dict(registry.execute('SELECT * FROM tenants WHERE id=?',(tenant['id'],)).fetchone());registry.close()
            g.tenant=migrated;g.tenant_slug=tenant['slug']
            db=get_db();ensure_journey(db)
            assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            uid=db.execute("SELECT id FROM users WHERE active=1 AND role='admin' AND COALESCE(is_platform_user,0)=0 LIMIT 1").fetchone()
            if not uid:continue
            with app.test_client() as client:
                client.set_cookie('onecpase_tenant',tenant['slug'])
                with client.session_transaction() as session:
                    session.update(user_id=uid['id'],role='admin',permissions=[],tenant_slug=tenant['slug'])
                for path in ('/fitness/intake/allocations','/fitness/intake/allocation-roster','/fitness/intake/allocation-reviews','/queries/workflow-setup'):
                    response=client.get(path)
                    assert response.status_code==200,f'{path}: {response.status_code}'
                    rendered+=1
    result=dict(migration_rehearsal=True,tenant_databases=len(tenants),pages_rendered=rendered,provider_actions=0)
    (stage/'rehearsal-result.json').write_text(json.dumps(result))
    # Copies contain private data: remove them after the rehearsal.
    assert copies.resolve().is_relative_to(stage.resolve())
    shutil.rmtree(copies)
    print(json.dumps(result),flush=True)

def apply(stage,manifest):
    rehearsal=json.loads((stage/'rehearsal-result.json').read_text());assert rehearsal['migration_rehearsal']
    validate(stage,manifest)
    platform,tenants,paths=database_paths()
    from datetime import datetime,timedelta,timezone
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
    for tenant in tenants:
        conn=sqlite3.connect('file:'+tenant['db_path']+'?mode=ro',uri=True)
        try:
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='payment_sync_state'").fetchone()
            if exists:
                assert not conn.execute("SELECT 1 FROM payment_sync_state WHERE status='running' AND started_at>?",(cutoff,)).fetchone(),'Payment sync is active; deploy after it finishes'
        finally:conn.close()
    backup_root=Path('/opt/onecpase-backups')
    required=sum(path.stat().st_size + (path.with_name(path.name+'-wal').stat().st_size if path.with_name(path.name+'-wal').exists() else 0) for path in paths)
    assert shutil.disk_usage(backup_root).free >= required + 512*1024*1024, 'Insufficient space for deployment backups and working-space reserve'
    backup=backup_root/('workflow_'+stage.name.rsplit('-',1)[-1])
    assert not backup.exists();backup.mkdir(mode=0o700)
    metadata=[]
    stopped=False;installed=[]
    try:
        subprocess.run(['systemctl','stop','onecpase'],check=True);stopped=True
        validate(stage,manifest)
        for index,path in enumerate(paths):
            target=backup/'databases'/f'{index}.db';backup_sqlite(path,target)
            metadata.append(dict(original=str(path),backup=str(target)))
        for entry in manifest:
            relative=entry['path'];target=ROOT/relative
            if target.exists():
                saved=backup/'code'/relative;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved)
        (backup/'database-map.json').write_text(json.dumps(metadata))
        (backup/'manifest.json').write_text(json.dumps(manifest))
        owner=(ROOT/'onecpase/scheduler.py').stat()
        for entry in manifest:
            target=ROOT/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_name(target.name+'.workflow-new')
            shutil.copy2(stage/'incoming'/entry['path'],temp);os.chown(temp,owner.st_uid,owner.st_gid);os.replace(temp,target)
            installed.append(entry)
        subprocess.run(['systemctl','start','onecpase'],check=True)
        healthy=False
        for attempt in range(25):
            try:
                with urlopen('http://127.0.0.1:5002/healthz',timeout=5) as response:
                    healthy=response.status==200 and json.load(response)['status']=='ok'
                if healthy:break
            except Exception:pass
            time.sleep(1)
        assert healthy,'Service failed post-deployment health check'
        result=dict(deployed=True,backup=str(backup),files=len(installed),database_backups=len(metadata),health='ok')
        (stage/'deployment-result.json').write_text(json.dumps(result))
        print(json.dumps(result),flush=True)
    except BaseException:
        if stopped:
            subprocess.run(['systemctl','stop','onecpase'],check=False)
            for entry in reversed(installed):
                target=ROOT/entry['path'];saved=backup/'code'/entry['path']
                if saved.exists():shutil.copy2(saved,target)
                elif entry['before_sha256'] is None and target.exists():target.unlink()
            # Migrations are additive. Keep all current data during rollback.
            subprocess.run(['systemctl','start','onecpase'],check=False)
        raise
    # Cleanup failure must never roll back a healthy application release.
    try:
        result['retention']=retention_policy().prune_deployments(backup_root,apply=True)
    except Exception as exc:
        result['retention_error']=str(exc)
    (stage/'deployment-result.json').write_text(json.dumps(result))
    print(json.dumps({'backup_retention':result.get('retention'),'backup_retention_error':result.get('retention_error')}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['rehearse','apply']);parser.add_argument('stage')
    args=parser.parse_args();stage=Path(args.stage).resolve()
    assert stage.parent==ROOT and stage.name.startswith('.workflow-release-')
    manifest=json.loads((stage/'manifest.json').read_text());validate(stage,manifest)
    if args.action=='rehearse':
        try:rehearse(stage,manifest)
        finally:cleanup_rehearsal(stage)
    else:apply(stage,manifest)
