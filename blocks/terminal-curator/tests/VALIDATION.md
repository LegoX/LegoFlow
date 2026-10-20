# Terminal Curator integration validation

Validated on 2026-10-20 on one Docker-capable CPU node, with local source,
configuration, plugin, documentation, and privacy checks on the development
checkout. No PR, dataset publication, or GPU training was launched.

## Runtime and pins

| Component | Version |
| --- | --- |
| Terminal-Lego | `1a2a6048a0d568891407ed3c6a441c995f4491b0` |
| Harbor, shared with Tracer and Evaluator | `73fd120e731217b38f31667932ea26a28cdd4fff` |
| LegoFlow-Trace-Crafter | `5e9575a63f59b704c98776399a9502443918e356` |
| Claude Code runtime | `2.1.118` |
| LiteLLM proxy | `1.83.14`, Python 3.13 |
| Verifier | `pytest==8.4.2`, installed during image build |
| Docker / Compose on the test node | `29.5.2` / `5.1.4` |
| Requested generation and rollout model | `claude-opus-5-5` |

Credentials were supplied through private runtime environment files, outside
the repository. The Stack Exchange information endpoint returned HTTP 200;
the LLM catalog contained the requested model and an actual chat completion
succeeded. Live collection retrieved the accepted answers for questions
[59838](https://stackoverflow.com/questions/59838) and
[59895](https://stackoverflow.com/questions/59895).

The node's existing registry authentication and default Docker build network
were unusable. Tests used a private `DOCKER_CONFIG` and an isolated Buildx
builder with host networking. Existing daemon configuration and other
workloads were left intact.

## Real tasks and consumers

| Task | Source | Oracle | Fresh NOP | Tracer | Evaluator |
| --- | --- | --- | --- | --- | --- |
| `terminal-jsonl-summary` | Hand-authored offline fixture | 1 | 0 | 1 | Not run |
| `terminal-csv-reconcile` | Hand-authored offline fixture | 1 | 0 | 0 | Not run |
| `terminal-so-59838` | Live Stack Overflow seed, generated and reviewed candidate | 1 | 0 | 1 | 1 |
| `terminal-so-59895` | Live Stack Overflow seed, generated and reviewed candidate | 1 | 0 | 1 | 1 |

Every accepted trial had a real, nonempty pytest JUnit report, no Harbor
exception, and the expected reward. NOP failures came from assertions rather
than missing interpreters, collection errors, or missing rewards. Reference
solution exit codes were checked independently.

The final two Stack Overflow tasks were published together after semantic
review and another fresh Oracle/NOP run. The same immutable batch was then
consumed through Tracer's manifest staging and Evaluator's native local
registry. Each task has the same Harbor checksum across Oracle, NOP, Tracer,
and Evaluator. Both consumers solved both tasks. Successful Tracer traces produced
two LF records; Trainer's existing `scripts/dataprep.sh` separately consumed
that Harbor job and produced two nonempty LF records with valid message roles
and assistant endings. GPU training was not part of this CPU validation.

The CSV fixture's earlier rollout chose the wrong timestamp winner. Its
reward remained zero and its trace was excluded from successful SFT data.
Earlier generated-task and rollout failures remain in ignored evidence; they
were not relabeled as successful final runs.

## Failures exercised and fixed

- Raw generated reference scripts sometimes returned the requested program
  body instead of creating the program. The adapter now specifies Harbor's
  argument-free installer/action-script contract.
- A generated Ubuntu image omitted Python. Normalization now bakes an
  isolated verifier runtime into supported images and preserves the final
  Dockerfile user; grading does not install dependencies over the network.
- A comment mentioning `solve.sh` was incorrectly rejected. AST inspection
  permits comments/docstrings while rejecting executable references.
- A generated task promised symlink handling that its reference did not
  implement. Independent task review corrected the reference and added
  absolute/relative symlink-chain, spaces, and relocation behavior checks.
  The old reference fails the added symlink checks; the corrected one passes.
- Task review made required output labels, log contents, pre-existing output
  state, and verifier-writable fixture areas explicit. Existing outcome
  assertions were retained; unsupported source-code spelling checks were
  replaced by stronger behavior checks. Source attribution, original
  instruction hashes, and changes are recorded in candidate provenance.
- Upstream generation can return a test candidate after every model review
  rejects it. The adapter now requires an explicit boolean approval for the
  exact returned test code. Missing, malformed, or negative reviews cannot
  approve the fallback. Two real final task-review requests also passed.
- Repeated publication keeps immutable batches so existing staged inputs
  retain their contents. Missing/empty/unsafe manifests fail before staging.
- Failure/interruption archives preserve the effective redacted overlay and
  actual repository SHAs. Concurrent rejected starts cannot overwrite live
  state; forced exits terminate stale running state. Empty gitlink directories
  cannot masquerade as their parent repository's commit.
- Cleanup preserves published versions by default, and stop targets only
  recorded processes and exact current-run Compose project labels.

## Automated checks

- Terminal block: **116 passed** on the Docker node, including native pinned
  Harbor registry resolution. A separate source-only tree without `.git`,
  vendored checkouts, artifacts, or business credentials passed **115**
  offline checks and explicitly skipped the one unavailable Harbor-runtime
  check.
- Root: **85 pytest checks passed**. The complete root runner reported
  **14 PASS, 1 SKIP, 0 FAIL**; its skip was the existing Curator upstream
  network check, not a terminal test.
- Root configuration/dependency validation: **0 FAIL**. Existing orphan-output
  warnings remain visible. Default SWE and registered-benchmark configuration
  selections were preserved and covered by regressions.
- Claude plugin/marketplace validation, four skill validations, Codex wrapper
  parity/discovery checks, and contract-generator idempotence passed.
- Root documentation production build passed, including mounted terminal
  pages, links, and search data.
- Terminal dashboard HTTP/live-state, escaping, secret allowlists, and HTML
  output passed. Tracer rendered real terminal-job data; Evaluator indexed
  real terminal registry jobs.
- Python compilation, Bash syntax, staged whitespace checks, repository
  privacy checks, and exact supplied-credential scans passed.

The CSV fixture's status field is quoted to retain its intentional surrounding
spaces without trailing whitespace in Git. Parsed CSV rows are identical,
its input-integrity checksum is updated, and fixture controls are rechecked.

## Reproduction and evidence

From a configured Docker execution host:

```bash
bash blocks/terminal-curator/scripts/setup.sh
uv pip install --python blocks/terminal-curator/artifacts/env/terminal-curator/bin/python \
  -r blocks/terminal-curator/tests/requirements.txt
bash blocks/terminal-curator/tests/run.sh
bash blocks/terminal-curator/tests/run.sh --with-smoke
```

The fixture smoke performs no Stack Exchange or generation-LLM requests.
For a live batch, supply endpoint credentials through the documented
environment variables, set finite source limits in `config.yaml`, and use
the block's check/run skills. Follow the Tracer and Evaluator configuration
examples in the block documentation for their separate runs.

Machine-readable reports, candidate provenance, raw trials/JUnit, trajectories,
LF samples, and semantic-review comparisons are kept under ignored
`artifacts/`. They are deliberately excluded from the code PR. CI workflows
are configured but were not remotely dispatched before PR approval.
