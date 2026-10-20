#!/usr/bin/env bash
# Signal this run only; retain enough identity to recover its Docker sidecars.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
"$PYTHON" - "$BLOCK_DIR" "$@" <<'PY'
import argparse, json, os, re, shutil, signal, subprocess, sys, time
from pathlib import Path

parser=argparse.ArgumentParser(description='Stop this block run and recover its owned subprocesses and containers.')
parser.add_argument('block'); parser.add_argument('--pid',type=int); parser.add_argument('--run-id')
args=parser.parse_args(); block=Path(args.block); artifacts=block/'artifacts'
pid_file=artifacts/'run.pid'; process_file=artifacts/'run.process.json'
try:
    metadata=json.loads(process_file.read_text()) if process_file.is_file() else {}
except (OSError,ValueError):
    raise SystemExit('ERROR: invalid process identity; inspect the local run record')
if not isinstance(metadata,dict): raise SystemExit('ERROR: invalid process identity')
recorded=None
if pid_file.exists():
    raw=pid_file.read_text().strip()
    if not raw.isdigit(): raise SystemExit('ERROR: invalid PID record')
    recorded=int(raw)
if args.pid is not None and recorded is not None and args.pid!=recorded:
    raise SystemExit('ERROR: another run owns the PID record; refusing to signal')
pid=args.pid if args.pid is not None else recorded if recorded is not None else metadata.get('pid')
if pid is None:
    print('No terminal-curator process recorded.'); raise SystemExit(0)
if type(pid) is not int or pid<=1: raise SystemExit('ERROR: invalid PID record')
if metadata and metadata.get('pid')!=pid:
    raise SystemExit('ERROR: another run owns the process identity; refusing to signal')
run_id=metadata.get('run_id') or args.run_id
if run_id is not None and (not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',run_id)):
    raise SystemExit('ERROR: invalid run identity')
if args.run_id is not None and run_id!=args.run_id:
    raise SystemExit('ERROR: run identity changed; refusing to signal')

def identity(target):
    try:
        fields=Path(f'/proc/{target}/stat').read_text().rsplit(')',1)[1].split()
        return None if fields[0]=='Z' else fields[19]
    except (FileNotFoundError,ProcessLookupError,IndexError): return None

def descendants(target):
    try: children=Path(f'/proc/{target}/task/{target}/children').read_text().split()
    except FileNotFoundError: return []
    result=[]
    for child in map(int,children):
        result.extend(descendants(child)); result.append(child)
    return result

start=identity(pid)
if start is not None:
    try: command=Path(f'/proc/{pid}/cmdline').read_bytes().decode(errors='replace').split('\x00')
    except FileNotFoundError: command=[]
    if command:
        if str(block/'scripts/terminal_curator.py') not in command or 'run' not in command:
            raise SystemExit('ERROR: recorded PID is not this block run; refusing to signal')
        if args.run_id is not None and not any(command[i:i+2]==['--run-id',args.run_id] for i in range(len(command)-1)):
            raise SystemExit('ERROR: recorded PID belongs to another run; refusing to signal')
        if metadata and metadata.get('start_ticks')!=start:
            raise SystemExit('ERROR: stale process identity; refusing to signal')
        targets={target:identity(target) for target in descendants(pid)+[pid]}
        try: os.kill(pid,signal.SIGTERM)
        except ProcessLookupError: pass
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and identity(pid)==start: time.sleep(.2)
        if identity(pid)==start:
            for target in descendants(pid): targets.setdefault(target,identity(target))
        for target,expected in targets.items():
            if expected is not None and identity(target)==expected:
                try: os.kill(target,signal.SIGKILL)
                except ProcessLookupError: pass

# Capture run_id from process identity, never from a possibly newer state.json.
# Dead PIDs still need recovery after SIGKILL/OOM interrupted Harbor's finally.
projects=set()
if run_id and (metadata or args.run_id is not None):
    validation=artifacts/'runs'/run_id/'validation'
    projects={path.parent.name.lower() for path in validation.glob('*/*/config.json')}
failed=False
if projects and not shutil.which('docker'):
    print('Docker CLI unavailable; inspect this run before restarting.',file=sys.stderr)
    failed=True
elif projects:
    for resource,listing,removal in (
        ('containers',['docker','ps','-a','--format','{{.ID}}\t{{.Label "com.docker.compose.project"}}'],['docker','rm','-f']),
        ('networks',['docker','network','ls','--format','{{.ID}}\t{{.Label "com.docker.compose.project"}}'],['docker','network','rm']),
    ):
        try:
            result=subprocess.run(listing,capture_output=True,text=True,timeout=5,check=True)
            ids=[]
            for line in result.stdout.splitlines():
                parts=line.split('\t',1)
                if len(parts)==2 and parts[1] in projects and re.fullmatch(r'[a-f0-9]+',parts[0]): ids.append(parts[0])
            if ids:
                subprocess.run(removal+ids,capture_output=True,timeout=15,check=True)
                print(f'Removed {len(ids)} owned Harbor {resource}.')
        except (subprocess.SubprocessError,OSError):
            failed=True
            print(f'Could not finish cleanup of owned Harbor {resource}; inspect this run before restarting.',file=sys.stderr)
if failed: raise SystemExit(1)
print('Stopped terminal-curator and recovered its recorded sidecars.' if start else 'Recorded process has exited; owned sidecar recovery complete.')
PY
