"""Read-only integration check against configured dependencies or a deployed API."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check(api):
    def one(rows, label):
        assert len(rows) == 1, f'{label}: expected one unambiguous source selection'
        return rows[0]

    def get(path, params=None):
        response = api.get(path, params=params)
        if response.status_code != 200:
            raise RuntimeError(f'{path}: HTTP {response.status_code}')
        return response

    health = get('/api/health').json()
    assert health['status'] == 'ok', 'Backend configuration is not ready'
    catalogue = get('/v1/boreholes').json()['items']
    holes = {row['hole_id'] for row in catalogue}
    assert holes, 'Empty active release'
    datasets = get('/v1/boreholes/07THD002/datasets').json()['items']
    dataset = one([d for d in datasets if d['source_dataset_name'] == '07THD002' and d['axis_id']], '07THD002 dataset')
    revision, axis = dataset['dataset_revision_id'], dataset['axis_id']
    base = f'/v1/datasets/{revision}'
    logs = get(base + '/logs').json()['items']
    mineral = one([l for l in logs if l['source_log_name'] == 'Min1 sTSAS' and l['log_kind'] == 'scalar'
                   and l['axis_id'] == axis], 'Min1 sTSAS')
    sample = get(base + '/samples/15', {'axis_id': axis, 'log_ids': mineral['log_id']}).json()
    assert sample['sample_no'] == 15 and sample['md_m'] == 62.50175
    assert sample['results'][0]['value']['value_text'] == 'Muscovite'
    assert sample['image_status'] == 'available'
    image_id = sample['images'][0]['image_asset_id']
    png = get(f'/v1/image-assets/{image_id}/content')
    assert png.content.startswith(b'\x89PNG\r\n\x1a\n')
    image_only = get(base + '/samples/15', {'axis_id': axis, 'include_results': 'false'}).json()
    assert image_only['results'] == []
    spectral_log = one([l for l in logs if l['source_log_name'] == 'Reflectance' and l['spectral_region'] == 'VSWIR'
                        and l['axis_id'] == axis], 'VSWIR Reflectance')
    spectral = get(base + '/samples/15', {'axis_id': axis, 'log_ids': spectral_log['log_id']}).json()['results'][0]
    assert spectral['status'] == 'available' and len(spectral['spectra']) == len(spectral['wavelength']) == 531
    scalar_page = get(base + f"/logs/{mineral['log_id']}/values", {'axis_id': axis, 'limit': 100}).json()
    assert next(v for v in scalar_page['items'] if v['sample_no'] == 15)['value_text'] == 'Muscovite'
    profile = one([l for l in logs if l['log_kind'] == 'profile' and l['availability_status'] == 'payload_present'
                   and l['axis_id'] == axis], 'profile')
    profile_page = get(base + f"/profile-logs/{profile['log_id']}/values", {'axis_id': axis, 'limit': 2}).json()
    assert len(profile_page['items']) == 2 and len(profile_page['items'][0]['values']) > 0
    page = get(base + '/samples', {'axis_id': axis, 'limit': 100}).json()
    second = get(base + '/samples', {'axis_id': axis, 'limit': 100, 'after_sample': page['next_after_sample']}).json()
    assert len(page['items']) == 100 and len(second['items']) == 100
    assert max(r['sample_no'] for r in page['items']) < min(r['sample_no'] for r in second['items'])
    duplicate_datasets = get('/v1/boreholes/05KCD001/datasets').json()['items']
    duplicate_dataset = one([d for d in duplicate_datasets if d['source_dataset_name'] == '05KCD001' and d['axis_id']], '05KCD001 dataset')
    samples = [get(f"/v1/datasets/{duplicate_dataset['dataset_revision_id']}/samples/{n}",
                   {'axis_id': duplicate_dataset['axis_id'], 'include_results': 'false'}).json() for n in (129, 130)]
    assert samples[0]['md_m'] == samples[1]['md_m']
    assert all(s['image_status'] == 'available' for s in samples)
    assert samples[0]['images'][0]['image_asset_id'] != samples[1]['images'][0]['image_asset_id']
    return {'status': 'passed', 'release_id': health['release_id'], 'unique_holes': len(holes),
            'catalogue_rows': len(catalogue), 'sample15': 'Muscovite', 'spectrum_channels': 531,
            'image_sha256': hashlib.sha256(png.content).hexdigest(),
            'checks': ['catalogue', 'dataset identity', 'mineral', 'PNG', 'image only', 'spectrum', 'scalar page', 'profile page', 'pagination', 'repeated depth identity']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', help='Omit to run the FastAPI app against environment-configured dependencies')
    args = parser.parse_args()
    if args.base_url:
        import httpx
        api = httpx.Client(base_url=args.base_url.rstrip('/'), timeout=120)
    else:
        from fastapi.testclient import TestClient
        from app.main import app
        api = TestClient(app)
    try:
        with api:
            print(json.dumps(check(api), indent=2))
    except Exception as exc:
        # No DSNs, credentials or provider exception text in reports.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
