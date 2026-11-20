#!/usr/bin/env python3
"""Strict v0.32.4 QA: same external CSV -> Parquet -> evaluator -> real BI.

Consumes reports created by featurelab30_e2e.py and featurelab31_browser_acceptance.py.
Verifies original bytes, catalog artifact location and bytes, and independent
browser receipt. A passing *core* check is still not a business acceptance or
proof of independent data provenance. Never fabricates measurements or receipts.
"""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from featurelab31_release_gate import check as check_release


def file_sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def inspect_same_source(csv_path:Path,backend:dict,browser:dict,*,advanced=None,business=None,scope='core')->dict:
    issues=check_release(backend,browser,advanced=advanced,require_advanced=scope=='full',
                         business=business,require_business=scope=='full')
    csv_path=csv_path.expanduser().resolve()
    source_sha=(backend.get('source') or {}).get('sha256')
    if csv_path.suffix.lower()!='.csv' or not csv_path.is_file():
        issues.append('real input CSV is missing')
    elif file_sha256(csv_path)!=source_sha:
        issues.append('external input CSV bytes changed since backend evaluation')
    sha=(backend.get('artifact') or {}).get('sha256')
    aid=(backend.get('artifact') or {}).get('artifact_id')
    pid=backend.get('qa_project_id')
    home=(backend.get('qa_home') or '')
    root=None
    if not home or not pid or not aid or not sha:
        issues.append('missing retained QA home/project/artifact identity')
    else:
        root=Path(home).expanduser().resolve()/'isolated_application'
        dbpath=root/'projects'/'catalog.sqlite3'
        if not dbpath.is_file():
            issues.append('retained QA catalog missing; ephemeral QA cannot prove same-source Parquet')
        else:
            try:
                with sqlite3.connect(f'file:{dbpath}?mode=ro',uri=True) as db:
                    row=db.execute('''SELECT relpath,sha256 FROM feature_artifacts
                        WHERE project_id=? AND artifact_id=?''',(pid,aid)).fetchone()
                if not row or row[1]!=sha:
                    issues.append('retained catalog artifact identity/SHA256 mismatch')
                else:
                    folder=(root/'feature_lab').resolve()
                    path=(folder/row[0]).resolve()
                    if not path.is_relative_to(folder) or not path.is_file() or file_sha256(path)!=sha:
                        issues.append('materialized Parquet not found inside QA home or SHA256 mismatched')
            except (sqlite3.Error,OSError,ValueError) as e:
                issues.append('catalog/Parquet verification failed: '+type(e).__name__)
    return {'schema_version':1,'status':'passed' if not issues else 'blocked',
        'scope':scope+'_qa_only','provenance':'operator_declared_not_independently_certified',
        'source_sha256':source_sha,'artifact_sha256':sha,
        'browser_sha256':browser.get('artifact_sha256'),
        'issues':list(dict.fromkeys(issues)),
        'note':'通过只证明所给报表及本机实际字节的一致性；业务准确率和现场用户验收需另外审核。'}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Verify external source and retained E2E evidence')
    parser.add_argument('--csv',type=Path,required=True)
    parser.add_argument('--backend',type=Path,required=True)
    parser.add_argument('--browser',type=Path,required=True)
    parser.add_argument('--advanced',type=Path)
    parser.add_argument('--business',type=Path)
    parser.add_argument('--scope',choices=['core','full'],default='core')
    parser.add_argument('--out',type=Path,default=Path('reports/v0324/same_source_gate.json'))
    a=parser.parse_args(argv)
    def read(path):
        if path is None:return {}
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            return data if isinstance(data,dict) else {}
        except (ValueError,OSError):return {}
    result=inspect_same_source(a.csv,read(a.backend),read(a.browser),
       advanced=read(a.advanced),business=read(a.business),scope=a.scope)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
