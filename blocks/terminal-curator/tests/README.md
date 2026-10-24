Run from `blocks/terminal-curator`:

```bash
uv venv artifacts/env/tests-uv
uv pip install --python artifacts/env/tests-uv/bin/python -r tests/requirements.txt
bash tests/run.sh
```

The offline pytest suite verifies safe generation paths, source preservation,
normalization, strict Oracle/NOP evidence checks, rejection of empty/erroring
test runs, manifest replacement, immutable published versions, and credential
redaction. Lifecycle checks cover PID ownership, effective-overlay archiving,
secret redaction, pinned repositories, rejection of stale run reports, cleanup
that preserves published versions, and stop refusing unrelated process IDs.
Dashboard checks cover safe snapshots, HTML escaping, live HTTP state on a
temporary loopback server, and 404 responses outside the route allowlist.
Collection checks cover bounded JSON inputs, accepted answers, deduplication,
output path containment, and generated environment limits.
Generator-review regressions use a minimal parent that returns a candidate
after failed reviews, with fake completion responses. They require an actual
boolean approval for that candidate, exercise plain/fenced JSON, and ensure
later negative reviews replace an earlier approval for the same code. Live
review quality is assessed in the per-task semantic audit.
It also executes both fixture reference solutions against relocated
temporary input directories and confirms that fresh inputs and empty outputs
fail their verifiers. It makes no external network, LLM, or Docker calls.

To request the actual Docker/Harbor smoke after the block's setup and preflight:

```bash
bash tests/run.sh --with-smoke
```

The runner calls `scripts/start.sh --config tests/smoke/config.yaml`, then
`tests/smoke/verify.sh`. This performs the actual normalization, independent
Oracle/NOP Harbor jobs, publication, and archiving path. The verifier reads raw
trial results and JUnit reports and requires every task to pass; it does not
accept report flags alone. It rejects stale reports left by earlier runs.
Docker unavailability returns **77 (skipped)**, not a passing smoke result.
Other failures return a nonzero status.

The smoke config is copied from production, with `source.type: tasks` and
`source.path: tests/fixtures/tasks`. Like a normal run, it replaces the
configured verified task pool and writes runtime evidence under `artifacts/`.
These are **hand-authored offline fixtures**. This smoke does not claim live
StackOverflow collection, paid LLM generation, or a successful Tracer agent
trajectory; those require separate live evidence. The resulting Harbor task
pool is suitable for that downstream Tracer check.

Set `TERMINAL_CURATOR_TEST_PYTHON` to use another Python 3.11+ environment with
`tests/requirements.txt` installed. The runner otherwise checks the block's
test environment, runtime environment, then `python3`.
