"""v0.34.1-3 regression: family diversity, leakage guards, advanced bridge."""
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from timepilot.feature_contracts import DataContract,FieldContract
from timepilot.feature_strategy import plan_candidates,ROW_ORDER_OPS
from timepilot.feature_candidates import propose
from timepilot.feature_candidate_bridge import normalize_advanced,list_advanced_batches


def _contract(ordered=True,restricted=False):
    columns=[FieldContract(field_id='fld_unit',source_name='unit_code',source_column_index=0,
           physical_type='VARCHAR',semantic_type='id',role='entity_key',confirmation='confirmed'),
      FieldContract(field_id='fld_seq',source_name='seq',source_column_index=1,
           physical_type='BIGINT',semantic_type='numeric',role='order_index',confirmation='confirmed')]
    for i in range(12):
        columns.append(FieldContract(field_id=f'fld_a{i}',source_name=f'A{i}',source_column_index=i+2,
           physical_type='DOUBLE',semantic_type='numeric',role='value',unit='bar',
           confirmation='confirmed',classification=('restricted' if restricted and i==0 else 'internal')))
    columns.append(FieldContract(field_id='fld_label',source_name='defect',source_column_index=14,
           physical_type='DOUBLE',semantic_type='numeric',role='label',confirmation='confirmed'))
    columns.append(FieldContract(field_id='fld_regime',source_name='regime',source_column_index=15,
           physical_type='VARCHAR',semantic_type='category',role='regime',confirmation='confirmed'))
    return DataContract(asset_id='asset',version_id='base',source_sha256='a'*64,
          revision=3,state='confirmed',fields=columns,entity_field_ids=['fld_unit'])


def _topology(safe):
    return {'can_propose_windows':bool(safe),'safe_for_windows':bool(safe),
           'order_field_id':'fld_seq' if safe else None,
           'partition_field_ids':['fld_unit'] if safe else [],
           'reasons':[] if safe else ['未确认采样顺序']}


def _profile():
    return {'source_sha256':'a'*64,'query_fingerprint':'prof-v1',
        'fields':[{'field_id':f'fld_a{i}','null_rate':0.01,'distinct_count':80,
                   'std':10,'min':0,'max':150,'zero_rate':.55 if i==3 else .01}
                  for i in range(12)]}


def test_diverse_planner_spreads_budget_across_families_and_fields():
    c=_contract()
    p=plan_candidates(c,profile=_profile(),topology=_topology(True),budget=16,
                 objective='quality',category_values={'fld_regime':['A','B']})
    assert len(p['proposals'])==16
    assert len(p['family_counts'])>=4
    assert len({tuple(x['input_field_ids']) for x in p['proposals']})>=9
    assert len({x['operator_id'] for x in p['proposals']})>=8
    assert all('fld_label' not in x['input_field_ids'] for x in p['proposals'])
    out=propose(c,template='adaptive',budget=16,topology=_topology(True),
                profile=_profile(),objective='quality',category_values={'fld_regime':['A','B']})
    assert len(out)==16
    assert len({x['fingerprint'] for x in out})==16
    assert all(x.get('family') in p['family_counts'] for x in out)
    assert all(x['scope']=='exploration' for x in out)
    assert all(x['parameters'].get('order_field_id')=='fld_seq'
               and x['parameters'].get('partition_field_ids')==['fld_unit']
               for x in out if x['operator_id'] in ROW_ORDER_OPS)


def test_missing_order_disables_all_history_and_window_candidates():
    c=_contract()
    plan=plan_candidates(c,profile=_profile(),topology=_topology(False),budget=24,
                 category_values={'fld_regime':['A']})
    assert len(plan['proposals'])<=24
    assert any('窗口' in x['reason'] for x in plan['blocked'])
    assert all(x['operator_id'] not in ROW_ORDER_OPS for x in plan['proposals'])
    out=propose(c,template='adaptive',budget=24,topology=_topology(False),profile=_profile(),
                category_values={'fld_regime':['A']})
    assert out and all(x['operator_id'] not in ROW_ORDER_OPS for x in out)


def test_restricted_label_and_unconfirmed_never_enter_supervised_plan():
    c=_contract(restricted=True)
    p=plan_candidates(c,profile=_profile(),topology=_topology(True),budget=60,
                      task_mode='supervised')
    assert all('fld_label' not in x['input_field_ids'] and 'fld_a0' not in x['input_field_ids']
               for x in p['proposals'])
    with pytest.raises(ValueError,match='不存在'):
        plan_candidates(c,selected_fields=['forged_id'])
    with pytest.raises(ValueError):plan_candidates(c,budget=65)


def _artifact():
    return {'job_id':'job-1','engine':'tsfresh',
      'sources':{'primary':{'asset_id':'asset','version_id':'base','sha256':'a'*64,
                            'contract_revision':3}},
      'rows':50,'columns':['__candidate_key','__candidate_cutoff_utc','temp__mean',
                           'pressure__fft'],'native_asset_id':'native-features',
      'sha256':'b'*64,'source_fingerprint':'c'*64,'recipe_sha256':'d'*64,
      'pit':{'pit':'as_of_event_and_available_at'}}


def test_advanced_bridge_does_not_promote_or_expose_reserved_columns():
    doc=normalize_advanced(_artifact(),asset_id='asset')
    assert doc['status']=='review_required' and doc['candidate_count']==2
    assert all(x['requires_pit_join'] and not x['included_in_base_selection'] for x in doc['candidates'])
    assert all(not x['column'].startswith('__') for x in doc['candidates'])
    assert normalize_advanced(_artifact(),asset_id='another') is None
    stale=normalize_advanced(_artifact(),asset_id='asset',source_valid=False,issue='source changed')
    assert stale['status']=='source_stale' and stale['candidate_count']==0


def test_advanced_batch_listing_checks_owner_project_source_and_version():
    class FakeProjects:
        def __init__(self):
            self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
            self.db.execute('CREATE TABLE feature_advanced_artifacts(job_id TEXT, project_id TEXT, owner_id TEXT, created_at TEXT)')
            self.db.execute('INSERT INTO feature_advanced_artifacts VALUES (?,?,?,?)',('job-1','p','owner','2026'))
            self.db.execute('INSERT INTO feature_advanced_artifacts VALUES (?,?,?,?)',('job-hidden','p','other','2026'))
            self.db.commit()
        def require_native(self,pid,aid):
            if pid!='p' or aid not in ('asset','native-features'):raise PermissionError('wrong project')
        @contextmanager
        def _db(self):yield self.db
    class Semantic:
        def __init__(self):self.sha='a'*64
        def source(self,pid,aid,vid):return {'sha256':self.sha}
        def latest(self,pid,aid,vid,source=None):return SimpleNamespace(revision=3,state='confirmed')
    class Native:
        def verify(self,aid):return {'valid':True}
    class Advanced:
        def result(self,pid,jid):return _artifact()
    projects=FakeProjects();semantic=Semantic()
    args=dict(projects=projects,native=Native(),semantic=semantic,
           registry=SimpleNamespace(_actor=lambda:'owner'),advanced=Advanced())
    result=list_advanced_batches('p','asset','base',**args)
    assert result['candidate_count']==2 and len(result['batches'])==1
    semantic.sha='z'*64
    changed=list_advanced_batches('p','asset','base',**args)
    assert changed['batches'][0]['status']=='source_stale'
    assert changed['candidate_count']==0
    with pytest.raises(PermissionError):list_advanced_batches('foreign','asset','base',**args)
