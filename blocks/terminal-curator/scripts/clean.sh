#!/usr/bin/env bash
# Remove regenerable candidates/logs; retain verified output, env and archives.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
ALL=0; DRY=0; YES=0
for arg in "$@"; do
  case "$arg" in
    --all) ALL=1 ;;
    --dry-run) DRY=1 ;;
    --yes) YES=1 ;;
    --help|-h) echo 'Usage: clean.sh [--all --yes] [--dry-run]'; exit 0 ;;
    *) echo "ERROR: unknown argument: $arg" >&2; exit 2 ;;
  esac
done
if [[ "$ALL" = 1 && "$DRY" = 0 ]]; then
  [[ "$YES" = 1 || "${TERMINAL_CURATOR_CLEAN_CONFIRM:-}" == terminal-curator ]] || {
    echo 'ERROR: --all removes this block artifacts, including verified tasks. Set TERMINAL_CURATOR_CLEAN_CONFIRM=terminal-curator to confirm.' >&2; exit 1;
  }
fi
"$PYTHON" - "$BLOCK_DIR/artifacts" "$ALL" "$DRY" <<'PY'
import fcntl, shutil, sys
from pathlib import Path
p=Path(sys.argv[1])
if not p.exists(): raise SystemExit(0)
if p.is_symlink(): raise SystemExit('ERROR: artifacts must not be a symlink')
if sys.argv[3]=='1':
    print('Would remove: '+str(p if sys.argv[2]=='1' else p/'runs')); raise SystemExit(0)
with (p/'run.lock').open('a') as lock:
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('ERROR: stop the active run before cleaning')
    if sys.argv[2]=='1':
        for child in p.iterdir():
            if child.name=='run.lock': continue
            if child.is_dir() and not child.is_symlink(): shutil.rmtree(child)
            else: child.unlink()
    else:
        runs=p/'runs'
        # Published tasks are copies, so deleting temporary run trees cannot break tracer links.
        if runs.exists(): shutil.rmtree(runs)
print('terminal-curator cleanup complete')
PY
