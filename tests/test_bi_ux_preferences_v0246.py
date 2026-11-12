"""Source-backed tests for additive BI window size persistence and isolation."""
from __future__ import annotations
import sqlite3
from types import SimpleNamespace
from timepilot.project_migrations import TARGET_SCHEMA, migrate
from timepilot.bi_ui_preferences import BIUIPreferences


def test_v17_pref_migrates_and_keeps_original_data(tmp_path):
    path=tmp_path/'projects.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE project_schema(version INTEGER NOT NULL);
          INSERT INTO project_schema VALUES(17);
          CREATE TABLE projects(id TEXT PRIMARY KEY);
          INSERT INTO projects VALUES('p');
          CREATE TABLE bi_ui_preferences(project_id TEXT NOT NULL,user_id TEXT NOT NULL,
          asset_id TEXT NOT NULL,mode TEXT NOT NULL,width INTEGER NOT NULL,
          last_workspace_id TEXT,dismissed_tips TEXT NOT NULL,updated_at TEXT NOT NULL,
          PRIMARY KEY(project_id,user_id,asset_id));
          INSERT INTO bi_ui_preferences VALUES('p','a','asset','open',440,NULL,'[]','previous');""")
        migrate(db);migrate(db)
        assert db.execute('SELECT version FROM project_schema').fetchone()[0] == TARGET_SCHEMA and TARGET_SCHEMA >= 18
        assert db.execute("SELECT width,height FROM bi_ui_preferences WHERE asset_id='asset'").fetchone()==(440,670)


def test_window_preference_scoped_by_actor_and_asset(tmp_path):
    path=tmp_path/'projects.sqlite3'
    def database():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');return db
    with database() as db:
        db.executescript("""CREATE TABLE project_schema(version INTEGER NOT NULL);
          INSERT INTO project_schema VALUES(17);
          CREATE TABLE projects(id TEXT PRIMARY KEY);
          INSERT INTO projects VALUES('p');
          CREATE TABLE bi_ui_preferences(project_id TEXT NOT NULL,user_id TEXT NOT NULL,
          asset_id TEXT NOT NULL,mode TEXT NOT NULL,width INTEGER NOT NULL,
          last_workspace_id TEXT,dismissed_tips TEXT NOT NULL,updated_at TEXT NOT NULL,
          PRIMARY KEY(project_id,user_id,asset_id));""")
        migrate(db)
    actor={'value':'user1'}
    projects=SimpleNamespace(get=lambda pid: {'id':pid},_db=database,_lock=__import__('threading').RLock())
    engine=SimpleNamespace(checked=lambda pid,aid,ver:None)
    service=SimpleNamespace(projects=projects,engine=engine,_actor=lambda:actor['value'])
    prefs=BIUIPreferences(service)
    result=prefs.patch('p','asset',{'mode':'open','width':600,'height':450})
    assert result['width']==600 and result['height']==450
    actor['value']='user2'
    assert prefs.get('p','asset')['width']==480
    assert prefs.get('p','asset')['height']==670
    actor['value']='user1'
    assert prefs.get('p','asset2')['height']==670
    try:prefs.patch('p','asset',{'height':1200})
    except ValueError as exc:assert '高度' in str(exc)
    else:raise AssertionError('invalid height accepted')
