"""Build a Python 3.12/x86_64 Lambda ZIP; no AWS calls or credentials required."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile

BACKEND = Path(__file__).resolve().parents[1]
LIMIT = 250 * 1024**2


def package_name(name):
    parts = PurePosixPath(name).parts
    if not parts or name.endswith('/'):
        return None
    if name.startswith('/') or any(p in ('..', '.') for p in parts):
        raise ValueError('Unsafe wheel member')
    if any(p in ('tests', 'test', '__pycache__') for p in parts):
        return None
    if parts[0].endswith('.data'):
        if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
            return None
        parts = parts[2:]
    return '/'.join(parts)


def build(output, wheels):
    seen = {}
    total = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        def add(name, data):
            nonlocal total
            digest = hashlib.sha256(data).hexdigest()
            if name in seen:
                if seen[name] != digest:
                    raise ValueError(f'Conflicting package member: {name}')
                return
            seen[name] = digest
            total += len(data)
            if total >= LIMIT:
                raise ValueError('Uncompressed package exceeds the 250 MiB Lambda quota')
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
        for wheel in sorted(wheels.glob('*.whl')):
            with zipfile.ZipFile(wheel) as source:
                for member in sorted(source.namelist()):
                    name = package_name(member)
                    if name:
                        add(name, source.read(member))
        for path in sorted((BACKEND / 'app').rglob('*.py')):
            add(path.relative_to(BACKEND).as_posix(), path.read_bytes())
        add('data/anomalies.csv', (BACKEND / 'data/anomalies.csv').read_bytes())
    return {
        'file': str(output), 'runtime': 'python3.12', 'architecture': 'x86_64',
        'bytes_uncompressed': total, 'bytes_compressed': output.stat().st_size,
        'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'wheels': [p.name for p in sorted(wheels.glob('*.whl'))],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wheel-cache', type=Path, help='Optional existing wheel cache; still resolved by pip')
    parser.add_argument('--offline', action='store_true', help='Resolve exclusively from --wheel-cache')
    args = parser.parse_args()
    if args.offline and not args.wheel_cache:
        parser.error('--offline requires --wheel-cache')
    with tempfile.TemporaryDirectory(prefix='hylogger-wheels-') as temp:
        wheels = Path(temp)
        command = [sys.executable, '-m', 'pip', 'download', '-r', str(BACKEND / 'requirements-lambda.txt'),
                   '--dest', str(wheels), '--only-binary=:all:', '--python-version', '3.12',
                   '--implementation', 'cp', '--abi', 'cp312', '--platform', 'manylinux_2_28_x86_64',
                   '--platform', 'manylinux_2_27_x86_64', '--platform', 'manylinux2014_x86_64']
        if args.wheel_cache:
            command += ['--find-links', str(args.wheel_cache.resolve())]
        if args.offline:
            command += ['--no-index']
        subprocess.run(command, check=True)
        manifest = build(args.output.resolve(), wheels)
    args.output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'wheels'}, indent=2))


if __name__ == '__main__':
    main()
