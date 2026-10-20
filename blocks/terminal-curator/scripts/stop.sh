#!/usr/bin/env bash
# Signal only the process recorded by this block; never touch unrelated jobs.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
pid_file="$BLOCK_DIR/artifacts/run.pid"
[[ -f "$pid_file" ]] || { echo 'No terminal-curator process recorded.'; exit 0; }
pid="$(cat "$pid_file")"
[[ "$pid" =~ ^[0-9]+$ ]] || { echo 'ERROR: invalid PID record' >&2; exit 1; }
"$PYTHON" - "$pid" "$BLOCK_DIR" <<'PY'
import json, os, re, shutil, signal, subprocess, sys, time
from pathlib import Path
pid=int(sys.argv[1]); block=sys.argv[2]
path=Path(f'/proc/{pid}/cmdline')
if not path.exists():
    print('Recorded process has exited.'); raise SystemExit(0)
command=path.read_bytes().replace(b'\x00', b' ').decode()
if block+'/scripts/terminal_curator.py' not in command or ' run' not in command:
    raise SystemExit('ERROR: recorded PID is not this block run; refusing to signal')
def identity(target):
    try: return Path(f'/proc/{target}/stat').read_text().split()[21]
    except FileNotFoundError: return None
def descendants(target):
    try: children=Path(f'/proc/{target}/task/{target}/children').read_text().split()
    except FileNotFoundError: return []
    result=[]
    for child in map(int, children): result.extend(descendants(child)); result.append(child)
    return result
targets={target: identity(target) for target in descendants(pid)+[pid]}
os.kill(pid, signal.SIGTERM)
deadline=time.monotonic()+10
while time.monotonic()<deadline and identity(pid)==targets[pid]: time.sleep(.2)
for target, start in targets.items():
    if start is not None and identity(target)==start:
        try: os.kill(target, signal.SIGKILL)
        except ProcessLookupError: pass
# Harbor normally deletes its containers in finally. After a forced kill,
# recover only compose projects recorded under this block's current run.
state_file=Path(block)/'artifacts/state.json'
if shutil.which('docker') and state_file.exists():
    state=json.loads(state_file.read_text())
    run_id=str(state.get('run_id',''))
    if re.fullmatch(r'[A-Za-z0-9_-]+',run_id):
        validation=Path(block)/'artifacts/runs'/run_id/'validation'
        projects={p.parent.name.lower() for p in validation.glob('*/*/config.json')}
        for resource, listing, removal in (
            ('containers',['docker','ps','-a','--format','{{.ID}}\t{{.Label "com.docker.compose.project"}}'],['docker','rm','-f']),
            ('networks',['docker','network','ls','--format','{{.ID}}\t{{.Label "com.docker.compose.project"}}'],['docker','network','rm']),
        ):
            try:
                result=subprocess.run(listing,capture_output=True,text=True,timeout=5)
                ids=[]
                for line in result.stdout.splitlines():
                    parts=line.split('\t',1)
                    if len(parts)==2 and parts[1] in projects and re.fullmatch(r'[a-f0-9]+',parts[0]):ids.append(parts[0])
                if ids:
                    subprocess.run(removal+ids,capture_output=True,timeout=15,check=True)
                    print(f'Removed {len(ids)} owned Harbor {resource}.')
            except (subprocess.SubprocessError,OSError):
                print(f'Could not finish cleanup of owned Harbor {resource}; inspect this run before restarting.',file=sys.stderr)
print('Stopped terminal-curator and its recorded subprocesses.')
PY
