#!/usr/bin/env python3
"""v0.31.0: audit durable Agent plans with real business reviewer decisions.

A successful tool plan alone is not evidence of business value. An independent
review sheet must explicitly identify the plan and source type, review outcome,
manual corrections and time saved; synthetic/demo records are excluded.
The script never modifies an application database and fails closed.
"""
from __future__ import annotations
import argparse,csv,json,sqlite3,sys
from pathlib import Path

FIELDS=['project_id','plan_id','source_kind','reviewer','accepted','manual_corrections','measured_time_saved_minutes','comment']

def rate(ok,all_):return round(ok/all_,4) if all_ else None

def assess(catalog:Path,project:str,records:list[dict],min_tasks:int=10):
    with sqlite3.connect(f'file:{catalog.resolve()}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        plans={r['plan_id']:dict(r) for r in db.execute('''SELECT plan_id,project_id,status,source_sha256,
            created_at,updated_at FROM feature_agent_plans WHERE project_id=?''',(project,))}
        step_rows={}
        for step in db.execute('''SELECT plan_id,status,tool_id FROM feature_agent_steps
            WHERE plan_id IN (SELECT plan_id FROM feature_agent_plans WHERE project_id=?)''',(project,)):
            step_rows.setdefault(step['plan_id'],[]).append(dict(step))
    problems=[];verified=[];seen=set()
    if len(records)<min_tasks:problems.append(f'业务样本不足，至少需要 {min_tasks} 个独立计划')
    for ix,item in enumerate(records,2):
        key=item.get('plan_id','').strip()
        if not key or key in seen:
            problems.append(f'第 {ix} 行计划 ID 为空或重复');continue
        seen.add(key);plan=plans.get(key)
        if item.get('project_id','').strip()!=project or plan is None:
            problems.append(f'第 {ix} 行项目或计划不属于当前项目');continue
        if item.get('source_kind','').strip()!='real_business':
            problems.append(f'第 {ix} 行未声明真实业务任务，拒绝将示例混入验收');continue
        if not item.get('reviewer','').strip():
            problems.append(f'第 {ix} 行缺少人工复核人员');continue
        accepted=item.get('accepted','').strip().lower()
        if accepted not in ('yes','no'):
            problems.append(f'第 {ix} 行人工验收必须为 yes 或 no');continue
        try:
            corrections=int(item.get('manual_corrections',''))
            saved=float(item.get('measured_time_saved_minutes',''))
            if corrections<0 or not -10000<=saved<=10000:raise ValueError
        except ValueError:
            problems.append(f'第 {ix} 行人工改动次数或实测节省工时无效');continue
        if not item.get('comment','').strip():
            problems.append(f'第 {ix} 行缺少业务验收说明');continue
        steps=step_rows.get(key,[])
        status=plan['status'];completed=status=='succeeded' and bool(steps) and all(x['status']=='succeeded' for x in steps)
        verified.append({'plan_id':key,'plan_status':status,'agent_completed':completed,
            'reviewer_acceptance':accepted=='yes','manual_corrections':corrections,
            'measured_time_saved_minutes':saved,'step_count':len(steps),
            'source_sha256_prefix':plan['source_sha256'][:12]})
    count=len(verified)
    completed=sum(x['agent_completed'] for x in verified)
    accepted=sum(x['agent_completed'] and x['reviewer_acceptance'] for x in verified)
    # Business acceptance requires both a successfully finished tool plan and a
    # reviewer-approved outcome. Reject if any annotation is incomplete.
    if count<min_tasks:problems.append('有效真实业务任务数量未达到验收下限')
    result={'version':'0.31.0','status':'blocked' if problems else 'passed','project_id':project,
      'scope':'real_user_reviewed_agent_plans','eligible_tasks':count,
      'agent_completed':completed,'business_accepted':accepted,
      'agent_completion_rate':rate(completed,count),'business_acceptance_rate':rate(accepted,count),
      'average_manual_corrections':round(sum(x['manual_corrections'] for x in verified)/count,3) if count else None,
      'measured_total_time_saved_minutes':round(sum(x['measured_time_saved_minutes'] for x in verified),3) if count else None,
      'issues':problems,'records':verified,
      'caveat':'来源 real_business 为业务审核人员声明，需独立审查实际任务内容；无业务方记录时不能推断成功率'}
    return result

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--catalog',type=Path)
    p.add_argument('--project')
    p.add_argument('--annotations',type=Path)
    p.add_argument('--min-tasks',type=int,default=10)
    p.add_argument('--out',type=Path,default=Path('reports/v031/agent_business.json'))
    p.add_argument('--template',type=Path,help='Write empty review sheet header; never populate fake results')
    a=p.parse_args(argv)
    if a.template:
        a.template.parent.mkdir(parents=True,exist_ok=True)
        with a.template.open('w',encoding='utf-8-sig',newline='') as h:csv.writer(h).writerow(FIELDS)
        print('Empty reviewer template:',a.template)
        return 0
    try:
        if not a.catalog or not a.project or not a.annotations:raise ValueError('请明确指定项目数据库、项目 ID 和业务人员复核表')
        if not a.catalog.is_file() or not a.annotations.is_file():raise ValueError('业务验收输入文件不存在')
        with a.annotations.open(encoding='utf-8-sig',newline='') as h:
            reader=csv.DictReader(h)
            if not reader.fieldnames or set(FIELDS)-set(reader.fieldnames):raise ValueError('复核表缺少必需字段')
            data=list(reader)
        result=assess(a.catalog,a.project,data,min_tasks=a.min_tasks)
    except Exception as exc:
        result={'version':'0.31.0','status':'blocked','issues':[type(exc).__name__+': '+str(exc)]}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
