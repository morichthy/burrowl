"""Prepare and verify a frozen, synthetic example in an isolated workspace.

Default preparation and rollback are previews. Writes require --apply, never
overwrite an existing destination, and never place historical Runs in the
source checkout's root runs/. Exit 0 means the requested checks passed; exit 1
means an input, integrity, or verification failure. Only the standard library
and the framework's own modules are required; verification does not need plots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parent
ROOT = EXAMPLE.parents[1]
OWNER = 'RES-FLOATING-POINT-SUMMATION'
RECEIPT = '.example-preparation.json'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def inside(base: Path, relative: str) -> Path:
    """Reject redirection and traversal before reading or writing any fixture."""
    raw = base / relative
    resolved = raw.resolve()
    if (not resolved.is_relative_to(base.resolve()) or resolved != raw.absolute()
            or '..' in Path(relative).parts):
        raise ValueError(f'Unsafe relative path: {relative}')
    return resolved


def destination(value: str) -> Path:
    # Keep all materialized historical paths under one ignored development area.
    # Requiring a child prevents rollback from targeting the shared examples root.
    allowed = ROOT / '.local/examples'
    target = inside(ROOT, value)
    if target == allowed or not target.is_relative_to(allowed):
        raise ValueError('Destination must be a child of .local/examples/')
    return target


def source_plan() -> dict[str, bytes]:
    """Combine framework files with explicitly pinned example inputs only."""
    sys.path.insert(0, str(ROOT / 'automation/scripts'))
    import deployment
    files = {}
    for name in set(deployment.framework_files(ROOT)) | set(deployment.seed_files(ROOT)):
        files[name] = inside(ROOT, name).read_bytes()
    manifest = read_json(EXAMPLE / 'fixtures/restore-manifest.json')
    for item in manifest['entries']:
        data = inside(EXAMPLE, item['source']).read_bytes()
        if digest(data) != item['sha256']:
            raise ValueError(f'Frozen example bytes changed: {item["source"]}')
        # Refuse a crafted fixture that attempts to replace framework programs.
        if not item['target'].startswith(('research/floating-point-summation/', 'runs/run-')):
            raise ValueError('Fixture target is outside the selected example')
        files[item['target']] = data
    files['retrieval/sources.json'] = (EXAMPLE / 'fixtures/sources.json').read_bytes()
    for kind in ('process', 'report'):
        name = f'fixtures/read-{kind}.request.json'
        files['research/floating-point-summation/' + name] = (EXAMPLE / name).read_bytes()
    return files


def prepare(target: Path, apply: bool):
    if target.exists():
        raise ValueError('Destination already exists; use a new name or verified rollback')
    files = source_plan()
    receipt = {'schema_version': 1, 'kind': 'floating-point-development-example',
               'files': {name: digest(data) for name, data in sorted(files.items())}}
    # Resolve every destination up front, before the first write.
    for name in files:
        inside(target, name)
    if apply:
        target.mkdir(parents=True)
        # Save the full recovery inventory first, so interrupted preparation can
        # be rolled back too. Missing planned files are harmless during rollback.
        (target / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        for name, data in files.items():
            path = inside(target, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return {'action': 'prepare', 'applied': apply, 'destination': str(target),
            'file_count': len(files), 'root_checkout_runs_written': False}


def rollback(target: Path, apply: bool):
    receipt = read_json(inside(target, RECEIPT))
    if receipt.get('kind') != 'floating-point-development-example':
        raise ValueError('Destination is not a prepared example')
    expected = receipt['files']
    actual = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
    extras = actual - set(expected) - {RECEIPT}
    if extras:
        raise ValueError(f'New files exist; preserve them before rollback: {sorted(extras)[:5]}')
    # Check the entire inventory before deleting a single file. Edited files,
    # links, or unknown additions must never be lost to a cleanup convenience.
    for name, pinned in expected.items():
        path = inside(target, name)
        if path.exists() and digest(path.read_bytes()) != pinned:
            raise ValueError(f'Edited file prevents rollback: {name}')
    directories = [p for p in target.rglob('*') if p.is_dir()]
    for path in directories:
        inside(target, path.relative_to(target).as_posix())
    if apply:
        for name in expected:
            inside(target, name).unlink(missing_ok=True)
        (target / RECEIPT).unlink()
        for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            path.rmdir()
        target.rmdir()
    return {'action': 'rollback', 'applied': apply, 'destination': str(target)}


def verify(target: Path):
    receipt = read_json(inside(target, RECEIPT))
    for name, pinned in receipt['files'].items():
        if digest(inside(target, name).read_bytes()) != pinned:
            raise ValueError(f'Prepared file changed: {name}')
    sys.path.insert(0, str(target / 'automation/scripts'))
    from memory.api import dispatch
    from memory.service import MemoryService
    service = MemoryService(target)
    inspected = service.inspect(OWNER)
    if inspected.get('error'):
        raise ValueError(inspected['error'])
    documents = {}
    for kind in ('process', 'report'):
        request = read_json(target / f'research/floating-point-summation/fixtures/read-{kind}.request.json')
        result = dispatch(service, 'document', request)
        if result.get('error'):
            raise ValueError(result['error'])
        expected_sections = 8 if kind == 'process' else 4
        if result.get('missing') or len(result.get('document', {}).get('section_refs', [])) != expected_sections:
            raise ValueError(f'{kind} document has missing evidence or an unexpected section count')
        documents[kind] = result
    materials = dispatch(service, 'raw-materials', {'owner_id': OWNER})
    if materials.get('error'):
        raise ValueError(materials['error'])
    if materials.get('missing') or not materials.get('items'):
        raise ValueError('Raw-material collection has unresolved references')
    for material in materials['items']:
        if material['status'] != 'registered':
            raise ValueError(f'Raw material is unavailable: {material["path"]}')
        if digest(inside(target, material['path']).read_bytes()) != material['sha256']:
            raise ValueError(f'Raw-material bytes changed: {material["path"]}')
    # All verification outputs go outside the prepared source tree. The fixture
    # and preparation receipt remain unchanged and can still be rolled back.
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    output = ROOT / '.local/example-checks' / stamp
    output.mkdir(parents=True)
    run = target / 'research/floating-point-summation/runs/run-20260908t215458z-04c7d84d40d8'
    command = [sys.executable, '-B', str(run / 'reproduce.py'), '--root', str(target),
               '--manifest', str(run / 'source-manifest.json'), '--output', str(output / 'numbers'), '--no-plots']
    completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=120)
    (output / 'stdout.txt').write_text(completed.stdout, encoding='utf-8')
    (output / 'stderr.txt').write_text(completed.stderr, encoding='utf-8')
    if completed.returncode:
        raise ValueError(f'Numerical replay failed; see {output}')
    numbers = read_json(output / 'numbers/verification.json')
    if numbers['checked_results'] != 150 or not numbers['all_saved_results_match']:
        raise ValueError('Numerical replay did not match all 150 fixed results')
    for kind, result in documents.items():
        (output / f'{kind}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'materials.json').write_text(json.dumps(materials, ensure_ascii=False, indent=2), encoding='utf-8')
    result = {'action': 'verify', 'passed': True, 'checked_results': 150,
              'documents_read': list(documents), 'raw_materials_checked': len(materials['items']),
              'output': str(output),
              'scope': 'Frozen synthetic inputs; no scientific or business acceptance implied'}
    (output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify', 'rollback'))
    parser.add_argument('--destination', default='.local/examples/floating-point-summation')
    parser.add_argument('--apply', action='store_true', help='Apply prepare/rollback; otherwise preview only')
    args = parser.parse_args()
    try:
        target = destination(args.destination)
        result = verify(target) if args.action == 'verify' else (
            prepare(target, args.apply) if args.action == 'prepare' else rollback(target, args.apply))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
