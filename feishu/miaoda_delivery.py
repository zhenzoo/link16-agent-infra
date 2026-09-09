"""Inventory explicit web assets and verify a Miaoda release using maintained lark-cli.

Does not upload a repository, change access scope, create a release, or send a message.
Publishing is orchestrated by the installed feishu HTML-to-Miaoda recipe and lark-apps.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root, entry, allowlist):
    root = root.resolve(strict=True)
    names = json.loads(allowlist.read_text(encoding='utf-8-sig'))
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ValueError('files must be a nonempty JSON array of root-relative paths')
    rows = []
    seen = set()
    for name in names:
        logical = PurePosixPath(name.replace('\\', '/'))
        if logical.is_absolute() or '..' in logical.parts or not logical.parts or ':' in name:
            raise ValueError('asset path must stay under the explicit root')
        relative = logical.as_posix()
        if relative.casefold() in seen:
            raise ValueError('duplicate asset path')
        seen.add(relative.casefold())
        if any(p.lower() in ('.git', '.venv', 'node_modules') or p.lower().startswith('.env') for p in logical.parts):
            raise ValueError('configuration or dependency tree is not a web delivery asset')
        target = (root / relative).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError('asset resolves outside the explicit root or is not a file')
        rows.append({'path': relative, 'bytes': target.stat().st_size, 'sha256': sha(target)})
    entry = PurePosixPath(entry.replace('\\', '/')).as_posix()
    if entry not in {r['path'] for r in rows} or Path(entry).suffix.lower() not in ('.html', '.htm'):
        raise ValueError('the exact HTML entry must be included in the allowlist')
    return {'artifact_type': 'web_asset_inventory', 'status': 'inventoried',
            'updated_at': datetime.now(timezone.utc).isoformat(), 'root': str(root), 'entry': entry,
            'files': rows, 'totalBytes': sum(r['bytes'] for r in rows),
            'covers': 'Only explicit allowlisted files, resolved paths, sizes and content hashes.',
            'judge': 'Filesystem measurements; imports, dynamic fetches and browser interactions still require runtime checks.'}


def cli_prefix():
    node = shutil.which('node')
    if node:
        runner = Path(node).parent / 'node_modules/@larksuite/cli/scripts/run.js'
        if runner.is_file():
            return [node, str(runner)]
    cli = shutil.which('lark-cli')
    if cli:
        return [cli]
    raise RuntimeError('maintained lark-cli is required; use the installed lark-apps setup instructions')


def verify_release(project, release_id, expected_commit, profile=None):
    project = project.resolve(strict=True)
    meta = json.loads((project / '.spark/meta.json').read_text(encoding='utf-8-sig'))
    app_id = meta['app_id']
    if not re.fullmatch(r'app_[a-zA-Z0-9]+', app_id) or not re.fullmatch(r'[a-f0-9]{40}', expected_commit):
        raise ValueError('explicit initialized app and full expected Git SHA are required')
    if not re.fullmatch(r'\d+', release_id):
        raise ValueError('invalid release ID')
    subprocess.run(['git', 'cat-file', '-e', expected_commit + '^{commit}'], cwd=project, check=True, capture_output=True)
    command = [*cli_prefix(), 'apps', '+release-get', '--app-id', app_id, '--release-id', release_id, '--as', 'user']
    if profile:
        command.extend(['--profile', profile])
    response = subprocess.run(command, cwd=project, text=True, encoding='utf-8', capture_output=True, timeout=120)
    if response.returncode:
        raise RuntimeError(f'lark-cli release read failed (exit {response.returncode}); inspect the command in the selected identity')
    envelope = json.loads(response.stdout)
    if envelope.get('ok') is not True:
        raise RuntimeError('lark-cli did not confirm a successful release read')
    release = envelope['data'].get('release', envelope['data'])
    matched = release.get('commit_id') == expected_commit
    finished = release.get('status') == 'finished'
    url = release.get('online_url')
    verified = bool(matched and finished and isinstance(url, str) and url.startswith('https://'))
    return {'artifact_type': 'miaoda_release_verification', 'updated_at': datetime.now(timezone.utc).isoformat(),
            'status': 'verified_release' if verified else 'not_verified', 'app_id': app_id,
            'release_id': release_id, 'expected_commit': expected_commit, 'actual_commit': release.get('commit_id'),
            'release_status': release.get('status'), 'online_url': url, 'verification': envelope,
            'covers': 'Remote release finished state and exact local Git object; access scope is unchanged.',
            'judge': 'Miaoda release API. This is not a logged-in, anonymous, media or WebGL browser acceptance.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    inv = commands.add_parser('inventory')
    inv.add_argument('--root', type=Path, required=True); inv.add_argument('--entry', required=True)
    inv.add_argument('--files', type=Path, required=True); inv.add_argument('--output', type=Path, required=True)
    release = commands.add_parser('verify-release')
    release.add_argument('--project', type=Path, required=True); release.add_argument('--release-id', required=True)
    release.add_argument('--expected-commit', required=True); release.add_argument('--cli-profile')
    release.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.root, args.entry, args.files) if args.action == 'inventory' else verify_release(args.project, args.release_id, args.expected_commit, args.cli_profile)
    if args.output.suffix.lower() != '.json':
        raise ValueError('receipt output must be a JSON path')
    if args.action == 'inventory' and args.output.resolve() in {args.files.resolve(), *[(args.root / row['path']).resolve() for row in result['files']]}:
        raise ValueError('receipt must not replace an input asset or allowlist')
    if args.output.exists():
        prior = json.loads(args.output.read_text(encoding='utf-8-sig'))
        if not isinstance(prior, dict) or prior.get('artifact_type') != result['artifact_type']:
            raise ValueError('refusing to overwrite an unrelated existing file')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('verification', 'files')}, ensure_ascii=False))
    raise SystemExit(1 if result['status'] == 'not_verified' else 0)


if __name__ == '__main__':
    main()
