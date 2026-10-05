"""Install the approved package or initialize a new project without overwriting work."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAMES = ('jianying-amv-workflow', 'video-media-review', 'video-workflow-learning')

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def files(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}

def copy_new_tree(src, dst):
    if dst.exists():
        if files(src) == files(dst):
            return 'already_identical'
        raise ValueError(f'Existing different files are preserved: {dst}. Review the update before replacing them.')
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return 'installed'

def install(home):
    home = home.expanduser().resolve()
    skills_root = home / '.agents' / 'skills'
    data_root = home / '.agents' / 'video-workflow'
    # Validate every destination before starting; a partial install never destroys an earlier version.
    sources = [(ROOT / 'skills' / name, skills_root / name) for name in NAMES]
    sources += [(ROOT / 'toolkit', data_root / 'toolkit'), (ROOT / 'templates', data_root / 'templates')]
    for src, dst in sources:
        if not src.is_dir():
            raise ValueError(f'Incomplete package: {src}')
        if dst.is_symlink():
            raise ValueError(f'Refusing a symlinked install destination: {dst}')
        if dst.exists() and files(src) != files(dst):
            raise ValueError(f'Existing different installation is preserved: {dst}')
    profile_src = ROOT / 'data' / 'user-profile.json'
    profile_dst = data_root / 'user-profile.json'
    if profile_dst.exists() and read(profile_src) != read(profile_dst):
        raise ValueError('Existing personal profile differs; approve a rule update before replacing it.')
    result = {str(dst): copy_new_tree(src, dst) for src, dst in sources}
    if not profile_dst.exists():
        write_new(profile_dst, read(profile_src))
    (data_root / 'knowledge').mkdir(parents=True, exist_ok=True)
    index = data_root / 'knowledge' / 'INDEX.json'
    if not index.exists():
        write_new(index, {'schema_version': '1.0', 'entries': [], 'promotions_require_user_confirmation': True})
    # Seed factual case records without replacing a user's later knowledge or promoting rules.
    seed_root = ROOT / 'data' / 'knowledge'
    if seed_root.is_dir():
        current_index = read(index)
        seed_index = read(seed_root / 'INDEX.json')
        for entry in seed_index.get('entries', []):
            if any(row.get('id') == entry['id'] for row in current_index['entries']):
                continue
            copy_new_tree(seed_root / entry['directory'], data_root / 'knowledge' / entry['directory'])
            current_index['entries'].append(entry)
        # Only this directory's owned index is changed; SKILL/profile rules stay unchanged.
        index.write_text(json.dumps(current_index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    receipt = {
        'installed_at': datetime.now(timezone.utc).isoformat(),
        'package_version': '1.0.0', 'destinations': result,
        'profile_revision': read(profile_dst)['revision'],
        'source_sha256': files(ROOT / 'skills'),
        'discovery_status': 'installed_pending_actual_session_check',
        'global_AGENTS_modified': False,
    }
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    write_new(data_root / 'install-receipts' / f'{name}.json', receipt)
    return receipt

def init_project(dest, home):
    dest = dest.expanduser().resolve()
    if dest.exists():
        raise ValueError(f'Choose a new project directory; existing directory is preserved: {dest}')
    home = home.expanduser().resolve()
    data_root = home / '.agents' / 'video-workflow'
    template = data_root / 'templates' / 'project'
    if not template.is_dir():
        template = ROOT / 'templates' / 'project'
    profile = data_root / 'user-profile.json'
    if not profile.is_file():
        raise ValueError('Install this approved workflow in the target home first.')
    shutil.copytree(template, dest)
    state_path = dest / 'PROJECT_STATE.json'
    state = read(state_path)
    state['project_id'] = dest.name
    state['project_root'] = '.'
    # The user location is deliberately home-relative so migration does not embed another PC username.
    state['profile_path'] = '~/.agents/video-workflow/user-profile.json'
    state.setdefault('constraints', {})['profile_revision'] = read(profile)['revision']
    state['created_at'] = datetime.now(timezone.utc).isoformat()
    # This is a frozen derived record of the startup rules, never a second editable profile.
    snapshot_path = 'constraints/SNAPSHOT_v1.json'
    write_new(dest / snapshot_path, {
        'schema_version': '1.0', 'record_role': 'derived_constraint_snapshot_not_rule_authority',
        'constraints_revision': state['constraints']['revision'],
        'profile_source': state['profile_path'], 'profile_revision': read(profile)['revision'],
        'profile_sha256': hashlib.sha256(profile.read_bytes()).hexdigest(),
        'captured_at_utc': state['created_at'], 'profile_values': read(profile),
        'project_overrides': state['constraints']['project_overrides'],
    })
    state['constraints']['snapshot_path'] = snapshot_path
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    directories = [state.get('paths', {}).get(key) for key in ('review_root', 'delivery_root')]
    directories += ['handoff', 'backups', 'learning']
    for name in directories:
        if name:
            resolved = (dest / name).resolve()
            if not resolved.is_relative_to(dest):
                raise ValueError('Template directory escapes the new project')
            resolved.mkdir(exist_ok=True)
    return {'project_root': str(dest), 'state': str(state_path), 'phase': state['phase'],
            'next_action': '从项目根目录打开Codex；先检查环境与素材，再多轮讨论需求。'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('install', 'init-project'):
        p = sub.add_parser(name)
        p.add_argument('--home', type=Path, default=Path.home())
        if name == 'init-project':
            p.add_argument('--dest', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = install(args.home) if args.command == 'install' else init_project(args.dest, args.home)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
