#!/usr/bin/env python3
"""Read-only Study-I SVD reconstruction audit for SoK-DelegatedFL.

Run against the original local experiment directory. Does not alter the
repository, reproduce CKKS without inputs, or publish any run data.

Usage:
  python study1_reconstruction_audit.py --repo ~/experiment --run ~/experiment/results/raw/<I-OR-run-id>
  python study1_reconstruction_audit.py --self-test
"""
from __future__ import annotations
import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
import numpy as np

FEATURES = 12
UNKNOWN = 13


def read_records(path: Path):
    with path.open(newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    keys = [k for k in rows[0] if k.startswith('x_')]
    if len(keys) != FEATURES:
        raise ValueError(f'expected 12 feature columns; got {len(keys)}: {keys}')
    out = {}
    for p in sorted({r['partition'] for r in rows}):
        part = [r for r in rows if r['owner'] == 'O_1' and r['partition'] == p]
        X = np.array([[float(r[k]) for k in keys] for r in part], dtype=np.float64)
        y = np.array([float(r['target_heart_rate']) for r in part], dtype=np.float64)
        out[p] = {'X': X, 'y': y, 'ids': [r['record_id'] for r in part]}
    return out, keys


def build_A(models, R):
    # Exact design matrix from Go metrics.Reconstruct: [w_t, -1]
    return np.array([list(models[i]['w']) + [-1.0] for i in range(R)], dtype=np.float64)


def go_compatible_min_norm(A, Z):
    # Z shape [number of records, rounds]; Go's SVD cutoff is 13*smax*eps.
    U, s, Vt = np.linalg.svd(A, full_matrices=False)
    tol = max(A.shape) * s[0] * np.finfo(float).eps
    rank = int(np.sum(s > tol))
    coeff = (Z @ U[:, :rank]) / s[:rank]
    theta = coeff @ Vt[:rank, :]
    cond = float(s[0] / s[rank - 1]) if rank else math.inf
    return theta, dict(rank=rank, singular_values=s.tolist(), tolerance=tol, condition_number=cond)


def ridge(A, Z, rel_lambda):
    U, s, Vt = np.linalg.svd(A, full_matrices=False)
    lam = rel_lambda * s[0]**2
    return (Z @ U * (s / (s*s + lam))) @ Vt


def rel_l2(truth, pred):
    return float(np.linalg.norm(truth - pred) / np.linalg.norm(truth))


def features(X, Xhat, aux_X):
    sd = np.std(aux_X, axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    per_dim_rmse = np.sqrt(np.mean((X-Xhat)**2, axis=0))
    return dict(raw_relative_L2=rel_l2(X, Xhat),
                standardized_rmse=float(np.sqrt(np.mean(((X-Xhat)/sd)**2))),
                per_channel_rmse=per_dim_rmse.tolist(),
                per_channel_aux_std=sd.tolist())


def self_test():
    rng = np.random.default_rng(123)
    A = rng.normal(size=(5, UNKNOWN))
    th = (A.T @ rng.normal(size=5))  # true point IN the 5-dimensional row space
    est, diag = go_compatible_min_norm(A, (A @ th)[:,None].T)
    assert diag['rank'] == 5 and np.allclose(est[0], th, atol=1e-9)
    # Change vector only in the null-space: identical observations but distinct truth.
    U, s, Vt = np.linalg.svd(A, full_matrices=True)
    th2 = th + 10.0*Vt[-1]
    assert np.allclose(A @ th2, A @ th)
    print('SELF-TEST PASS: rank-5 recovery can be exact for row-space-aligned truth,')
    print('but nonunique: different 13-dimensional truths produce identical 5 observations.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, help='local clone of SoK-DelegatedFL')
    p.add_argument('--run', type=Path, help='I-OR results/raw/<run-id> directory')
    p.add_argument('--self-test', action='store_true')
    args=p.parse_args()
    if args.self_test:
        self_test(); return
    if args.repo is None or args.run is None:
        p.error('--repo and --run are required unless --self-test is set')
    repo = args.repo.expanduser().resolve()
    run = args.run.expanduser().resolve()
    rec_path = repo/'manifests'/'study1_records.csv'
    records, feature_names = read_records(rec_path)
    counts = {k:len(v['y']) for k,v in records.items()}
    print('Partition counts for O_1 (important manuscript-vs-code check):', counts)
    print('Published Go privacy/main.go: readRecords(..., "learning") for its target.')
    print('Thus this audit matches O_1 learning (NOT attacker_target).')
    X = records['learning']['X']
    y = records['learning']['y']
    aux = records['attacker_auxiliary']['X']
    true = np.column_stack((X, y))
    models=[]
    residuals=[]
    for r in range(1,11):
        mp=run/f'executor_view_model_round_{r:02d}.json'
        rp=run/f'executor_view_plaintext_residuals_round_{r:02d}.json'
        if not (mp.is_file() and rp.is_file()):
            raise FileNotFoundError(f'missing round-{r} model/residual: {mp}, {rp}')
        model=json.loads(mp.read_text())
        if len(model['w']) != FEATURES:
            raise ValueError(f'bad model dimension: {mp}')
        models.append(model)
        arr=np.array(json.loads(rp.read_text()),dtype=np.float64)
        if len(arr)!=len(X):
            raise ValueError(f'round {r}: residual count {len(arr)} != owner training count {len(X)}')
        residuals.append(arr)
    residuals=np.array(residuals).T
    print('Samples reconstructed by matching code:', len(X))
    print('Samples labeled attacker_target in O_1 manifest:', len(records['attacker_target']['y']))
    print('Reference zero-vector relative L2: 1.0')
    mean_baseline = np.broadcast_to(np.mean(aux,axis=0),(len(X),FEATURES))
    print('Auxiliary-feature-mean baseline relative L2:',round(rel_l2(X,mean_baseline),8))
    for R in [1,5,10]:
        A=build_A(models,R)
        b=np.array([m['b'] for m in models[:R]])
        z=residuals[:,:R]-b
        est,d=go_compatible_min_norm(A,z)
        Xhat=est[:,:FEATURES]
        fm=features(X,Xhat,aux)
        oracle_rhs=true @ A.T
        oracle,_=go_compatible_min_norm(A,oracle_rhs)
        # Inversion with exact noiseless observations exposes geometric projection error.
        oracle_feature=rel_l2(X,oracle[:,:FEATURES])
        noise=z-oracle_rhs
        print('\nR =',R,'rank =',d['rank'],'condition =',format(d['condition_number'],'.6g'))
        print('singular values:', [float(f'{v:.9g}') for v in d['singular_values']])
        print('raw_relative_L2 =',fm['raw_relative_L2'])
        print('standardized_RMSE =',fm['standardized_rmse'])
        print('oracle_noiseless_min_norm_feature_relL2 =',oracle_feature)
        print('round residual/linear equation discrepancy RMSE =',float(np.sqrt(np.mean(noise**2))))
        print('SVD pseudoinverse solver vs clean projection difference relL2 =',rel_l2(est,oracle))
        print('Ridge (predeclared lambdas, NOT target-selected):')
        for lam in (1e-12,1e-10,1e-8,1e-6,1e-4):
            xr=ridge(A,z,lam)[:,:FEATURES]
            print(f'  lambda_rel={lam:g}: relL2={rel_l2(X,xr):.9g}')
        if R==5:
            print('Per-channel checks (raw and auxiliary-std-normalized):')
            for name,err,sd in zip(feature_names,fm['per_channel_rmse'],fm['per_channel_aux_std']):
                print(f'  {name:30s} rawRMSE={err:.8g}, aux_sd={sd:.8g}, normalizedRMSE={err/sd:.8g}')
    print('\nIMPORTANT: Does not validate manuscript numbers unless the exact immutable I-OR runs')
    print('and matching code/configuration used for the submitted version are supplied.')
    print('Nonzero initialization requires a NEW matched run; cannot infer results from old logs.')

if __name__ == '__main__': main()
