"""UCI public QA fetch has explicit permission, fixed host and bounded archives."""
from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from timepilot.featurelab361_public import download_public, PUBLIC


class FakeResponse:
    def __init__(self, data: bytes, url:str):self.data=data;self.url=url
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def geturl(self):return self.url
    def read(self,count):return self.data[:count]


def test_public_registry_documents_license_and_attribution():
    assert set(PUBLIC)=={'ai4i','appliances'}
    for case in PUBLIC.values():
        assert case['url'].startswith('https://archive.ics.uci.edu/')
        assert case['license']=='CC BY 4.0'
        assert case['doi']


def test_download_only_explicit_official_UCI_and_sha256(tmp_path, monkeypatch):
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('nested/ai4i2020.csv','UDI,a,b,Machine failure\n'+''.join(f'{i},{i},{2*i},{i%2}\n' for i in range(130)))
    import timepilot.featurelab361_public as m
    monkeypatch.setattr(m,'urlopen',lambda req,timeout=30:FakeResponse(buffer.getvalue(),PUBLIC['ai4i']['url']))
    result=download_public('ai4i',tmp_path)
    assert result['rows']==130 and result['production_data'] is False
    assert result['source_sha256'] and (tmp_path/'ai4i2020.csv').is_file()
    with pytest.raises(FileExistsError):download_public('ai4i',tmp_path)


def test_external_redirect_rejected(tmp_path,monkeypatch):
    import timepilot.featurelab361_public as m
    monkeypatch.setattr(m,'urlopen',lambda req,timeout=30:FakeResponse(b'', 'https://untrusted.example.com/foo.zip'))
    with pytest.raises(ValueError,match='redirects'):
        download_public('ai4i',tmp_path)


def test_unknown_public_dataset_rejected(tmp_path):
    with pytest.raises(ValueError,match='unknown'):
        download_public('unofficial',tmp_path)
