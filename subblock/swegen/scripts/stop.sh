#!/usr/bin/env bash
# Stop the swegen pipeline cleanly.
#
# Kills, in order:
#   1. the create_all_bg.sh launcher (if still alive),
#   2. each per-language bash scripts/create_<lang>.sh worker,
#   3. any `swegen create` python process spawned by those workers.
#
# Idempotent: re-running after everything is gone exits 0 with no error.
# Sends SIGTERM first, waits up to $GRACE seconds, then escalates to SIGKILL.
#
# Flags:
#   -n|--dry-run     list what would be killed; touch nothing
#   --with-docker    additionally stop any docker containers whose image
#                    name starts with `harbor` or `swegen-` (off by default
#                    so we don't disturb unrelated user containers)
#   --grace SECONDS  TERM→KILL grace period (default 15)
#   -h|--help        usage

set -uo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DRY_RUN=0
WITH_DOCKER=0
GRACE=15
while [ $# -gt 0 ]; do
    case "$1" in
        -n|--dry-run) DRY_RUN=1 ;;
        --with-docker) WITH_DOCKER=1 ;;
        --grace) GRACE="${2:-15}"; shift ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "stop.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

# Argv patterns that uniquely identify swegen worker processes.
# Anchored on the block dir's scripts/ path so we never match a homonym
# process from another block or another user.
PATTERNS=(
    "bash[[:space:]]+${BLOCK_DIR}/scripts/create_all_bg\.sh"
    "bash[[:space:]]+(\./)?scripts/create_(py|js|ts|go|c|cpp|java|rust)\.sh"
    "${BLOCK_DIR}/artifacts/envs/swegen-env/bin/python.*swegen.*create"
)

# Collect PIDs once so the report and the kill loop see the same set.
# Exclude this stop.sh itself from any matches.
SELF_PID=$$
collect_pids() {
    local matched=()
    for pat in "${PATTERNS[@]}"; do
        # pgrep -f reads /proc/<pid>/cmdline; -d $'\n' splits on newlines
        while IFS= read -r pid; do
            [ -z "$pid" ] && continue
            [ "$pid" = "$SELF_PID" ] && continue
            matched+=("$pid")
        done < <(pgrep -f "$pat" 2>/dev/null || true)
    done
    # Deduplicate, preserve order.
    printf '%s\n' "${matched[@]}" | awk '!seen[$0]++'
}

PIDS="$(collect_pids)"

if [ -z "$PIDS" ]; then
    echo "stop.sh: no swegen workers running."
else
    echo "stop.sh: target processes:"
    while IFS= read -r pid; do
        cmd="$(ps -o cmd= -p "$pid" 2>/dev/null | head -c 160)"
        echo "  pid=$pid  $cmd"
    done <<<"$PIDS"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "stop.sh: --dry-run, exiting without killing."
    else
        echo "stop.sh: sending SIGTERM…"
        while IFS= read -r pid; do
            kill -TERM "$pid" 2>/dev/null || true
        done <<<"$PIDS"

        # Wait up to $GRACE seconds for them to exit on their own.
        end=$(( $(date +%s) + GRACE ))
        while [ "$(date +%s)" -lt "$end" ]; do
            remaining=()
            while IFS= read -r pid; do
                kill -0 "$pid" 2>/dev/null && remaining+=("$pid")
            done <<<"$PIDS"
            [ ${#remaining[@]} -eq 0 ] && break
            sleep 1
        done

        # Anything still alive gets SIGKILL.
        STILL_ALIVE=()
        while IFS= read -r pid; do
            kill -0 "$pid" 2>/dev/null && STILL_ALIVE+=("$pid")
        done <<<"$PIDS"
        if [ ${#STILL_ALIVE[@]} -gt 0 ]; then
            echo "stop.sh: ${#STILL_ALIVE[@]} process(es) survived ${GRACE}s, sending SIGKILL: ${STILL_ALIVE[*]}"
            for pid in "${STILL_ALIVE[@]}"; do
                kill -KILL "$pid" 2>/dev/null || true
            done
        fi
        echo "stop.sh: worker processes stopped."
    fi
fi

if [ "$WITH_DOCKER" -eq 1 ]; then
    if ! command -v docker >/dev/null 2>&1; then
        echo "stop.sh: --with-docker requested but docker binary not found; skipping container cleanup." >&2
    else
        CONTAINERS="$(docker ps --filter 'ancestor=' --format '{{.ID}} {{.Image}}' 2>/dev/null \
            | awk 'tolower($2) ~ /^(harbor|swegen-)/ {print $1}')"
        if [ -z "$CONTAINERS" ]; then
            echo "stop.sh: no harbor/swegen-* containers running."
        else
            echo "stop.sh: stopping containers:"
            echo "$CONTAINERS" | sed 's/^/  /'
            if [ "$DRY_RUN" -eq 1 ]; then
                echo "stop.sh: --dry-run, not stopping."
            else
                # shellcheck disable=SC2086
                docker stop -t 5 $CONTAINERS >/dev/null || true
                echo "stop.sh: containers stopped."
            fi
        fi
    fi
fi

exit 0
