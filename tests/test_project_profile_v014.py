"""Candidate full-vs-bounded stats and source traceability."""
import io
from fastapi.testclient import TestClient
from openpyxl import Workbook
from timepilot.api import create_app


def workbook():
    wb=Workbook();ws=wb.active;ws.title='记录'
    ws.append(['Time','A2','A14','available_at'])
    ws.append(['2024-01-01','device1',10,'2024-02-01'])
    ws.append(['2024-02-01','device1',None,'2024-03-01'])
    ws.append(['2024-03-01','device1',5,'2024-04-01'])
    ws.append(['2024-04-01','device1',-4,'2024-05-01'])
    buf=io.BytesIO();wb.save(buf);return buf.getvalue()


def test_candidate_profile_full_and_bounded_with_isolation(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as client:
        a=client.post('/api/v1/projects',json={'name':'a'}).json()['id']
        b=client.post('/api/v1/projects',json={'name':'b'}).json()['id']
        r=client.post(f'/api/v1/projects/{a}/imports',files=[('files',('test.xlsx',workbook()))]);assert r.status_code==201,r.text
        iid=r.json()['import_id'];iid_path=f'/api/v1/projects/{a}/imports/{iid}'
        candidate=client.get(iid_path).json()['candidates'][0]['candidate_id']
        url=iid_path+f'/regions/{candidate}/profile'
        full=client.get(url).json(); assert full['scope']=='full_candidate';assert full['row_count_scanned']==4
        target=next(x for x in full['fields'] if x['name']=='A14')
        assert target['missing']==1 and target['numeric_count']==3
        assert target['numeric_min']=='-4' and target['numeric_max']=='10'
        bound=client.get(url+'?max_rows=2').json();assert bound['scope']=='bounded_incomplete'
        assert bound['truncated'] and bound['row_count_scanned']==2
        assert client.get(f'/api/v1/projects/{b}/imports/{iid}/regions/{candidate}/profile').status_code==404
        # A changed original must fail even if a cached profile is present.
        manifest=app.state.smart_import.manifest(iid)
        source=app.state.smart_import._dir(iid)/manifest['files'][0]['stored']
        source.write_bytes(source.read_bytes()+b'extra')
        assert client.get(url).status_code==422
