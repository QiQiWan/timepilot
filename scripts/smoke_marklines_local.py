"""Optional large-data acceptance on a real user-provided ZIP, local only."""
from pathlib import Path
from timepilot.smart_import import SmartImportService, TransformRecipe
import time, json, argparse
p=argparse.ArgumentParser();p.add_argument('zip_path',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
service=SmartImportService(a.output)
with a.zip_path.open('rb') as f:
    begin=time.monotonic();m=service.ingest(f,a.zip_path.name)
print('DISCOVERY',len(m['files']),len(m['candidates']),len(m['groups']),len(m['suggested_candidate_ids']),round(time.monotonic()-begin,2),flush=True)
recipe=TransformRecipe(candidate_ids=m['suggested_candidate_ids'],name='跨年度车型月度明细')
begin=time.monotonic();version=service.transform(m['import_id'],recipe)
print('TRANSFORMATION',version['counts'],version['series_count'],version['period_min'],version['period_max'],round(time.monotonic()-begin,2),flush=True)
(a.output/'RESULT.json').write_text(json.dumps({'import_id':m['import_id'],'version':version},ensure_ascii=False,indent=2),encoding='utf-8')
