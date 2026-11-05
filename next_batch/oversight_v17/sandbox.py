"""Local Docker command policy. Actual isolation still requires runtime probes."""
import os
import re
import shutil
import subprocess

from .events import IntegrityError

DOCKER = ['docker', '--host=unix:///var/run/docker.sock']


def host_environment():
    env = os.environ.copy()
    for name in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:
        env.pop(name, None)
    return env


def container_command(image_digest, name, *, memory_mb=4096):
    if not re.fullmatch(r'ghcr.io/scaleapi/swe-bench_pro-v2@sha256:[0-9a-f]{64}', image_digest):
        raise IntegrityError('Immutable official image reference required')
    if not re.fullmatch(r'v17-[a-z0-9-]{1,80}', name):
        raise IntegrityError('Unsafe container name')
    return DOCKER + ['create', '--name', name, '--network', 'none', '--cpus', '1',
        '--memory', str(memory_mb)+'m', '--memory-swap', str(memory_mb)+'m', '--pids-limit', '256',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
        '--env', 'OPENROUTER_API_KEY=', '--env', 'HTTP_PROXY=', '--env', 'HTTPS_PROXY=',
        '--env', 'ALL_PROXY=', '--env', 'http_proxy=', '--env', 'https_proxy=',
        '--entrypoint', '/bin/sleep', image_digest, 'infinity']


def actor_command(name, command, workspace='/app', seconds=60):
    if not re.fullmatch(r'v17-[a-z0-9-]{1,80}', name) or workspace not in {'/app','/testbed'}:
        raise IntegrityError('Explicit isolated actor container required')
    if not isinstance(command,str) or len(command.encode()) > 16000 or type(seconds) is not int or not 1 <= seconds <= 120:
        raise IntegrityError('Bounded actor command required')
    # The shell interprets model text only inside the prepared nonroot container.
    return DOCKER + ['exec', '--user', '10001:10001', '--workdir', workspace, name,
        '/usr/bin/env', '-i', 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'HOME=/tmp/actor-home', 'LANG=C.UTF-8',
        '/usr/bin/timeout', '--kill-after=2s', str(seconds)+'s',
        '/bin/bash', '--noprofile', '--norc', '-c', command]


def capacity_probe(path='/workspace'):
    info = subprocess.run(DOCKER + ['info','--format','{{json .Driver}}'], check=True,
                          capture_output=True, text=True, env=host_environment(), timeout=20)
    disk = shutil.disk_usage(path)
    # Official task declares 10GiB storage before accounting for VFS layer copies.
    minimum = 12 * 1024**3
    return {'docker_driver': info.stdout.strip().strip('"'), 'free_bytes': disk.free,
            'minimum_free_bytes': minimum, 'capacity_gate': disk.free >= minimum,
            'meaning': 'Conservative pre-pull gate, not measured peak usage or a runtime isolation proof.'}
