"""Evidence-only observations of individual BI tool steps.

No data rows are persisted in the audit store. A plan may be simulated and
checked stage-by-stage; only the legacy audited CAS commit modifies the saved
workspace. Read tools return actual read evidence, not a fake Viewer receipt.
"""
from __future__ import annotations
import copy
import hashlib
from timepilot.bi_agent_service import signature
from timepilot.bi_tool_registry import validate_action,TOOLS


def observe_steps(service,pid,workspace,actions,*,confirmed=False):
    state=copy.deepcopy(workspace['state']);results=[]
    for index,action in enumerate(actions):
        args=validate_action(action)
        effect=TOOLS[action.tool][1]
        if effect=='read':
            observed=service._read(pid,state,action.tool,args)
            sample_type='authorized_read'
            data_signature=signature(observed)
            count=(observed.get('row_count') or observed.get('returned') or
                   len(observed.get('fields',[])) or len(observed.get('rows',[]))) if isinstance(observed,dict) else None
            next_state=state
        else:
            next_state,_=service._transition(pid,state,action.tool,args,confirmed=confirmed)
            # Calculate a genuine bounded result after each transformation to
            # detect an invalid intermediate operator before committing any step.
            observed=service.engine.execute(pid,next_state,limit=5)
            sample_type='real_sql_preview'
            data_signature=signature(observed.get('rows',[]) if isinstance(observed,dict) else observed)
            count=observed.get('returned') if isinstance(observed,dict) else None
        results.append({'ordinal':index,'tool':action.tool,'effect':effect,
                        'observation_type':sample_type,'preview_rows':count,
                        'source_sha256':workspace['source_sha256'],
                        'input_state_sha256':signature(state),
                        'output_state_sha256':signature(next_state),
                        'result_sha256':data_signature,
                        'preview_scope':'bounded_sample_not_full'} )
        state=next_state
    return results
