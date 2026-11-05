"""Official V2 task import with explicit actor/evaluator field separation."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib

from .events import IntegrityError
from .finance import canonical

OFFICIAL_REPOSITORY = 'https://github.com/scaleapi/SWE-bench_Pro-os'
SOURCE_COMMIT = '66f92766bba642462d4bbe5479e83f91f9211862'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def task_path(source, task_id):
    if not re.fullmatch(r'instance_[A-Za-z0-9_.-]{1,220}', task_id):
        raise IntegrityError('Invalid official task identifier')
    root = Path(source).resolve()
    path = root / 'v2' / 'tasks' / task_id
    if not path.resolve().is_relative_to(root / 'v2' / 'tasks') or path.is_symlink():
        raise IntegrityError('Unsafe task path')
    return path


def import_task(source, task_id):
    root = Path(source).resolve()
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()
    if revision != SOURCE_COMMIT:
        raise IntegrityError('Official source revision differs from the declared development pin')
    ids = (root / 'v2/hard51_ids.txt').read_text().splitlines()
    if task_id not in ids:
        raise IntegrityError('Development candidate must be in official Hard51')
    path = task_path(root, task_id)
    status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all', '--',
                             'v2/tasks/' + task_id, 'v2/hard51_ids.txt'], cwd=root,
                             check=True, capture_output=True, text=True)
    if status.stdout:
        raise IntegrityError('Pinned source files have local changes')
    configuration = tomllib.loads((path / 'task.toml').read_text())
    image = configuration['environment']['docker_image']
    if image != 'ghcr.io/scaleapi/swe-bench_pro-v2:' + task_id:
        raise IntegrityError('Unexpected official image registry/tag')
    files = ['instruction.md', 'task.toml', 'environment/Dockerfile', 'solution/gold_patch.diff',
             'tests/test.sh', 'tests/run_script.sh', 'tests/parser.py', 'tests/config.json', 'tests/test_patch.patch']
    digests = {}
    for name in files:
        file = path / name
        if not file.resolve().is_relative_to(path.resolve()) or file.is_symlink():
            raise IntegrityError('Unsafe benchmark file')
        digests[name] = sha(file.read_bytes())
    return {'task_id': task_id, 'source_repository': OFFICIAL_REPOSITORY, 'source_commit': revision,
            'split': 'Hard51', 'instruction': (path / 'instruction.md').read_text(),
            'image_tag': image, 'image_digest': None, 'base_commit': None,
            'official_difficulty_metadata': configuration['metadata']['difficulty'],
            'file_sha256': digests, 'local_gold_gate': 'not_run', 'local_empty_gate': 'not_run',
            'actor_execution': 'blocked_pending_environment_and_boundary_gates'}


def actor_task(manifest):
    """Never serialize a full row/manifest into an actor prompt."""
    return {key: manifest[key] for key in ('task_id', 'instruction')}


def launch_gate(manifest, checks):
    required = {'gold_passes', 'empty_fails', 'regressions_valid', 'sandbox_isolated',
                'source_history_sanitized', 'hidden_resources_absent', 'review_bridge_ready',
                'funding_ready', 'prompts_frozen'}
    if not required.issubset(checks) or not all(checks[key] is True for key in required):
        raise IntegrityError('Real benchmark execution gates incomplete')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', manifest.get('image_digest') or ''):
        raise IntegrityError('Immutable image digest required')
    if not re.fullmatch(r'[0-9a-f]{40}', manifest.get('base_commit') or ''):
        raise IntegrityError('Actual sanitized repository base commit required')
    return True


def write_development_manifest(source, destination):
    root = Path(source)
    # Selection rule fixed before model execution, without inspecting outcomes.
    chosen = sorted((root / 'v2/hard51_ids.txt').read_text().splitlines())[0]
    manifest = import_task(root, chosen)
    manifest['selection_rule'] = 'First lexicographic official Hard51 ID; development only, excluded from confirmatory tasks.'
    Path(destination).write_text(canonical(manifest) + '\n')
    return manifest
