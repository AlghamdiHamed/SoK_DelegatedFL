"""Correct post-exit cap verification using preserved in-service evidence."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(row, root=ROOT):
    if row.get('status') != 'cap_not_verified':
        return row, None
    raw = root/'results/raw'/row['run_id']
    limit = int(row['limit_bytes'])
    names = ['cap.before', 'swap.before', 'app.exit', 'budget_result.json']
    assert (raw/'cap.before').read_text().strip() == str(limit), 'Captured memory limit differs'
    assert (raw/'swap.before').read_text().strip() == '0', 'Captured swap limit differs'
    assert (raw/'app.exit').read_text().strip() == '0', 'Application did not exit successfully'
    assert row.get('error') is None and row.get('owner_returncode') == 0, 'Unsuccessful trial'
    assert row.get('completed_rounds') == 10, 'Incomplete owner rounds'
    models = sorted(raw.glob('global_model_round_*'))
    assert {int(p.stem.rsplit('_', 1)[1]) for p in models} == set(range(1, 11)), 'Incomplete model sequence'
    assert len(models) == 10 and all(p.stat().st_size > 0 for p in models), 'Invalid model artifacts'
    names += [p.name for p in models]
    if row['study'] == 'study2':
        name = 'measurement_O_1_finished.json'
        worker = json.loads((raw/name).read_text())
        assert worker['status'] == 'ok', 'Worker did not finish'
        assert worker['cgroup_start']['memory.max'] == str(limit), 'Worker limit differs'
        assert worker['cgroup_start']['memory.swap.max'] == '0', 'Worker swap differs'
        names.append(name)
    else:
        assert (raw/'resources_O_1.json').is_file(), 'Missing owner completion metrics'
        names.append('resources_O_1.json')
    corrected = dict(row, status='feasible', cap_confirmed=True)
    evidence = {
        'run_id': row['run_id'],
        'previous_status': row['status'],
        'corrected_status': 'feasible',
        'reason': 'Post-exit systemd properties were defaults; in-service captures verify enforcement.',
        'expected_limit_bytes': limit,
        'raw_files_unchanged': True,
        'evidence_sha256': {
            name: hashlib.sha256((raw/name).read_bytes()).hexdigest()
            for name in names
        },
        'analysis_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    }
    return corrected, evidence


def record(row, campaign):
    corrected, evidence = verify(row)
    if evidence:
        audit = campaign/('cap-verification-'+row['run_id']+'.json')
        if not audit.exists():
            with audit.open('x') as f:
                json.dump(evidence, f, indent=2)
        print('Verified completed capped run:', row['run_id'], flush=True)
    return corrected


def main():
    campaign = Path(sys.argv[1]).resolve()
    assert (campaign/'idle.json').is_file(), 'Use the existing calibrated campaign directory'
    spec = importlib.util.spec_from_file_location(
        'budget', ROOT/'scripts/feasibility_budget.py')
    budget = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(budget)

    for path in sorted(campaign.glob('I*-k*-b*.json')):
        row = json.loads(path.read_text())
        corrected = record(row, campaign)
        if corrected != row:
            budget.save(path, corrected)

    original_trial = budget.Campaign.trial

    def verified_trial(self, *args, **kwargs):
        return record(original_trial(self, *args, **kwargs), self.out)

    budget.Campaign.trial = verified_trial
    sys.argv = [str(ROOT/'scripts/feasibility_budget.py'), 'summary', str(campaign)]
    budget.main()
    sys.argv = [str(ROOT/'scripts/feasibility_budget.py'), 'run', str(campaign)]
    budget.main()


if __name__ == '__main__':
    main()
