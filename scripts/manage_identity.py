"""Offline provisioning; never accepts a password on command line."""
from __future__ import annotations
import argparse
import getpass
import os
from pathlib import Path
from timepilot.enterprise_security import EnterpriseSecurity


def main():
    parser=argparse.ArgumentParser(description='TimePilot enterprise identity offline provisioning')
    sub=parser.add_subparsers(dest='command',required=True)
    new=sub.add_parser('bootstrap',help='create a new isolated tenant and administrator')
    new.add_argument('--tenant',required=True);new.add_argument('--name',required=True)
    new.add_argument('--user',required=True)
    claim=sub.add_parser('claim-legacy',help='explicitly assign previously unclaimed projects to a tenant')
    claim.add_argument('--tenant-id',required=True)
    claim.add_argument('--project-id',action='append',default=[],help='Assign only these project IDs; repeatable')
    claim.add_argument('--apply',action='store_true',help='Required to mutate project tenant ownership')
    args=parser.parse_args()
    home=Path(os.getenv('TIMEPILOT_HOME',Path.home()/'.timepilot')).resolve()
    security=EnterpriseSecurity(home)
    if args.command=='bootstrap':
        first=getpass.getpass('New administrator password (at least 14 characters): ')
        again=getpass.getpass('Repeat password: ')
        if first!=again:raise SystemExit('Password confirmation mismatch')
        tenant=security.bootstrap_tenant(args.tenant,args.name,args.user,first)
        print('Tenant created:',tenant,'\nAdministrator:',args.user,'\nPassword is not stored in logs.')
    else:
        candidates=security.preview_legacy_projects()
        selected=[p for p in candidates if not args.project_id or p['id'] in args.project_id]
        print('Unclaimed local projects:',len(candidates),'\nSelected:',len(selected))
        for project in selected:print(' ',project['id'],project['name'],project['tenant_id'])
        if not args.apply:
            print('DRY RUN ONLY. To claim these projects, repeat with --apply and an optional --project-id filter.')
            return
        answer=input('This changes project ownership to tenant '+args.tenant_id+'. Type CLAIM to continue: ')
        if answer!='CLAIM':raise SystemExit('Cancelled')
        print('Claimed legacy projects:',security.claim_legacy_projects(
            args.tenant_id,project_ids=args.project_id or None))

if __name__=='__main__':main()
