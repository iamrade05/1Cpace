"""Run the scheduled payment sync immediately, or inspect its durable status.

Usage: python -m scripts.sync_payments [--status] [--source itensity|nupay]
Uses the configured tenant and credentials; never starts the web app or its workers.
"""
import argparse
import json
import logging
import sqlite3
from pathlib import Path

from flask import Flask

from onecpase.config import Config
from onecpase.payment_sync import run_source, status_rows
from onecpase.scheduler import _find_tenant


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--source',choices=['itensity','nupay'])
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    app=Flask('onecpase',root_path=str(Path(__file__).resolve().parents[1]/'onecpase'))
    app.config.from_object(Config)
    if app.config.get('DATABASE_URL'):
        raise SystemExit('Scheduled payment sync requires a tenant SQLite database.')
    tenant=_find_tenant(app,app.config['ITENSITY_TENANT_SLUG'])
    if tenant is None:
        raise SystemExit('Configured payment-sync tenant was not found.')
    if not args.status:
        for source in ([args.source] if args.source else ['itensity','nupay']):
            run_source(app,tenant,source)
    with sqlite3.connect(tenant['db_path']) as db:
        db.row_factory=sqlite3.Row
        result=status_rows(db)
    print(json.dumps(result,indent=2))
    if not args.status and any(r['status']=='failed' for r in result
                              if not args.source or r['source']==args.source):
        raise SystemExit(1)


if __name__=='__main__':
    main()
