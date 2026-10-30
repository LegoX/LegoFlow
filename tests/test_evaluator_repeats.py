"""Evaluator k-repeat loop (RSI_LOOP_PLAN.md §2.1, §5.5, §10 fix 4).

``blocks/evaluator/scripts/start.sh`` gained ``runtime_info.input.harbor_job.repeats``:

* ``k == 1`` (default) must keep the historical single-pass behavior exactly —
  same job naming (no ``-r1`` suffix), same artifacts, same exit codes, and no
  weakness-report build.
* ``k > 1`` runs the Harbor + post-analysis section once per repeat with a
  fresh LiteLLM proxy lifecycle per repeat (the proxy's trajectory root is
  fixed at fork time), distinct ``-r1..-rk`` job dirs, and then joins the k job
  dirs via ``scripts/build_weakness_report.py``. A repeat that dies mid-round
  must still produce a best-effort report from the completed repeat job dirs,
  without masking the original exit code.

Two layers of checks, both offline (no network, no Docker, no Harbor env):

* static/parse checks — ``bash -n`` plus semantic structure assertions, and a
  real execution of the job-suffix helper extracted from start.sh;
* a control-flow harness that runs the *unmodified* start.sh inside a fully
  stubbed block tree (fake dryrun/probe/analyze/archive scripts, fake LiteLLM
  venv whose proxy stub records starts, fake Harbor checkout, and either a
  ``command_override`` that stands in for the Harbor run or — with no override
  configured — a fakebin ``uv`` that records the generated default command and
  writes one per-trial ``result.json`` into the repeat's job dir).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BLOCK = REPO_ROOT / "blocks" / "evaluator"
START = BLOCK / "scripts" / "start.sh"
DRYRUN = BLOCK / "scripts" / "dryrun.sh"

START_TEXT = START.read_text()
DRYRUN_TEXT = DRYRUN.read_text()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def bash_syntax_ok(path: Path) -> bool:
    proc = subprocess.run(
        ["bash", "-n", str(path)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"{path} has bash syntax errors:\n{proc.stderr}"
    return True


def function_region(text: str, name: str) -> str:
    """Extract a top-level bash function body by name.

    start.sh defines its functions with the opening ``name() {`` and the
    closing ``}`` alone at column 0, so scanning to the first such line is
    exact for every function this suite looks at.
    """
    start = re.search(rf"^{re.escape(name)}\(\) \{{$", text, re.M)
    assert start is not None, f"start.sh does not define function {name}()"
    tail = text[start.end():]
    end = re.search(r"^}$", tail, re.M)
    assert end is not None, f"could not find the closing brace of {name}()"
    return tail[:end.start()]


def call_position(region: str, call: str) -> int:
    """First position of a *call site* (not the comment prose) in a region."""
    match = re.search(rf"(?<![\w-]){re.escape(call)}\b", region)
    assert match is not None, f"no call site for {call}() in region:\n{region}"
    return match.start()


# ---------------------------------------------------------------------------
# (a) Both scripts parse
# ---------------------------------------------------------------------------
def test_start_sh_parses():
    assert bash_syntax_ok(START)


def test_dryrun_sh_parses():
    assert bash_syntax_ok(DRYRUN)


# ---------------------------------------------------------------------------
# (b) Semantic structure of start.sh
# ---------------------------------------------------------------------------
def test_start_sh_reads_repeats_leaf():
    assert "runtime_info.input.harbor_job.repeats" in START_TEXT


def test_start_sh_validates_repeats_as_positive_integer():
    region = START_TEXT[START_TEXT.index('REPEATS="'):START_TEXT.index('REPEATS="') + 700]
    assert re.search(r'REPEATS="\$\{REPEATS_RAW:-1\}"', region)
    assert re.search(r"\[\[ \"\$REPEATS\" =~ \^\[1-9\]\[0-9\]\*\$ \]\]", region)


def test_proxy_restarted_per_repeat():
    """The repeat runner must stop any previous proxy before starting a new one."""
    for fn in ("cleanup_litellm", "start_litellm_proxy", "wait_for_proxy"):
        assert re.search(rf"^{fn}\(\) \{{$", START_TEXT, re.M), f"missing {fn}()"

    region = function_region(START_TEXT, "run_single_repeat")
    stop = call_position(region, "cleanup_litellm")
    start = call_position(region, "start_litellm_proxy")
    assert stop < start, "run_single_repeat must stop the previous proxy before starting a new one"
    # The repeat loop invokes the runner once per repeat, bounded by REPEATS.
    assert re.search(r"^\s*while\s+.*REPEATS", START_TEXT, re.M), "no REPEATS-bounded loop"
    assert re.search(r'run_single_repeat\s+"\$', START_TEXT), "loop never calls run_single_repeat"


def test_per_repeat_trajectory_root_is_re_exported():
    """Each repeat re-points the proxy's trajectory root at its own job dir."""
    region = function_region(START_TEXT, "prepare_repeat")
    assert "EVAL_TRAJECTORY_FILE_PATTERN" in region
    assert "EVAL_JOB_NAME" in region and "EVAL_JOB_DIR" in region
    # The proxy artifacts dir is derived per repeat job name, not baked once.
    assert 'artifacts/litellm/$REPEAT_JOB_NAME' in region


def test_repeat_job_suffix_helper_is_used_for_job_naming():
    region = function_region(START_TEXT, "prepare_repeat")
    assert re.search(r'repeat_job_suffix\s+"\$REPEATS"', region), (
        "prepare_repeat must derive its suffix through repeat_job_suffix"
    )


def test_weakness_report_builder_invocation_matches_contract():
    region = function_region(START_TEXT, "build_weakness_report")
    assert "build_weakness_report.py" in region
    for flag in (
        "--jobs", "--dataset", "--audit-slice", "--out",
        "--history-dir", "--response-matrix", "--ledger",
    ):
        assert flag in region, f"builder invocation missing {flag}"
    # The receipt prepare_repeat pins into each job dir is handed to the
    # builder, guarded so its absence keeps the builder's own fallback probes.
    assert re.search(r'\[\[ -f "\$1/round_receipt\.json" \]\]', region)
    assert re.search(r'--receipt "\$1/round_receipt\.json"', region)
    assert re.search(r'\$\{receipt_flag\[@\]\+"\$\{receipt_flag\[@\]\}"\}', region)
    # Absent audit slice is tolerated with a warning, and a failed build is
    # non-fatal (never fails the eval run).
    assert re.search(r"\[\[ ! -f \"\$audit_slice\" \]\]", region)
    assert "unaffected" in region
    assert re.search(r"^\s*return 0$", region, re.M)


def test_builder_only_runs_for_multiple_repeats():
    """k == 1 must not produce a weakness report (pre-loop behavior)."""
    gated = re.search(
        r'if \[\[ "\$REPEATS" -gt 1 \]\][^\n]*\n(?:[^\n]*\n){0,6}?[^\n]*build_weakness_report',
        START_TEXT,
    )
    assert gated is not None, (
        "the main-flow build_weakness_report call must be gated on repeats > 1"
    )


def test_exit_trap_salvages_report_without_masking_exit_code():
    trap = function_region(START_TEXT, "_archive_run_on_exit")
    assert "local rc=$?" in trap
    assert "cleanup_litellm" in trap
    assert "weakness_report_salvage" in trap
    assert re.search(r"archive_run\.sh\"\s+\"\$rc\"", trap)
    assert re.search(r"^\s*exit \$rc$", trap, re.M), "trap must re-exit with the original rc"

    salvage = function_region(START_TEXT, "weakness_report_salvage")
    # Salvage is guarded: skipped for k == 1, skipped when nothing completed,
    # skipped when a build was already attempted, and never fails.
    assert "WEAKNESS_REPORT_ATTEMPTED" in salvage
    assert "REPEATS" in salvage and "${#REPEAT_JOB_DIRS[@]}" in salvage
    assert re.search(r"^\s*return 0$", salvage, re.M)


def test_completed_repeats_recorded_after_run_and_analysis():
    """Only repeats whose run + analysis section finished feed the report."""
    region = function_region(START_TEXT, "run_single_repeat")
    analysis = region.index("analyze_job.sh")
    appended = region.index("REPEAT_JOB_DIRS+=(")
    returned = region.index('return "$RUN_RC"')
    assert analysis < appended < returned
    # Only repeats that wrote a per-trial result.json may feed the report; an
    # empty repeat dir is warned about, never joined.
    find_guard = (
        'find "$REPEAT_JOB_DIR" -mindepth 2 -maxdepth 2 -type f '
        '-name result.json -print -quit'
    )
    assert find_guard in region[:appended], (
        "REPEAT_JOB_DIRS must be guarded by the per-trial result.json predicate"
    )


def test_run_single_repeat_guards_errexit_suppressed_failures():
    """`run_single_repeat ... || REPEAT_RC=$?` suspends errexit for the whole
    function body, so commands that were fatal at top level in master (the
    terminal-curator publication freeze, the cfg read) must guard themselves."""
    region = function_region(START_TEXT, "run_single_repeat")
    assert re.search(
        r'publication\.py" --registry "\$REGISTRY_ABS" \|\| return \$\?', region), (
        "the per-repeat publication freeze must propagate its exit code"
    )
    assert re.search(r'job_analysis\.enabled\)" \|\| return \$\?', region), (
        "the job_analysis cfg read must propagate a failure"
    )


def test_run_command_is_rebuilt_per_repeat_from_the_override():
    """RUN_COMMAND must not be reused across repeats: without command_override
    each repeat rebuilds the default Harbor command so its own -rN --job-name
    is embedded (a frozen repeat-1 command would rerun the -r1 job)."""
    assert 'COMMAND_OVERRIDE="$(cfg command_override)"' in START_TEXT
    assert 'RUN_COMMAND="$(cfg command_override)"' not in START_TEXT
    region = function_region(START_TEXT, "build_run_command")
    reset = re.search(r'RUN_COMMAND="\$COMMAND_OVERRIDE"', region)
    assert reset is not None, "build_run_command must reset from COMMAND_OVERRIDE"
    guard = re.search(r'\[\[ -n "\$RUN_COMMAND" \]\]', region)
    assert guard is not None and reset.start() < guard.start()


def test_proxy_trajectory_job_root_is_exported():
    """trajectory_logger routes "<job>/<trial>/agent" writes relative to
    TRAJECTORY_JOB_ROOT; without the export routing only works by the
    parents[2] coincidence of the default jobs dir."""
    export = re.search(r'^export TRAJECTORY_JOB_ROOT="\$HARBOR_JOBS_DIR"$',
                       START_TEXT, re.M)
    assert export is not None, "start.sh must export TRAJECTORY_JOB_ROOT"
    assert START_TEXT.index('HARBOR_JOBS_DIR="$(abspath') < export.start()
    assert export.start() < START_TEXT.index("start_litellm_proxy")


def test_round_receipt_is_copied_into_the_job_dir():
    """The trainer round receipt (contract 8) must be pinned into each repeat's
    job dir so build_weakness_report can key the row to checkpoint_sha."""
    region = function_region(START_TEXT, "prepare_repeat")
    assert "from_trainer_checkpoint" in region
    assert "round_receipt.json" in region
    assert re.search(r'cp "\$receipt_src" "\$REPEAT_JOB_DIR/round_receipt\.json"', region)


# ---------------------------------------------------------------------------
# (c) Unit test of the job-dir suffix helper (executed, not just grepped)
# ---------------------------------------------------------------------------
def extract_bash_function(text: str, name: str) -> str:
    lines = text.splitlines()
    header = f"{name}() {{"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    assert start is not None, f"could not find {header} in start.sh"
    end = next((j for j in range(start + 1, len(lines)) if lines[j] == "}"), None)
    assert end is not None, f"could not find the closing brace of {name}()"
    return "\n".join(lines[start:end + 1])


@pytest.mark.parametrize(
    ("repeats", "index", "expected"),
    [
        (1, 1, ""),          # single pass: historical job naming, no suffix
        (2, 1, "-r1"),
        (3, 1, "-r1"),
        (3, 2, "-r2"),
        (3, 3, "-r3"),
        (5, 5, "-r5"),
    ],
)
def test_repeat_job_suffix(repeats: int, index: int, expected: str):
    helper = extract_bash_function(START_TEXT, "repeat_job_suffix")
    probe = f'printf "[%s]\\n" "$(repeat_job_suffix {repeats} {index})"'
    proc = subprocess.run(
        ["bash", "-c", helper + "\n" + probe], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"[{expected}]\n"


# ---------------------------------------------------------------------------
# (d) dryrun.sh stays in sync: parses, mentions, and defaults the leaf
# ---------------------------------------------------------------------------
def test_dryrun_mentions_repeats_leaf():
    assert "runtime_info.input.harbor_job.repeats" in DRYRUN_TEXT
    # A missing leaf degrades to a single-pass warning, never a crash.
    assert re.search(r"missing\)", DRYRUN_TEXT)
    assert "single pass" in DRYRUN_TEXT


def _dryrun_classifier() -> str:
    blocks = re.findall(
        r"python3 - \"\$CONFIG\" <<'PY'\n(.*?)\nPY\n", DRYRUN_TEXT, re.S
    )
    matches = [b for b in blocks if "harbor_job.repeats" in b]
    assert matches, "dryrun.sh has no repeats classifier heredoc"
    return matches[0]


@pytest.mark.parametrize(
    ("repeats_value", "expected"),
    [
        (3, "int:3"),
        (1, "int:1"),
        (None, "missing"),
        ("3", "bad:'3'"),
        (0, "bad:0"),
        (-2, "bad:-2"),
        (True, "bad:True"),
        (1.5, "bad:1.5"),
    ],
)
def test_dryrun_repeats_classifier(repeats_value, expected, tmp_path):
    """Executes dryrun.sh's actual classifier against throwaway configs."""
    harbor_job = {} if repeats_value is None else {"repeats": repeats_value}
    config = {"runtime_info": {"input": {"harbor_job": harbor_job}}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    proc = subprocess.run(
        [sys.executable, "-c", _dryrun_classifier(), str(config_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected


# ---------------------------------------------------------------------------
# Control-flow harness: run the unmodified start.sh over a stub block tree
# ---------------------------------------------------------------------------
STUB_DRYRUN = """#!/usr/bin/env bash
exit 0
"""

STUB_PROBE = """#!/usr/bin/env bash
exit 0
"""

STUB_ANALYZE = """#!/usr/bin/env bash
mkdir -p "$HARNESS_STATE"
echo "$1" >> "$HARNESS_STATE/analyze_calls.txt"
mkdir -p "$1/analysis"
exit 0
"""

STUB_ARCHIVE = """#!/usr/bin/env bash
mkdir -p "$HARNESS_STATE"
echo "rc=$1" >> "$HARNESS_STATE/archive_calls.txt"
exit 0
"""

STUB_PROXY = """#!/usr/bin/env bash
mkdir -p "$HARNESS_STATE"
echo "$EVAL_JOB_NAME" >> "$HARNESS_STATE/proxy_starts.txt"
exec sleep 20
"""

STUB_PY_OK = """#!/usr/bin/env bash
exit 0
"""

# Stand-in for the proxy-readiness socket probe ($EVAL_LITELLM_PYTHON): succeed
# only once this repeat's proxy stub has recorded its start. That models the
# real contract ("proxy is listening before Harbor launches") and removes the
# harness race where cleanup could kill a slow stub before its marker landed.
STUB_PROXY_READY = """#!/usr/bin/env bash
marker="$HARNESS_STATE/proxy_starts.txt"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if [[ -f "$marker" ]] && grep -qx "$EVAL_JOB_NAME" "$marker"; then
    exit 0
  fi
  sleep 0.2
done
exit 0
"""

STUB_SETSID = """#!/usr/bin/env bash
# Harness stand-in for util-linux setsid (absent on macOS dev machines).
exec "$@"
"""

# Stand-in for `uv` (the default RUN_COMMAND is `uv run harbor run ...`): records
# the full argv (so each repeat's --job-name can be asserted) and writes one
# per-trial result.json into this repeat's job dir — the minimal artifact a
# repeat needs before it may feed the weakness report. HARNESS_SIM_FAIL_ON
# (a job-name suffix such as "-r2") makes that repeat exit HARNESS_SIM_FAIL_RC
# (default 3) before writing anything.
STUB_UV = """#!/usr/bin/env bash
mkdir -p "$HARNESS_STATE"
printf '%s\\n' "$*" >> "$HARNESS_STATE/uv_calls.txt"
if [[ -n "${HARNESS_SIM_FAIL_ON:-}" && "$EVAL_JOB_NAME" == *"${HARNESS_SIM_FAIL_ON}" ]]; then
  exit "${HARNESS_SIM_FAIL_RC:-3}"
fi
trial="$EVAL_JOB_DIR/harness__trial-0001"
mkdir -p "$trial"
printf '%s\\n' '{"task_name": "harness__trial-0001", "verifier_result": {"rewards": {"reward": 1}}}' \\
  > "$trial/result.json"
exit 0
"""

# Stand-in for terminal-curator's publication freeze (invoked via `python3`):
# the pre-loop setup call (start.sh top level) succeeds; every later per-repeat
# freeze fails with rc=2.
STUB_PUBLICATION = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

state = Path(os.environ["HARNESS_STATE"])
state.mkdir(parents=True, exist_ok=True)
with (state / "publication_calls.txt").open("a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
if (state / "publication_frozen").exists():
    sys.exit(2)
(state / "publication_frozen").touch()
sys.exit(0)
"""

STUB_REDACT = """#!/usr/bin/env python3
import os
import sys

target = sys.argv[2]
if os.environ.get("HARNESS_FAIL_PREPARE_ON", "") and os.environ["HARNESS_FAIL_PREPARE_ON"] in target:
    sys.exit(3)
with open(sys.argv[1], encoding="utf-8") as src, open(target, "w", encoding="utf-8") as dst:
    dst.write(src.read())
"""

STUB_BUILDER = """#!/usr/bin/env python3
import json
import os
import sys

os.makedirs(os.environ["HARNESS_STATE"], exist_ok=True)
with open(os.path.join(os.environ["HARNESS_STATE"], "builder_calls.jsonl"), "a",
          encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
raise SystemExit(int(os.environ.get("HARNESS_BUILDER_RC", "0")))
"""

FAKE_CONFIG = """
command_override: {command_override}
meta_info:
  repositories:
    harbor:
      path: repos/harbor
  environment:
    harbor_uv: artifacts/env/harbor-uv
    litellm_uv: artifacts/env/litellm-venv
runtime_info:
  input:
    llm_api:
      api_key: harness-key
      api_base_url: http://127.0.0.1:9/v1
      model: openai/Harness-Model
    task_source:
      provider: harbor_registry
      dataset_name: {dataset_name}
      version: "1.0"
      registry_path: {registry_path}
      no_hack: false
    harbor_job:
      jobs_dir: artifacts/jobs
      job_name_prefix: harnessjob
      n_concurrent: 2
      n_tasks: null
      max_retries: 0
      timeout_multiplier: 1
      repeats: {repeats}
    agent:
      name: custom-openhands-sdk
      version: "1.0"
      model_name: Harness-Model
      max_turns: 5
      temperature: 1.0
      runtime_image: harness-img
      runtime_host_path: ""
    job_analysis:
      enabled: true
    litellm_proxy:
      config_template: scripts/serve_llm/litellm_config.example.yaml
      port: 4101
      master_key: harness-master-key
    runtime_mount:
      container_runtime_root: /opt/custom-agent-runtime/oh-sdk
{local_model_serving}  output: {{}}
"""


def make_stub_tree(tmp_path: Path, repeats: int, command_override: str, *,
                   dataset_name: str = "swebench-verified",
                   registry_path: str = "repos/harbor/registry.json",
                   trainer_checkpoint: str = "") -> tuple[Path, Path]:
    """Build a stub evaluator block; returns (block_dir, state_dir).

    ``BLOCK_DIR`` is derived from the copied start.sh's own location, so the
    shipped script runs unmodified against the fake tree. ``dataset_name`` /
    ``registry_path`` select the benchmark; ``terminal-curator`` additionally
    stubs the producer's registry + publication freeze. ``trainer_checkpoint``
    fills ``local_model_serving.from_trainer_checkpoint``.
    """
    root = tmp_path / "faketree"
    block = root / "blocks" / "evaluator"
    state = tmp_path / "state"

    scripts = block / "scripts"
    (scripts).mkdir(parents=True)
    (block / "artifacts").mkdir()
    (root / "scripts").mkdir(parents=True)
    (state).mkdir()

    shutil.copy2(START, scripts / "start.sh")

    def write(path: Path, text: str, executable: bool = False) -> None:
        path.write_text(textwrap.dedent(text).lstrip("\n"))
        if executable:
            path.chmod(0o755)

    write(scripts / "dryrun.sh", STUB_DRYRUN, executable=True)
    write(scripts / "probe_llm_completion.sh", STUB_PROBE, executable=True)
    write(scripts / "analyze_job.sh", STUB_ANALYZE, executable=True)
    write(scripts / "archive_run.sh", STUB_ARCHIVE, executable=True)
    write(scripts / "build_weakness_report.py", STUB_BUILDER, executable=True)
    write(root / "scripts" / "redact_archive_config.py", STUB_REDACT, executable=True)

    harbor = block / "repos" / "harbor"
    serve = harbor / "scripts" / "serve_llm"
    serve.mkdir(parents=True)
    (harbor / ".git").write_text("harness\n")
    (harbor / "registry.json").write_text("[]\n")
    (serve / "litellm_config.example.yaml").write_text("litellm_settings:\n  drop_params: true\n")
    (serve / "trajectory_logger.py").write_text("# harness stub\n")
    write(serve / "serve_litellm.sh", STUB_PROXY, executable=True)

    for rel, stub_text in (
        ("artifacts/env/harbor-uv/bin/python", STUB_PY_OK),
        ("artifacts/env/litellm-venv/bin/python", STUB_PROXY_READY),
        ("artifacts/env/litellm-venv/bin/litellm", STUB_PY_OK),
    ):
        stub = block / rel
        stub.parent.mkdir(parents=True, exist_ok=True)
        write(stub, stub_text, executable=True)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    write(fakebin / "setsid", STUB_SETSID, executable=True)
    write(fakebin / "uv", STUB_UV, executable=True)

    if dataset_name == "terminal-curator":
        producer = root / "blocks" / "terminal-curator"
        (producer / "artifacts" / "tasks").mkdir(parents=True)
        (producer / "artifacts" / "tasks" / "registry.json").write_text(
            json.dumps([{"name": "terminal-curator", "version": "1.0"}]) + "\n")
        (producer / "scripts").mkdir(parents=True)
        write(producer / "scripts" / "publication.py", STUB_PUBLICATION, executable=True)

    if trainer_checkpoint:
        local_model_serving = (
            "    local_model_serving:\n"
            f"      from_trainer_checkpoint: {json.dumps(trainer_checkpoint)}\n"
        )
    else:
        local_model_serving = ""
    (block / "config.yaml").write_text(
        FAKE_CONFIG.format(repeats=repeats, command_override=command_override,
                           dataset_name=dataset_name, registry_path=registry_path,
                           local_model_serving=local_model_serving)
    )
    return block, state


def run_start_sh(block: Path, state: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    fakebin = block.parent.parent.parent / "fakebin"
    env = {
        # Prefer the interpreter running this suite (has PyYAML for cfg reads),
        # then the harness bin (setsid stand-in), then the inherited PATH.
        "PATH": f"{Path(sys.executable).parent}:{fakebin}:{os.environ['PATH']}",
        "LITELLM_HOST_IP": "127.0.0.1",  # macOS lacks `hostname -I`
        "HARNESS_STATE": str(state),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    for var in ("EVAL_CONFIG", "EVAL_UPDATE_REPOS", "LITELLM_PORT"):
        env.pop(var, None)
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(block / "scripts" / "start.sh")],
        cwd=str(block), env=env, text=True, capture_output=True, timeout=180,
    )


def state_lines(state: Path, name: str) -> list[str]:
    path = state / name
    return path.read_text().splitlines() if path.exists() else []


def job_dirs(block: Path) -> list[str]:
    jobs = block / "artifacts" / "jobs"
    return sorted(p.name for p in jobs.iterdir()) if jobs.is_dir() else []


def builder_calls(state: Path) -> list[list[str]]:
    return [json.loads(line) for line in state_lines(state, "builder_calls.jsonl")]


HARNESS_JOB_RE = r"^harnessjob-\d{14}$"
HARNESS_JOB_REPEAT_RE = r"^harnessjob-\d{14}-(r\d)$"


def test_harness_single_pass_matches_pre_loop_behavior(tmp_path):
    """k=1: one unsuffixed job dir, one proxy, one analysis, no report build."""
    block, state = make_stub_tree(tmp_path, repeats=1, command_override="exit 0")
    proc = run_start_sh(block, state)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dirs = job_dirs(block)
    assert len(dirs) == 1, f"expected exactly one job dir, got {dirs}"
    assert re.match(HARNESS_JOB_RE, dirs[0]), f"job dir must carry no repeat suffix: {dirs[0]}"
    assert state_lines(state, "proxy_starts.txt") == [dirs[0]]
    assert state_lines(state, "analyze_calls.txt") == [str(block / "artifacts" / "jobs" / dirs[0])]
    # Pre-loop behavior: no weakness report is built and no loop artifacts appear.
    assert builder_calls(state) == []
    assert not (block / "artifacts" / "weakness").exists()
    assert not (block / "artifacts" / "loop").exists()
    assert "repeat 1/1" not in proc.stdout


def test_harness_three_repeats_suffix_dirs_and_join_report(tmp_path):
    """k=3: -r1..-r3 job dirs, a fresh proxy per repeat, one joined report."""
    block, state = make_stub_tree(tmp_path, repeats=3, command_override="")
    proc = run_start_sh(block, state)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dirs = job_dirs(block)
    assert len(dirs) == 3, f"expected three repeat job dirs, got {dirs}"
    for name in dirs:
        assert re.match(HARNESS_JOB_REPEAT_RE, name), f"expected a -rN suffix: {name}"
    assert sorted(d.rsplit("-", 1)[-1] for d in dirs) == ["r1", "r2", "r3"]

    # One proxy start per repeat, each pointing at that repeat's job name.
    assert state_lines(state, "proxy_starts.txt") == dirs
    # Per-repeat analysis on each repeat's own job dir.
    analyze = [Path(line).name for line in state_lines(state, "analyze_calls.txt")]
    assert analyze == dirs

    calls = builder_calls(state)
    assert len(calls) == 1, f"expected exactly one builder call, got {calls}"
    argv = calls[0]
    jobs_root = block / "artifacts" / "jobs"
    assert argv[0] == "--jobs"
    assert argv[1:4] == [str(jobs_root / d) for d in sorted(dirs, key=lambda n: n.rsplit("-", 1)[-1])]
    rest = dict(zip(argv[4::2], argv[5::2]))
    assert rest["--dataset"] == "swebench-verified"
    assert rest["--out"].endswith("artifacts/weakness/weakness_report.json")
    assert rest["--history-dir"].endswith("artifacts/weakness/history")
    assert rest["--response-matrix"].endswith("artifacts/response_matrix.jsonl")
    assert rest["--ledger"].endswith("artifacts/loop/ledger.yaml")
    assert rest["--audit-slice"].endswith("artifacts/reserve/audit_slice.txt")
    # The audit slice is absent in the harness: warned, not fatal.
    assert "audit slice absent" in proc.stdout
    assert state_lines(state, "archive_calls.txt") == ["rc=0"]


def test_harness_builder_failure_is_non_fatal(tmp_path):
    block, state = make_stub_tree(tmp_path, repeats=2, command_override="")
    proc = run_start_sh(block, state, extra_env={"HARNESS_BUILDER_RC": "7"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(builder_calls(state)) == 1
    assert "weakness report build failed" in proc.stdout
    assert "unaffected" in proc.stdout


def test_harness_harbor_failure_stops_loop_and_reports_completed_repeats(tmp_path):
    """A repeat whose Harbor run fails stops the loop; salvage still reports.

    Repeat 2's Harbor run exits rc=3 before writing any trial result: its
    analysis is skipped and it is excluded from the weakness-report join — the
    completed, populated repeat 1 is the only reportable job dir.
    """
    block, state = make_stub_tree(tmp_path, repeats=3, command_override="")
    proc = run_start_sh(block, state, extra_env={"HARNESS_SIM_FAIL_ON": "-r2"})

    assert proc.returncode == 3, proc.stdout + proc.stderr
    dirs = job_dirs(block)
    assert sorted(d.rsplit("-", 1)[-1] for d in dirs) == ["r1", "r2"], f"no repeat 3 expected: {dirs}"
    # Repeat 2 ran (partial) + was recorded as a proxy start; repeat 3 never started.
    assert len(state_lines(state, "proxy_starts.txt")) == 2
    # Repeat 2 wrote no result.json: no analysis for it, excluded from the join.
    assert [Path(line).name for line in state_lines(state, "analyze_calls.txt")] == [dirs[0]]
    assert "excluded from the weakness report" in proc.stdout

    calls = builder_calls(state)
    assert len(calls) == 1, calls
    argv = calls[0]
    reported = [Path(a).name for a in argv[1:argv.index("--dataset")]]
    assert reported == [dirs[0]], f"salvage must report only the populated repeat, got {reported}"
    # The original exit code reaches archive_run.sh unmasked.
    assert "rc=3" in state_lines(state, "archive_calls.txt")


def test_harness_midround_death_salvages_only_completed_repeats(tmp_path):
    """A repeat that dies before finishing is excluded from the salvage set."""
    block, state = make_stub_tree(tmp_path, repeats=3, command_override="")
    proc = run_start_sh(block, state, extra_env={"HARNESS_FAIL_PREPARE_ON": "-r2"})

    assert proc.returncode == 1, proc.stdout + proc.stderr
    dirs = job_dirs(block)
    assert sorted(d.rsplit("-", 1)[-1] for d in dirs) == ["r1", "r2"], f"no repeat 3 expected: {dirs}"
    # Repeat 2 died in prepare: no proxy, no analysis for it.
    assert state_lines(state, "proxy_starts.txt") == [dirs[0]]
    assert len(state_lines(state, "analyze_calls.txt")) == 1

    calls = builder_calls(state)
    assert len(calls) == 1, calls
    argv = calls[0]
    jobs_root = block / "artifacts" / "jobs"
    assert argv[0] == "--jobs"
    assert argv[1] == str(jobs_root / dirs[0])
    assert str(jobs_root / dirs[1]) not in argv, "an incomplete repeat must not be reported"
    assert "rc=1" in state_lines(state, "archive_calls.txt")


# ---------------------------------------------------------------------------
# Regressions: per-repeat RUN_COMMAND, suspended errexit, receipt pinning
# ---------------------------------------------------------------------------
def test_harness_repeats_run_harbor_with_their_own_job_name(tmp_path):
    """k=3 without command_override: every repeat's Harbor invocation embeds
    its own -rN --job-name.

    Regression: RUN_COMMAND used to be built once at repeat 1 and reused
    verbatim, so repeats 2..k reran Harbor against the -r1 job name.
    """
    block, state = make_stub_tree(tmp_path, repeats=3, command_override="")
    proc = run_start_sh(block, state)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = state_lines(state, "uv_calls.txt")
    assert len(calls) == 3, calls
    names = []
    for call in calls:
        match = re.search(r"--job-name (\S+)", call)
        assert match is not None, f"no --job-name in recorded command: {call}"
        names.append(match.group(1))
    assert [name.rsplit("-", 1)[-1] for name in names] == ["r1", "r2", "r3"], names
    # Each recorded job name is exactly the job dir Harbor was told to fill.
    assert [name.rsplit("-", 1)[-1] for name in sorted(names)] == sorted(
        d.rsplit("-", 1)[-1] for d in job_dirs(block))


def test_harness_override_wins_for_every_repeat(tmp_path):
    """command_override stays authoritative on each rebuild (k=2)."""
    marker = 'echo override-ran >> "$HARNESS_STATE/override_runs.txt"'
    block, state = make_stub_tree(tmp_path, repeats=2, command_override=marker)
    proc = run_start_sh(block, state)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert state_lines(state, "override_runs.txt") == ["override-ran", "override-ran"]
    assert state_lines(state, "uv_calls.txt") == []


def test_harness_publication_freeze_failure_stops_run(tmp_path):
    """terminal-curator: a failing per-repeat publication freeze must stop the
    run with its own exit code.

    Regression: run_single_repeat runs under `... || REPEAT_RC=$?` (errexit
    suspended), so the unguarded publication call used to fail silently and
    Harbor launched anyway.
    """
    block, state = make_stub_tree(
        tmp_path, repeats=1,
        command_override='echo harbor-ran >> "$HARNESS_STATE/harbor_runs.txt"',
        dataset_name="terminal-curator",
        registry_path="../terminal-curator/artifacts/tasks/registry.json",
    )
    proc = run_start_sh(block, state)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    # The pre-loop setup freeze succeeded; the per-repeat freeze failed rc=2.
    assert len(state_lines(state, "publication_calls.txt")) == 2
    # Harbor never launched, and the original rc reaches archive_run.sh.
    assert not (state / "harbor_runs.txt").exists()
    assert state_lines(state, "archive_calls.txt") == ["rc=2"]


def test_harness_round_receipt_copied_into_job_dir(tmp_path):
    """from_trainer_checkpoint set: the trainer round receipt lands in the job
    dir so build_weakness_report can pin checkpoint_sha for the round."""
    ckpt_dir = tmp_path / "model" / "round1"
    ckpt_dir.mkdir(parents=True)
    (ckpt_dir / "model.safetensors").write_text("weights\n")
    receipt = {"round": 1, "checkpoint_sha256": "ab" * 32, "n_instances": 1200}
    (ckpt_dir / "round_receipt.json").write_text(json.dumps(receipt))
    block, state = make_stub_tree(
        tmp_path, repeats=2, command_override="",
        trainer_checkpoint=str(ckpt_dir / "model.safetensors"),
    )
    proc = run_start_sh(block, state)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dirs = job_dirs(block)
    for job in dirs:
        copied = json.loads(
            (block / "artifacts" / "jobs" / job / "round_receipt.json").read_text())
        assert copied == receipt
    # The copied receipt is wired into the builder call, so the round is keyed
    # to checkpoint_sha instead of degrading to 'unknown'.
    calls = builder_calls(state)
    assert len(calls) == 1, calls
    argv = calls[0]
    receipt_idx = argv.index("--receipt")
    assert Path(argv[receipt_idx + 1]) == \
        Path(block / "artifacts" / "jobs" / dirs[0] / "round_receipt.json")


def test_harness_k1_job_name_is_a_fresh_timestamp(tmp_path):
    """The k=1 job name is built as prefix + `date +%Y%m%d%H%M%S`: anchor it
    semantically, not just by regex shape."""
    assert 'JOB_NAME="${JOB_NAME_PREFIX}-$(date +%Y%m%d%H%M%S)"' in START_TEXT
    # `date +%Y%m%d%H%M%S` truncates to whole seconds — bracket at that
    # granularity so sub-second capture skew cannot break the bounds.
    before = datetime.now().replace(microsecond=0)  # local, like `date`
    block, state = make_stub_tree(tmp_path, repeats=1, command_override="exit 0")
    proc = run_start_sh(block, state)
    after = datetime.now().replace(microsecond=0)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dirs = job_dirs(block)
    assert len(dirs) == 1, dirs
    observed = datetime.strptime(dirs[0].rsplit("-", 1)[-1], "%Y%m%d%H%M%S")
    assert before <= observed <= after, (
        f"job dir timestamp {dirs[0]} is not bracketed by the run window "
        f"[{before}, {after}]"
    )
