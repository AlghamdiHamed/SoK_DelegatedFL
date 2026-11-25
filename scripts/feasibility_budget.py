#!/usr/bin/env python3
"""Added rebuttal measurement: original roles/protocol, 42 width/budget cases."""
import argparse
import csv
import hashlib
import json
import os
import shlex
import signal
import statistics
import subprocess as sp
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIB = 1048576
SSH = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10']
ENV = {k:'1' for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS','GOMAXPROCS')}
REMOTE = '''set -u
root=$1; out=$2; role=$3; limit=$4; shift 4
cd "$root" || exit 90
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 GOMAXPROCS=1
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
echo "$BASHPID" > "$out/shell_$role.pid"
unit="dfl-budget-$(basename "$out")"
child=""; sampler=""
cleanup() {
 if [[ $limit != none ]]; then systemctl --user stop "$unit" >/dev/null 2>&1 || true
 elif [[ -n $child ]]; then kill -TERM "$child" 2>/dev/null || true; fi
 touch "$out/sample.stop"
}
trap 'cleanup; exit 143' TERM INT HUP
if [[ $limit != none ]]; then
 "$root/.venv/bin/python" "$root/scripts/feasibility_budget.py" sample "$out" & sampler=$!
 systemd-run --user --quiet --wait --pipe --unit="$unit" \
  --property="MemoryMax=$limit" --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=OMP_NUM_THREADS=1 --setenv=MKL_NUM_THREADS=1 --setenv=OPENBLAS_NUM_THREADS=1 \
  --setenv=NUMEXPR_NUM_THREADS=1 --setenv=GOMAXPROCS=1 \
  -- /bin/bash "$root/scripts/feasibility_scope.sh" "$out" "$@" & child=$!
else
 "$@" & child=$!
fi
wait "$child"; rc=$?
if [[ $limit != none ]]; then
 touch "$out/sample.stop"; wait "$sampler" || true
 systemctl --user show "$unit" --property=Result --property=MemoryMax \
  --property=MemorySwapMax --property=MemoryPeak --property=ExecMainStatus \
  > "$out/systemd.txt" 2>&1 || true
 systemctl --user reset-failed "$unit" >/dev/null 2>&1 || true
fi
exit "$rc"
'''


def save(path, value):
    temporary=Path(str(path)+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    temporary.replace(path)


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def number(path):
    try: return int(path.read_text().strip())
    except (OSError,ValueError): return None


def sample(out):
    peak=charged=0
    with (out/'rss_trace.csv').open('x') as f:
        w=csv.writer(f); w.writerow(['monotonic_ns','rss_bytes','hwm_bytes','cgroup_peak_bytes'])
        while not (out/'sample.stop').exists():
            try:
                pid=(out/'app.pid').read_text().strip()
                fields=dict(line.split(':',1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
                rss=int(fields['VmRSS'].split()[0])*1024
                hwm=int(fields['VmHWM'].split()[0])*1024
                cg=Path((out/'cgroup.path').read_text().strip())
                charge=number(cg/'memory.peak') or 0
                peak=max(peak,hwm); charged=max(charged,charge)
                w.writerow([time.monotonic_ns(),rss,hwm,charge])
            except (OSError,KeyError,ValueError): pass
            time.sleep(.02)
    save(out/'sample.json',{'sampled_peak_rss_bytes_lower_bound':peak or None,
                           'sampled_cgroup_peak_bytes':charged or None,'period_seconds':.02})


def grid(config):
    rows=[]
    for k in config['widths_descending']:
        for c in config['study2_configurations']:
            for b in config['additional_budgets_mib']:
                p=10240*k*k+5216*k+12
                g=p if c=='II-L' else 5120*k+12 if c=='II-U-E' else 8192*k*k+5184*k+12
                floor=4*(3*p+g)
                rows.append(dict(study='study2',configuration=c,width=k,budget_mib=b,
                    logical_parameters=p,predicted_increment_bytes=floor,
                    predicted_fit=floor<=b*MIB,prediction='idle + retained input/state/model weights + trained gradients; temporaries omitted'))
    for c in config['study1_configurations']:
        for b in config['additional_budgets_mib']:
            rows.append(dict(study='study1',configuration=c,width=1,budget_mib=b,
                logical_parameters=13,predicted_increment_bytes=None,predicted_fit=None,
                prediction='no Go/CKKS allocation predictor; fixed-workload measured boundary'))
    return rows


def classify(rc, props, limit, rounds, models, reason):
    confirmed=(props.get('MemoryMax')==str(limit) and props.get('MemorySwapMax')=='0')
    if reason: return reason,confirmed
    if not confirmed: return 'cap_not_verified',False
    if props.get('Result')=='oom-kill': return 'infeasible_oom',True
    if rc==0 and rounds==10 and models==10: return 'feasible',True
    return 'failed_other',True


class Campaign:
    def __init__(self,a):
        self.a=a
        self.hosts={'O_1':a.pi1,'O_2':a.pi2,'O_3':a.pi3,'A':a.aggregator}
        self.homes=({r:self.ssh(h,'printf %s "$HOME"').stdout.strip() for r,h in self.hosts.items()}
                    if a.command!='summary' else {})
        self.roots={r:Path(h)/'experiment' for r,h in self.homes.items()}
        self.roots['E']=ROOT
        self.out=a.output.resolve(); self.out.mkdir(parents=True,exist_ok=True)
        self.config=json.loads((ROOT/'configs/feasibility_budget.json').read_text())
        if self.config['rounds']!=10 or self.config['target_owner']!='O_1':
            raise RuntimeError('this measurement preserves the original ten rounds and targets O_1')
        if a.command!='summary':self.check_sources()

    def check_sources(self):
        paths=sp.check_output(['git','-C',str(ROOT),'ls-files','study1','study2','manifests',
            'configs/study1.yaml','configs/study2.yaml'],text=True).splitlines()
        paths+=['scripts/measure_existing_study2.py','scripts/feasibility_budget.py',
                'scripts/feasibility_scope.sh','configs/feasibility_budget.json']
        local={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in paths}
        for role,host in self.hosts.items():
            code=('import hashlib,json; from pathlib import Path; root=Path.home()/"experiment"; '
                  'print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in '+repr(paths)+'}))')
            remote=json.loads(self.ssh(host,shlex.join(['python3','-c',code])).stdout)
            if remote!=local:raise RuntimeError(f'{role}: source/manifest hashes differ; copy the same measurement files and experiment sources before running')
            binary='aggregator' if role=='A' else 'owner'
            root=self.roots[role]
            required=[root/'.venv/bin/python',root/'study1/bin'/binary]
            checks=' && '.join('test -x '+shlex.quote(str(p)) for p in required)
            self.ssh(host,checks)
        if not (ROOT/'study1/bin/delegate').is_file():raise RuntimeError('E Study-I delegate binary is missing')
        key=Path(self.homes['A'])/'dfl-secure/study1-final-optiplex-keys'
        self.ssh(self.hosts['A'],' && '.join('test -s '+shlex.quote(str(key/name))
                 for name in ('secret.key','public.key','relinearization.key','rotation.keys','parameters.bin')))
        snapshot={'hashes':local,'configuration':self.config,'hosts':self.hosts,
                  'executor_ip':self.a.executor_ip,'aggregator_ip':self.a.aggregator_ip}
        path=self.out/'source_snapshot.json'
        if path.exists() and read(path)!=snapshot:
            raise RuntimeError('campaign sources/configuration/hosts changed; use a new campaign directory')
        if not path.exists():save(path,snapshot)
        hardware={}
        for role,host in self.hosts.items():
            code=('import json,platform;from pathlib import Path; '
                  'p=Path("/proc/device-tree/model"); '
                  'print(json.dumps({"hostname":platform.node(),"architecture":platform.machine(),'
                  '"platform":platform.platform(),"model":p.read_text().strip(chr(0)) if p.exists() else None,'
                  '"meminfo":Path("/proc/meminfo").read_text()}))')
            hardware[role]=json.loads(self.ssh(host,shlex.join(['python3','-c',code])).stdout)
        save(self.out/('hardware-'+uuid.uuid4().hex[:10]+'.json'),hardware)

    def ssh(self,host,cmd,**kw):
        return sp.run([*SSH,host,cmd],capture_output=True,text=True,check=True,timeout=30,**kw)

    def fetch(self,role,remote,local):
        sp.run(['rsync','-a','--ignore-existing','--exclude=run.json',
            f'{self.hosts[role]}:{remote}/',str(local)+'/'],check=True)

    def remote(self,role,out,command,limit=None,log=None):
        root=self.roots[role]
        inv=['bash','-s','--',str(root),str(out),role,str(limit) if limit is not None else 'none',*map(str,command)]
        f=Path(log).open('x')
        p=sp.Popen([*SSH,self.hosts[role],shlex.join(inv)],stdin=sp.PIPE,stdout=f,stderr=sp.STDOUT,text=True)
        p.stdin.write(REMOTE); p.stdin.close()
        return p,f

    def stop(self,role,p,remote):
        if p.poll() is not None:return
        try:
            if role=='E':p.terminate()
            else:
                pid=shlex.quote(str(remote/f'shell_{role}.pid'))
                self.ssh(self.hosts[role],f'test ! -s {pid} || kill -TERM "$(cat {pid})"')
            p.wait(timeout=20)
        except (sp.SubprocessError,OSError):p.kill(); p.wait()

    def idle(self):
        if (self.out/'idle.json').exists():raise RuntimeError('idle baseline is already frozen; reuse it or select a new campaign directory')
        target=self.roots['O_1']
        baselines={}
        for study in ('study1','study2'):
            values=[]
            for rep in range(self.config['idle_repetitions']):
                name=f'idle-{study}-{rep}-{uuid.uuid4().hex[:10]}'
                remote=target/'results/feasibility'/name
                local=self.out/name; local.mkdir()
                self.ssh(self.hosts['O_1'],'mkdir -p '+shlex.quote(str(remote)))
                cmd=([target/'study1/bin/owner','--help'] if study=='study1' else
                     [target/'.venv/bin/python',target/'scripts/measure_existing_study2.py','--idle','--idle-output',remote/'idle.json'])
                p,f=self.remote('O_1',remote,cmd,'infinity',local/'process.log')
                try: rc=p.wait(timeout=120)
                except sp.TimeoutExpired:
                    self.stop('O_1',p,remote); raise RuntimeError('idle probe timed out')
                finally:f.close()
                self.fetch('O_1',remote,local)
                peak=number(local/'cgroup.peak')
                if rc or not peak or number(local/'swap.before')!=0:
                    raise RuntimeError(f'idle calibration failed; inspect {local}')
                values.append(peak)
            baseline=int(statistics.median(values))
            baselines[study]={'idle_bytes':baseline,'samples_bytes':values,
                'definition':self.config['baseline_definition'],'kind':'measured idle-startup peak, not total device RAM'}
        save(self.out/'idle.json',baselines)
        print(json.dumps(baselines,indent=2))

    def trial(self,case,baseline):
        cfg=case['configuration']; study=case['study']; width=case['width']
        seed=str(self.config['seed']); limit=baseline['idle_bytes']+case['budget_mib']*MIB
        rid=sp.check_output([sys.executable,str(ROOT/'scripts/new_run.py'),'--root',str(ROOT),
            '--study',study,'--configuration',cfg,'--seed',seed],text=True).strip()
        run=ROOT/'results/raw'/rid
        meta={**case,'run_id':rid,'idle_bytes':baseline['idle_bytes'],'limit_bytes':limit,'seed':int(seed),'energy_joules':None}
        save(run/'budget_case.json',meta)
        for role,host in self.hosts.items():
            sp.run(['scp',str(run/'run.json'),f'{host}:/tmp/{rid}.json'],check=True,stdout=sp.DEVNULL)
            self.ssh(host,'cd "$HOME/experiment" && python3 scripts/join_run.py --root . --run-json '+shlex.quote(f'/tmp/{rid}.json'))
        running=[]; files=[]
        def launch(role,kind,options):
            root=self.roots[role]; directory=root/'results/raw'/rid
            if study=='study2':
                cmd=[root/'.venv/bin/python',root/'scripts/measure_existing_study2.py','--width',str(width),
                     '--module',f'study2.{kind}.main',*options,'--run-dir',str(directory)]
            else:cmd=[root/'study1/bin'/kind,*options,'--run-dir',str(directory),'--run-id',rid]
            cmd=list(map(str,cmd))
            log=run/f'budget_process_{role}.log'
            if role=='E':
                f=log.open('x'); p=sp.Popen(cmd,cwd=ROOT,stdout=f,stderr=sp.STDOUT,env=dict(os.environ,**ENV))
            else:p,f=self.remote(role,directory,cmd,limit if role=='O_1' else None,log)
            running.append((role,p,directory)); files.append(f)
            return p
        def listener(role,port,p):
            query=f"ss -H -ltn '( sport = :{port} )'"
            for _ in range(600):
                if p.poll() is not None:raise RuntimeError(f'{role} failed before listening')
                out=(self.ssh(self.hosts[role],query).stdout if role!='E'
                     else sp.check_output(shlex.split(query),text=True))
                if out.strip():return
                time.sleep(.2)
            raise TimeoutError('listener startup')
        reason=None; rc=None
        try:
            if self.ssh(self.a.aggregator,"ss -H -ltn '( sport = :7002 )'").stdout.strip():
                raise RuntimeError('A port 7002 already occupied')
            if sp.check_output(['ss','-H','-ltn','( sport = :7001 )'],text=True).strip():
                raise RuntimeError('E port 7001 already occupied')
            aggopts=['--listen','0.0.0.0:7002']
            if study=='study2':aggopts+=['--seed',seed]
            else:
                aggopts+=['--configuration',cfg]
                if cfg=='I-ER':aggopts+=['--key-state',str(Path(self.homes['A'])/'dfl-secure/study1-final-optiplex-keys')]
            agg=launch('A','aggregator',aggopts); listener('A',7002,agg)
            delegated=cfg not in ('I-L','II-L')
            if delegated:
                opts=['--listen','0.0.0.0:7001']
                if study=='study1':opts+=['--configuration',cfg,'--aggregator',self.a.aggregator_ip+':7002']
                exe=launch('E','delegate',opts); listener('E',7001,exe)
            owners={}
            for role in ('O_2','O_3','O_1'):
                opts=['--owner',role,'--configuration',cfg,'--records',str(self.roots[role]/'manifests'/f'{study}_{role}_records.csv'),
                      '--executor',self.a.executor_ip+':7001','--aggregator',self.a.aggregator_ip+':7002','--task-id',rid]
                if study=='study2':opts+=['--seed',seed,'--work-mode','honest']
                owners[role]=launch(role,'owner',opts)
            deadline=time.monotonic()+self.config['trial_timeout_seconds']
            while time.monotonic()<deadline:
                rc=owners['O_1'].poll()
                if any(owners[r].poll() not in (None,0) for r in ('O_2','O_3')):
                    raise RuntimeError('uncapped peer owner failed; not O_1 budget evidence')
                if rc is not None and rc!=0:break
                if rc==0 and all(p.poll()==0 for p in owners.values()):break
                if agg.poll() not in (None,0):raise RuntimeError('aggregator failed')
                time.sleep(.1)
            else:reason='infeasible_timeout'
        except Exception as exc:reason='infrastructure_error: '+repr(exc)
        finally:
            for role,p,directory in reversed(running):self.stop(role,p,directory)
            for f in files:f.close()
            for role in self.hosts:self.fetch(role,self.roots[role]/'results/raw'/rid,run)
        props={}
        if (run/'systemd.txt').exists():
            props=dict(x.split('=',1) for x in (run/'systemd.txt').read_text().splitlines() if '=' in x)
        # Successful transient services may already be unloaded: the in-service
        # startup capture independently verifies both effective limit properties.
        props.setdefault('MemoryMax',str(number(run/'cap.before')))
        props.setdefault('MemorySwapMax',str(number(run/'swap.before')))
        suffix='jsonl' if study=='study2' else 'csv'
        roundsfile=run/f'owner_O_1_rounds.{suffix}'
        rounds=(json_rows(roundsfile) if suffix=='jsonl' else
                list(csv.DictReader(roundsfile.open()))) if roundsfile.exists() else []
        rounds=[r for r in rounds if str(r.get('round_id','')).isdigit()]
        status,confirmed=classify(rc,props,limit,len(rounds),len(list(run.glob('global_model_round_*'))),reason)
        metrics=read(run/'resources_O_1.json',{})
        sampled=read(run/'sample.json',{})
        charged=number(run/'cgroup.peak') or (int(props['MemoryPeak']) if props.get('MemoryPeak','').isdigit() else None) or sampled.get('sampled_cgroup_peak_bytes')
        meta.update(status=status,cap_confirmed=confirmed,systemd=props,owner_returncode=rc,
            completed_rounds=len(rounds),peak_rss_bytes=metrics.get('os_peak_rss_bytes'),
            sampled_peak_rss_bytes_lower_bound=sampled.get('sampled_peak_rss_bytes_lower_bound'),
            cgroup_peak_bytes=charged,error=reason)
        traffic={}
        logfile=run/'messages_O_1.jsonl'
        if logfile.exists():
            for row in json_rows(logfile):
                r=int(row['round_id']); direction=row['direction']
                traffic.setdefault(r,{'tx_bytes':0,'rx_bytes':0})['tx_bytes' if direction=='sent' else 'rx_bytes']+=int(row['socket_bytes'])
        aggrows=(json_rows(run/'aggregator_rounds.jsonl') if study=='study2' else
                 [read(p) for p in sorted(run.glob('aggregator_round_*_timing.json'))])
        per_round=[]
        for r,counts in sorted(traffic.items()):
            row={'run_id':rid,'round_id':r,'is_setup':r==0,**counts}
            size=counts['tx_bytes']+counts['rx_bytes']
            row.update(ideal_transfer_seconds_1mbit=8*size/1e6,ideal_transfer_seconds_10mbit=8*size/1e7)
            matched=next((v for v in rounds if int(v['round_id'])==r),None)
            row['owner_protocol_online_ns']=matched.get('owner_protocol_online_ns') if matched else None
            timing=next((v for v in aggrows if int(v['round_id'])==r),None)
            row['end_to_end_round_ns']=timing.get('end_to_end_round_ns') if timing else None
            row['complete_training_round']=bool(matched and timing and r>0)
            row['traffic_is_partial']=bool(r>0 and not row['complete_training_round'])
            per_round.append(row)
        write_csv(run/'budget_rounds.csv',per_round)
        meta['per_round']=per_round
        completed=[r for r in per_round if r['round_id']>0 and r['end_to_end_round_ns'] is not None]
        meta['median_round_latency_ns']=statistics.median(int(r['end_to_end_round_ns']) for r in completed) if completed else None
        meta['mean_bytes_per_completed_round']=statistics.mean(r['tx_bytes']+r['rx_bytes'] for r in completed) if completed else None
        meta['setup_bytes']=sum(r['tx_bytes']+r['rx_bytes'] for r in per_round if r['round_id']==0)
        save(run/'budget_result.json',meta)
        return meta

    def run(self):
        idle=read(self.out/'idle.json')
        if not idle:raise RuntimeError('run idle calibration first, using the same output directory')
        cases=grid(self.config)
        assert len(cases)==42
        predicted=self.out/'predictions.json'
        if not predicted.exists():save(predicted,cases)
        elif read(predicted)!=cases:raise RuntimeError('frozen prediction grid differs; use a new campaign directory')
        for case in cases:
            name=f"{case['configuration']}-k{case['width']}-b{case['budget_mib']}"
            path=self.out/(name+'.json')
            previous=read(path,{})
            if previous.get('status') in ('feasible','infeasible_oom','infeasible_timeout'):continue
            result=self.trial(case,idle[case['study']]); save(path,result)
            self.summary()
            print(name,result['status'],flush=True)
            if result['status'] not in ('feasible','infeasible_oom','infeasible_timeout'):
                raise RuntimeError(f"trial needs inspection: {path}; fix then resume the same campaign")

    def summary(self):
        rows=[]
        for case in grid(self.config):
            path=self.out/f"{case['configuration']}-k{case['width']}-b{case['budget_mib']}.json"
            r=read(path)
            if not r:continue
            rows.append({k:v for k,v in r.items() if k not in ('per_round','systemd')})
        write_csv(self.out/'feasibility_grid.csv',rows)
        crossings=[]
        for method in self.config['study2_configurations']:
            for budget in self.config['additional_budgets_mib']:
                planned=[c for c in grid(self.config) if c['configuration']==method and c['budget_mib']==budget]
                measured=[r for r in rows if r['configuration']==method and r['budget_mib']==budget]
                fits=sorted(r['width'] for r in measured if r['status']=='feasible')
                fails=sorted(r['width'] for r in measured if r['status'] in ('infeasible_oom','infeasible_timeout'))
                crossings.append({'configuration':method,'budget_mib':budget,
                    'predicted_largest_fitting_tested_width':max((c['width'] for c in planned if c['predicted_fit']),default=None),
                    'measured_largest_fitting_tested_width':max(fits,default=None),
                    'measured_smallest_failing_tested_width':min(fails,default=None),
                    'all_widths_measured':len(measured)==len(planned) and all(r['status'] in ('feasible','infeasible_oom','infeasible_timeout') for r in measured),
                    'observed_monotone':not fits or not fails or max(fits)<min(fails),
                    'failure_causes':';'.join(sorted(set(r['status'] for r in measured if r['status']!='feasible')))})
        write_csv(self.out/'crossovers.csv',crossings)
        lines=['# Feasibility budget grid','','One Pi 4 target owner; seed 101; idle-relative cgroup limits; ten original rounds.',
               'Predictions are a structural live-tensor model, not guarantees; temporary copies/allocator/cache effects can raise or lower measured charged usage.',
               'Transfer times are ideal serialization estimates, not measured 1/10 Mbit/s experiments. No energy was measured.','',
               '| Method | Width | Extra MiB | Total cap MiB | Predicted fit | Measured outcome | Peak RSS MiB | Charged peak MiB |',
               '|---|---:|---:|---:|---|---|---:|---:|']
        for r in rows:
            fmt=lambda value:f'{value/MIB:.2f}' if value is not None else 'NA'
            predicted='not modelled' if r['predicted_fit'] is None else str(r['predicted_fit'])
            lines.append(f"| {r['configuration']} | {r['width']} | {r['budget_mib']} | {r['limit_bytes']/MIB:.2f} | {predicted} | {r['status']} | {fmt(r['peak_rss_bytes'])} | {fmt(r['cgroup_peak_bytes'])} |")
        (self.out/'summary.md').write_text('\n'.join(lines)+'\n')


def write_csv(path,rows):
    if not rows:return
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader();w.writerows(rows)


def json_rows(path):
    if not path.exists():return []
    lines=path.read_text().splitlines(); rows=[]
    for i,line in enumerate(lines):
        try:rows.append(json.loads(line))
        except json.JSONDecodeError:
            if i!=len(lines)-1:raise
            # Preserve the raw trace; a killed writer can leave a partial final line.
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('idle','run','summary','sample'))
    p.add_argument('output',type=Path)
    p.add_argument('--pi1',default='pi1@192.168.0.146')
    p.add_argument('--pi2',default='pi2@192.168.0.66')
    p.add_argument('--pi3',default='pi3@192.168.0.118')
    p.add_argument('--aggregator',default='ubuntu@192.168.0.49')
    p.add_argument('--executor-ip',default='192.168.0.57')
    p.add_argument('--aggregator-ip',default='192.168.0.49')
    a=p.parse_args()
    if a.command=='sample':sample(a.output);return
    campaign=Campaign(a)
    getattr(campaign,a.command)()


if __name__=='__main__':main()
