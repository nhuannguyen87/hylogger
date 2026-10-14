"""HTTP contract tests that never connect to a database or AWS."""
from uuid import UUID
from fastapi.testclient import TestClient
import app.main as main

client = TestClient(main.app)
REVISION = 'ca3660de-f678-5256-89f5-40d17832e4eb'
AXIS = 'b9ba28cd-0216-5314-83df-f6247b0f56b6'
LOG = 'a4a5c7d3-0fe8-53be-97a2-02a5c7771ae8'


def test_image_only_sample_does_not_read_every_log(monkeypatch):
    calls = []
    monkeypatch.setattr(main, 'get_sample_by_revision', lambda **kw: calls.append(kw) or {'sample_no': kw['sample_no'], 'results': []})
    response = client.get(f'/v1/datasets/{REVISION}/samples/15', params={'axis_id': AXIS, 'include_results': 'false'})
    assert response.status_code == 200
    assert calls[0]['log_ids'] == []
    assert calls[0]['axis_id'] == AXIS


def test_explicit_log_and_legacy_default_are_preserved(monkeypatch):
    calls = []
    monkeypatch.setattr(main, 'get_sample_by_revision', lambda **kw: calls.append(kw) or {})
    client.get(f'/v1/datasets/{REVISION}/samples/15', params={'axis_id': AXIS, 'log_ids': LOG})
    client.get(f'/v1/datasets/{REVISION}/samples/15', params={'axis_id': AXIS})
    assert calls[0]['log_ids'] == [LOG]
    assert calls[1]['log_ids'] is None


def test_sample_cursor_and_repeated_depths_are_not_merged(monkeypatch):
    rows = [{'sample_no':129,'md_m':10}, {'sample_no':130,'md_m':10}]
    monkeypatch.setattr(main, 'get_samples_by_revision', lambda **kw: {'items':rows,'next_after_sample':130})
    response = client.get(f'/v1/datasets/{REVISION}/samples', params={'axis_id':AXIS,'after_sample':0})
    assert response.status_code == 200
    assert response.json()['items'] == rows
    assert response.json()['next_after_sample'] == 130


def test_lambda_function_url_adapter():
    from app.lambda_handler import handler
    event = {
        'version':'2.0','routeKey':'$default','rawPath':'/','rawQueryString':'',
        'headers':{'host':'example.lambda-url.ap-southeast-2.on.aws','x-forwarded-proto':'https'},
        'requestContext':{'http':{'method':'GET','path':'/','sourceIp':'127.0.0.1','protocol':'HTTP/1.1'},'stage':'$default'},
        'isBase64Encoded':False,
    }
    assert handler(event,{})['statusCode'] == 200


def test_storage_health_does_not_claim_cwd_or_s3_was_verified(monkeypatch):
    from app.media_reader import storage_health
    monkeypatch.setenv('ETL4_ASSET_BACKEND', 'local')
    monkeypatch.delenv('ETL4_ROOT', raising=False)
    assert storage_health()['asset_storage_configured'] is False
    monkeypatch.setenv('ETL4_ASSET_BACKEND', 's3')
    for key in ('ETL4_S3_BUCKET', 'ETL4_S3_PREFIX', 'ETL4_AWS_REGION'):
        monkeypatch.setenv(key, 'configured')
    assert storage_health()['asset_storage_configured'] is True
    assert storage_health()['asset_root_available'] is None
    assert storage_health()['media_verified_by_health'] is False


def test_dependency_failure_is_safe_and_keeps_cors(monkeypatch):
    def fail():
        raise RuntimeError('password=must-not-appear')
    monkeypatch.setattr(main, 'get_boreholes_v1', lambda **kw: fail())
    response = client.get('/v1/boreholes', headers={'Origin':'http://localhost:3000'})
    assert response.status_code == 503
    assert 'must-not-appear' not in response.text
    assert response.headers['access-control-allow-origin'] == 'http://localhost:3000'
    response = client.get('/', headers={'Origin':'https://untrusted.example'})
    assert 'access-control-allow-origin' not in response.headers


def test_invalid_selection_still_returns_404(monkeypatch):
    def fail(**kw):
        raise ValueError('Result log is outside selected dataset revision')
    monkeypatch.setattr(main, 'get_sample_by_revision', fail)
    response = client.get(f'/v1/datasets/{REVISION}/samples/15', params={'axis_id':AXIS, 'log_ids':LOG})
    assert response.status_code == 404
    assert response.json()['detail'] == 'Result log is outside selected dataset revision'


def test_paginated_chunk_reads_selected_s3_row_group(monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    import app.media_reader as reader
    sink = pa.BufferOutputStream()
    pq.write_table(pa.table({'sample_no': [129, 130], 'value_text': ['A', 'B']}), sink, row_group_size=1)
    payload = sink.getvalue().to_pybytes()
    class Store:
        def asset(self, conn, asset_id):
            return {'id': asset_id}
        def full(self, asset):
            return payload
    monkeypatch.setenv('ETL4_ASSET_BACKEND', 's3')
    monkeypatch.setattr(reader, 'get_s3_store', Store)
    monkeypatch.setattr(reader, 'local_asset', lambda *args: (_ for _ in ()).throw(AssertionError('S3 must not read local files')))
    table = reader.read_chunk_table(None, {'asset_id':'test', 'row_group':1})
    assert table.to_pylist() == [{'sample_no':130, 'value_text':'B'}]
