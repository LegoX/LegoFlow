# Terminal Curator integration validation

This record includes the native plugin tests below and the earlier independent
two-question implementation review. Docker work used one CPU
execution node. Source, configuration, plugin and browser checks also ran on
the development host. No GPU training was performed.

## Native plugin validation — 2026-10-21

Both native clients installed and discovered all **six LegoFlow plugins and
27 skills** in isolated homes. This catalog check does not claim execution of
the other blocks' workflows. Terminal Curator's four operations were actually
executed separately in both clients:

| Native client | setup | check | run | dashboard |
| --- | --- | --- | --- | --- |
| Claude Code 2.1.118 | Passed | Passed | Passed | Passed |
| Codex 0.163.0 | Passed | Passed | Passed | Passed |

The eight operation receipts contain actual script/tool calls and normal CLI
completion. Both dashboard calls exported HTML, served a loopback preview,
checked `/` and `/api/status`, and stopped that preview. Setup was an actual
idempotent run on the prepared pinned environments, rather than a cold-node
bootstrap. Codex's controller used `gpt-5.4` through the supplied Responses
endpoint; generation and real solving used `claude-opus-5-5`.

Each client generated its own variant of Stack Overflow question 59838 in an
isolated profile. The shared task ID must not be merged into one pool. The
final audited publications passed new Oracle/NOP trials and real Tracer solves:

| Producer controller | Final producer run | Oracle | Fresh NOP | Tracer solved |
| --- | --- | --- | --- | --- |
| claude-code | `20261021T052249Z-82fe76a5` | 16 passed / reward 1 | 15 test failures, 0 errors / reward 0 | 1 |
| codex | `20261021T065659Z-431c2f19` | 10 passed / reward 1 | 9 test failures, 0 errors / reward 0 | 1 |

| Producer controller | Final Harbor task checksum |
| --- | --- |
| claude-code | `acd863c8c7dc8156712efeb84bbba684146bee44bbbf7eed421323b46bc7ab81` |
| codex | `c6621dbec5a04751299c27a577fccff4b7ebfff3462f8fdcd72b01bf37d8c894` |

Both Tracer jobs produced nonempty valid raw trajectories, matching producer
checksums, normal native `end_turn` completion and no Harbor exception. All
verifier cases passed. Default SFT filtering was unchanged; the retained LF
counts were 1 (Claude-produced batch) and 1 (Codex-produced batch).
Evaluator independently solved the Claude-produced final variant with all
16 verifier cases passing and the same checksum, also ending normally.

The native tests exposed and fixed three integration issues:

- Actual Codex discovery returns `$plugin:skill` identifiers. The shared
  generator, 27 entry wrappers, compatibility check and invocation docs now
  use that form.
- Generated test approvals existed only in memory. New candidates retain
  `generation-test-review.json`, bound to the exact reviewed instruction,
  reference and tests; changed inputs cannot reuse an approval. A real
  Codex generation's three hashes and boolean/empty-issues approval matched.
- The first native audit appended results to already published provenance,
  changing its validated identity. That attempt was rejected and retained.
  New publications include `validation.json`; Tracer and Evaluator verify the
  accepted tree, IDs and registry before consumption, including a final
  launch check. Evaluator's final check runs before error suppression for
  Harbor result collection; a changed task during proxy warmup must stop
  launch. A pristine copy of a real accepted batch passed; appending
  one newline to its copied provenance was rejected for the pool, existing
  staging and Evaluator registry. The original remained unchanged.

Semantic review strengthened input-preservation assertions from path presence
to pristine contents/tree/modes, and exercised shell-special names. A real
wrong implementation that rewrites an input file passed the earlier 14 tests
but failed the strengthened integrity assertion; the reference passed all
16. A previous missing-mount control was invalid and was replaced by controls
that record the installed program's hash and actual invocation. Review notes
after publication are separate files, preserving immutable task provenance.
The Codex variant additionally gained an explicit read-only/literal-path
contract, a pre-learner input baseline, safe file-kind hashing and Bash syntax
checks. Its reference no longer trims literal trailing spaces. In four fresh
Docker controls, the fixed reference passed 10/10; the old reference failed
the trailing-space case; an initial-only input mutation passed the old seven
tests and was rejected by the new baseline. The final version was revalidated
and solved again; its earlier seven-test trajectory is retained separately.

Timeouts and interrupted trials remain recorded separately. Codex generation
eventually completed seven successful responses after read-timeout retries;
its retained candidate was resumed through import/QA instead of regenerating
it. No interrupted trial was treated as an accepted NOP result. The private
upload harness also briefly cleared source execution bits; its mode handling
was corrected and source permissions restored. That was a test-environment
issue, not a repository script-permission defect. Additional tmux windows also
needed explicit session-scoped Docker resources; default registry failures and
a rejected host-local-image workaround were preserved. The original public
Dockerfile was restored and independently built before fresh validation.

Current regressions: **292 Terminal tests + 141 root pytest tests = 433 passed**
on the execution node. A clean source export passed **291 Terminal tests** and
explicitly skipped the unavailable native Harbor check. The root runner also
passed 13 shell cases, with its existing upstream-network check skipped after
a bounded stalled probe. This does not satisfy the separate latest-upstream
pin policy described below. Docs built 62 pages; all eight Terminal routes,
navigation, search, synthetic demo and mobile behavior passed local and live
browser checks. Supplied private values were absent from publishable source,
website bytes and the existing PR commit history.

## Earlier two-question review — 2026-10-20

## Runtime and pins

| Component | Version |
| --- | --- |
| Terminal-Lego | `1a2a6048a0d568891407ed3c6a441c995f4491b0` |
| Harbor, shared with Tracer and Evaluator | `73fd120e731217b38f31667932ea26a28cdd4fff` |
| LegoFlow-Trace-Crafter | `5e9575a63f59b704c98776399a9502443918e356` |
| Native Claude Code runtime | `2.1.118` |
| LiteLLM proxy | `1.83.14`, Python 3.13 |
| Verifier | `pytest==8.4.2`, installed at image build time |
| Docker / Compose | `29.5.2` / `5.1.4` |
| Requested generation and solving model | `claude-opus-5-5` |

The Stack Exchange information endpoint and an actual LLM chat completion
both succeeded. Live collection retrieved accepted answers for questions
[59838](https://stackoverflow.com/questions/59838) and
[59895](https://stackoverflow.com/questions/59895). Credentials came from
private environment files; none are needed for the offline fixture smoke.

The execution node's default registry authentication and build network were
unusable. An isolated Buildx builder and private `DOCKER_CONFIG` supplied the
test environment without changing the daemon or unrelated workloads.

## New task generation and semantic review

Generation run `20261020T155447Z-8abb0bc1` produced both candidates. Raw 59838
passed Oracle/NOP; raw 59895 was rejected for an incorrect Oracle reward and
NOP fixture setup errors. Only the passing task entered that initial
publication. Request timeouts and rejected candidates were preserved.
Fourteen successful responses with recorded usage totaled 58,481 tokens;
this is not a count of all HTTP attempts or a cost measurement.

Independent review found behavior that the original tests did not distinguish:

- **59838:** the directory checker's eight cases accepted an incorrect `-e`
  implementation and a fixed-path implementation. Six new cases cover files,
  spaces/relative paths, directory/file/dangling symlinks and a fresh missing
  path. Tests no longer delete an incorrectly present input fixture. The
  instruction, Dockerfile and reference solution are byte-for-byte unchanged.
- **59895:** a supplied executable lacked its execute bit in the image;
  fixtures accessed missing learner output before assertions; and the
  instruction leaked a reference outline/chat residue. A mutable QA copy
  repairs those issues, retains the original assertions and adds seven
  behavior cases. It checks input integrity, output boundaries, relocation,
  symlinks, argument/stderr/exit forwarding and `--where` side effects.
  The reference solution is unchanged.

Eighteen direct Docker control scenarios matched their expected outcomes:

| Control | Result |
| --- | --- |
| 59838 QA reference | 14 tests passed |
| 59838 fresh QA NOP | 13 assertion failures, 0 errors |
| 59895 QA reference | 23 tests passed |
| 59895 fresh QA NOP | 22 assertion failures, 0 errors |
| Raw 59895 reference / NOP | Original failure and 12 NOP setup errors reproduced |
| Two incorrect 59838 implementations | Both passed old tests; both rejected by strengthened tests |
| Four incorrect 59895 launchers | All passed old tests; all rejected by strengthened tests |

Controls used isolated containers without grading-time network access.
Provenance records original file hashes, requirement/test mappings, edits and
control evidence. Neither pinned repository nor an immutable publication was
edited. Generated task code was executed on the Docker node, not the
controller host.

## Fresh publication and consumers

The audited candidates were re-imported through the actual block launcher.
Run `20261020T165745Z-3a383062` verified both in separate fresh Oracle and NOP
environments, then published one immutable batch and matching local registry.
Every accepted result had a nonempty JUnit report, zero pytest errors, the
expected reward and no Harbor exception. NOP required assertion failures.

| Task | Oracle | Fresh NOP | Tracer | Evaluator |
| --- | --- | --- | --- | --- |
| `terminal-so-59838` | 1 | 0 | 1 | 1 |
| `terminal-so-59895` | 1 | 0 | 1 | 1 |

The final Harbor task identities are:

| Task | Harbor checksum |
| --- | --- |
| 59838 | `0fe5eeb9f7f7dcd56343ca187a515402da2574e56b3e4da5bdf2434203273e23` |
| 59895 | `19e76de51e55a1b85caac85dd98b6711d8743783e3e9d0bab598debe6a4b1e3c` |

The second Tracer job solved both tasks with normal native CLI completion
(`end_turn`, `is_error=false`), valid nonempty raw trajectories and all 37
pytest cases passing. Both checksums match the published producer batch.

Default SFT filtering retained **one** LF record. The 59895 trace has one
reasoning-bearing assistant turn out of 15 (6.67%), below the existing
adaptive 20% threshold; it is correctly excluded. The 59838 trace is
classified as fast mode and retained. Role/tool-pair validation passed,
no parser exception occurred, and the threshold was not lowered. Solving
success and eligibility for this training policy are separate measurements.
Evaluator independently solved both tasks using the same native local
registry and checksums, also with normal native CLI completion. Its 37
verifier cases passed. The isolated Trainer CPU `dataprep.sh` consumed the
second Tracer raw job with unchanged default filtering and produced one
valid LF record, byte-identical to Tracer's retained LF output. No failed
first-attempt data was merged. GPU training was not launched.

The first new Tracer attempt solved 59838 but scored zero on 59895: the
solver left a test copy of `launch.sh` inside the declared read-only input
tree. The other 22 tests passed; the input-integrity assertion correctly
rejected the extra file. Its failed trajectory and one-record successful
SFT subset are retained separately. Subsequent validation uses a new job,
the same model and unchanged task bytes, without solution hints.

The two hand-authored smoke fixtures were independently rerun and both
passed Oracle=1/NOP=0. The first implementation's unsuccessful CSV rollout
remains a historical failure; it was never relabeled as successful SFT data.
The new tasks, controls and consumers above use new evidence, not reused
historical trajectories.

## Regression and browser checks

- Terminal suite: **278 passed** with the native pinned Harbor environment.
  Both the controller and a clean source-only export passed **277** and
  explicitly skipped the one unavailable Harbor-runtime check.
- Root suite: **133 pytest checks passed**. The root runner reported
  **14 PASS, 1 SKIP, 0 FAIL**: its PASS count includes the pytest aggregate
  plus 13 shell cases; the skip was the pre-existing upstream network check.
- Additional real Docker proof: a multistage image inherited `USER 1001:1002`;
  normalization installed the verifier while preserving image USER and actual
  process uid/gid. Pytest imported successfully as that user.
- Regression coverage includes malformed model reviews and selectors,
  per-candidate generation failures, output overlap, native Harbor checksum
  binding, empty-directory/permission fingerprints, atomic publication,
  frozen config/code archives, concurrent launches/archives/cleanup, stale
  PID protection, dead-worker sidecar recovery and unsafe setup destinations.
  A worker that exits normally after an error can remove its PID records
  before the EXIT trap: cleanup now still uses the launcher's captured
  identity and reports cleanup failure before archiving.
- Consumer/root coverage includes default SWE behavior, strict terminal
  selection, effective profiles, per-block remote dispatch, immutable owned
  staging and native terminal registry binding. Existing unowned staging is
  refused, including under overwrite. Isolated smoke profiles use new owned
  paths; the existing SWE root smoke's stage sequence is preserved.
- Existing consumer schema, completion-probe, launch/smoke contracts and
  Tracer dashboard regression cases passed. Two empty-table render fragments
  were moved out of f-string expressions to honor Tracer's declared Python
  3.11 minimum without changing the HTML.
- Actual HTTP/API browser checks covered four views, automatic polling,
  filters, task dialogs, keyboard focus, fold/scroll persistence, stale detail
  removal, unknown/failed states, Unicode/C++ tags, themes and mobile widths.
  A real generated-task snapshot correctly showed raw 59895's NOP reward 0
  **as failed evidence** because its JUnit contained setup errors.
- Documentation built **61 static pages**. Seven Terminal Curator routes,
  search, real Fumadocs mounted links, cross-block links and the embedded
  deterministic synthetic demo were checked. Real run snapshots remain
  separate from the synthetic documentation demo.
- YAML error regressions verified that config/template failures do not print
  secret-bearing source lines. Known private values were scanned against
  publishable source and generated website bytes; no matches were found.

The root CI rule `tests/cases/15_curator_submodule_latest.sh` separately
requires SWE Curator's pin to equal upstream `main`. Earlier heads failed
because the base pinned `53faa328fcfc000c258e0d4b47dd609a887fc23a`, while upstream
was `217b6e65a38598c7cef288a200d0716d2b4b6c3a`. Comparing the commits confirmed
exactly one README-only commit, changing three lines and no runtime code.
The gitlink and matching Curator config pin were synchronized to that commit
to fix CI without weakening the policy. A local network skip is not proof
of satisfying it; the updated head's actual CI result is reported in the PR.
Four other block jobs require an available `self-hosted, legoflow-ci` runner;
independent CPU-node testing does not turn queued Actions jobs into passes.

## Reproduction and scope

From a configured execution host:

```bash
bash blocks/terminal-curator/scripts/setup.sh
uv pip install --python blocks/terminal-curator/artifacts/env/terminal-curator/bin/python \
  -r blocks/terminal-curator/tests/requirements.txt
bash blocks/terminal-curator/tests/run.sh
bash blocks/terminal-curator/tests/run.sh --with-smoke
```

The fixture smoke makes no business LLM or Stack Exchange calls. For a live
batch, configure finite source limits and private environment credentials,
then follow the block's check/run skill, including semantic review. Use the
same audited publication for Tracer's manifest and Evaluator's registry.

The tested integration uses one execution node. Native registry task paths
refer to its local immutable batch. Moving to another node requires making
that batch available there and selecting a consistent registry/profile.
This does not establish multi-host artifact replication or GPU-training
correctness. The legacy root smoke remains a separate SWE/GPU chain; it was
not presented as this terminal CPU integration test.

Raw candidates, reports, provenance, control JUnit, consumer trajectories,
LF samples and detailed review notes are retained in ignored artifacts or
private local files and excluded from the Git tree. At the user's request,
the two final passing task trees and projected measured results are shared
as separate review attachments linked from the PR. They preserve the original
task identities and are not part of the master merge. Private profiles,
host-local registries, raw logs and trajectories remain excluded; the normal
Dashboard preview still exposes metadata and verifier evidence only.
