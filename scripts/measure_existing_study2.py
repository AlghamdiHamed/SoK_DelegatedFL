#!/usr/bin/env python3
"""Measure original Study-II entry points, optionally widening the workload.

No original source, model placement, training, protocol or protection is changed.
At width one even the model-construction namespace is left untouched. At larger
widths only the four Linear layer dimensions are supplied by this wrapper.
"""
import argparse
import hashlib
import json
import os
import platform
import resource
import runpy
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"


def cgroup():
    try:
        line = next(s for s in Path('/proc/self/cgroup').read_text().splitlines() if s.startswith('0::'))
        root = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
        return {name: (root/name).read_text().strip() for name in
                ('memory.max', 'memory.swap.max', 'memory.peak', 'memory.events') if (root/name).exists()}
    except (OSError, StopIteration):
        return {}


def widen(width):
    from study2.common import models
    if width != 1:
        original_nn = models.nn
        shapes = {(36, 128): (36, 128*width), (128, 64): (128*width, 64*width),
                  (64, 32): (64*width, 32*width), (32, 12): (32*width, 12)}
        def linear(inputs, outputs, *args, **kwargs):
            return original_nn.Linear(*shapes[(inputs, outputs)], *args, **kwargs)
        # Rebind only this source module's construction namespace, not torch.nn.
        models.nn = SimpleNamespace(Module=original_nn.Module, Linear=linear)
    return models


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--width', type=int, default=1)
    p.add_argument('--idle', action='store_true')
    p.add_argument('--idle-output', type=Path)
    p.add_argument('--module', default='study2.owner.main', choices=(
        'study2.owner.main', 'study2.delegate.main', 'study2.aggregator.main'))
    a, forwarded = p.parse_known_args()
    if a.idle:
        # Same runtime/optimizer baseline for all widths and all three methods.
        import study2.owner.main
        import torch
        import numpy
        torch.optim.SGD([torch.nn.Parameter(torch.empty(0))], lr=.02)
        info = {'definition': 'runtime and SGD initialized, no records/model/keys',
                'torch': torch.__version__, 'numpy': numpy.__version__, 'python': sys.version,
                'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                'cgroup': cgroup()}
        with a.idle_output.open('x') as f:
            json.dump(info, f, indent=2)
        return
    if forwarded and forwarded[0] == '--':
        forwarded = forwarded[1:]
    if not 1 <= a.width <= 128:
        p.error('width must be 1..128')
    run = Path(forwarded[forwarded.index('--run-dir')+1])
    role = ('A' if '.aggregator.' in a.module else 'E' if '.delegate.' in a.module
            else forwarded[forwarded.index('--owner')+1])
    start = time.perf_counter()
    originals = [ROOT/'study2/common/models.py', ROOT/a.module.replace('.', '/').replace('/main', '/main.py'),
                 ROOT/'study2/common/wire.py', ROOT/'study2/common/auth.py', ROOT/'study2/common/data.py']
    info = {'role': role, 'width': a.width, 'module': a.module, 'argv': forwarded,
            'hostname': platform.node(), 'architecture': platform.machine(),
            'pid': os.getpid(), 'cgroup_start': cgroup(),
            'original_source_sha256': {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
                                       for f in originals}, 'energy_joules': None}
    with (run/f'measurement_{role}_started.json').open('x') as f:
        json.dump(info, f, indent=2)
    status = 'error'
    try:
        widen(a.width)
        import torch
        import numpy
        info['torch'] = torch.__version__
        info['numpy'] = numpy.__version__
        info['python'] = sys.version
        sys.argv = [a.module, *forwarded]
        runpy.run_module(a.module, run_name='__main__')
        status = 'ok'
    finally:
        info.update(status=status, peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                    process_cpu_seconds=time.process_time(), wall_seconds=time.perf_counter()-start,
                    cgroup_end=cgroup())
        with (run/f'measurement_{role}_finished.json').open('x') as f:
            json.dump(info, f, indent=2)


if __name__ == '__main__':
    main()
