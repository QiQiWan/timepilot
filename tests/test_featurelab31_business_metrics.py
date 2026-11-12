from pathlib import Path
import sqlite3
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from featurelab31_agent_business_acceptance import assess


def test_agent_acceptance_rejects_missing_human_review(tmp_path):
    catalog=tmp_path/'project.sqlite3'
    with sqlite3.connect(catalog) as db:
        db.execute('''CREATE TABLE feature_agent_plans
        (plan_id TEXT,project_id TEXT,status TEXT,source_sha256 TEXT,created_at TEXT,updated_at TEXT)''')
        db.execute('CREATE TABLE feature_agent_steps(plan_id TEXT,status TEXT,tool_id TEXT)')
        db.execute('INSERT INTO feature_agent_plans VALUES (?,?,?,?,?,?)',('p1','project','succeeded','a'*64,'2026-01-01','2026-01-02'))
        db.execute('INSERT INTO feature_agent_steps VALUES (?,?,?)',('p1','succeeded','feature.schema.inspect'))
    good={'project_id':'project','plan_id':'p1','source_kind':'real_business','reviewer':'业务审阅人','accepted':'yes','manual_corrections':'0','measured_time_saved_minutes':'2.5','comment':'已复核业务结果'}
    passed=assess(catalog,'project',[good],min_tasks=1)
    assert passed['status']=='passed' and passed['business_acceptance_rate']==1
    missing=assess(catalog,'project',[dict(good,reviewer='')],min_tasks=1)
    assert missing['status']=='blocked' and missing['business_acceptance_rate'] is None
    duplicate=assess(catalog,'project',[good,good],min_tasks=1)
    assert duplicate['status']=='blocked'
    fake=assess(catalog,'project',[dict(good,source_kind='synthetic')],min_tasks=1)
    assert fake['status']=='blocked'
