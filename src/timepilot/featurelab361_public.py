"""Optional, manual download of license-documented public UCI benchmark CSVs.

No website scraping, no training-data redistribution: data comes only from the
UCI download endpoint on explicit operator request. Never claim that public
AI4I data proves factory-specific generalization (AI4I itself is synthetic).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile

PUBLIC = {
  'ai4i': {
    'dataset_id':601,
    'url':'https://archive.ics.uci.edu/static/public/601/ai4i%2B2020%2Bpredictive%2Bmaintenance%2Bdataset.zip',
    'csv':'ai4i2020.csv','doi':'10.24432/C5HS5C',
    'license':'CC BY 4.0', 'kind':'public_synthetic',
    'title':'AI4I 2020 Predictive Maintenance Dataset',
    'credit':'UCI Machine Learning Repository; S. Matzka (2020)',
  },
  'appliances': {
    'dataset_id':374,
    'url':'https://archive.ics.uci.edu/static/public/374/appliances%2Benergy%2Bprediction.zip',
    'csv':'energydata_complete.csv','doi':'10.24432/C5VC8G',
    'license':'CC BY 4.0','kind':'public_observational',
    'title':'Appliances Energy Prediction',
    'credit':'UCI Machine Learning Repository; L. Candanedo (2017)',
  },
}
MAX_COMPRESSED=30*1024*1024
MAX_CSV=70*1024*1024


def download_public(dataset: str,out: Path,*,timeout=30,force=False):
    if dataset not in PUBLIC:raise ValueError('unknown public dataset')
    meta=PUBLIC[dataset]
    target=out.expanduser().resolve()
    target.mkdir(parents=True,exist_ok=True)
    uri=urlparse(meta['url'])
    if uri.scheme!='https' or uri.hostname!='archive.ics.uci.edu':
        raise ValueError('only official UCI HTTPS origin is allowed')
    if target.joinpath(meta['csv']).exists() and not force:
        raise FileExistsError('public dataset exists; verify its SHA instead of overwriting')
    request=Request(meta['url'], headers={'User-Agent':'TimePilot-functional-QA/1.0'})
    with urlopen(request,timeout=timeout) as response:
        resolved=urlparse(response.geturl())
        if resolved.scheme!='https' or resolved.hostname!='archive.ics.uci.edu':
            raise ValueError('source redirects outside official HTTPS UCI origin')
        body=response.read(MAX_COMPRESSED+1)
        if len(body)>MAX_COMPRESSED:raise ValueError('public download exceeds safety limit')
    with zipfile.ZipFile(io.BytesIO(body)) as bundle:
        names=[i for i in bundle.infolist()
               if not i.is_dir() and Path(i.filename).name==meta['csv']]
        if len(names)!=1 or names[0].file_size>MAX_CSV:
            raise ValueError('CSV file missing, ambiguous, or too large')
        with bundle.open(names[0]) as stream:
            data=stream.read(MAX_CSV+1)
            if len(data)>MAX_CSV:raise ValueError('CSV decompression exceeds limit')
    text=data.decode('utf-8-sig')
    reader=csv.DictReader(io.StringIO(text))
    fieldnames=reader.fieldnames or []
    if len(fieldnames)<3:raise ValueError('downloaded public CSV has no valid header')
    rows=sum(1 for _ in reader)
    if rows<100:raise ValueError('public CSV has too few records')
    target_file=target/meta['csv']
    with tempfile.NamedTemporaryFile(mode='wb',dir=target,delete=False,prefix='.uci-qa-',suffix='.csv') as f:
        staged=Path(f.name);f.write(data)
    staged.replace(target_file)
    receipt={**meta, 'download_confirmed':True,'source_sha256':hashlib.sha256(data).hexdigest(),
       'rows':rows,'columns':fieldnames,'raw_bytes':len(data),
       'production_data':False, 'verified_for_modeling':False,
       'attribution_url':'https://archive.ics.uci.edu/dataset/'+str(meta['dataset_id']),
       'notes':'Fetched from official UCI on explicit request; cite DOI and CC BY 4.0.'}
    (target/(dataset+'_PROVENANCE.json')).write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt
