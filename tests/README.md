# Root-block tests

Structural sanity checks for the LegoFactory block tree as a whole. Pure
Python + pyaml, no Docker / GPU / network. Runs in the `root-block` job of
the cloud CI on every PR and push.

For per-block runtime tests (venvs, LLM endpoints, Docker, etc.) see
`subblock/<name>/tests/`.

---

## Quickstart

```bash
pip install pytest pyyaml          # one-time
pytest tests/test_root_block.py -v
```

Takes <1 s. No side effects.

---

## What gets checked

`test_root_block.py` validates that the block tree under `subblock/` is
structurally sound:

| Test | What it asserts |
|---|---|
| `test_subblock_dir_exists` | `subblock/` directory exists at the repo root |
| `test_subblock_present[<name>]` | every expected subblock dir exists (`curator`, `tracer`, `trainer`, `rl`, `evaluator`) |
| `test_config_yaml_parses[<name>]` | each block's `config.yaml` is valid YAML with `meta_info` and `runtime_info` top-level keys |
| `test_meta_info_identity[<name>]` | `meta_info.name` matches the block's directory name, `meta_info.parent` is `lego_factory` |
| `test_uniform_scripts_present[<name>, <script>]` | each block has the four uniform scripts under `scripts/`: `start.sh`, `dryrun.sh`, `clean.sh`, `archive_run.sh` |

With 5 blocks × 4 scripts the matrix yields ~30 individual test cases —
each one runs in well under a second.

---

## When something fails

| You see | What it means | What to do |
|---|---|---|
| `expected subblock dir … not found` | a block dir was deleted or renamed | restore it or update `EXPECTED_SUBBLOCKS` in the test if intentional |
| `missing <path>/config.yaml` | block exists but never got a config | scaffold one via `/root:create` or add by hand |
| `meta_info.name=… does not match dir name` | someone renamed a dir without updating `config.yaml` | edit `config.yaml` to match the dir, or rename the dir back |
| `meta_info.parent=…, expected lego_factory` | block is wired into the wrong tree | fix `meta_info.parent` in `config.yaml` |
| `missing uniform script: …/scripts/start.sh` | one of the four contract scripts is gone | per `BLOCK_DEFINITION.md`, every block must ship `start.sh`, `dryrun.sh`, `clean.sh`, `archive_run.sh` — restore the missing one |

---

## What's NOT checked here

- Whether each `config.yaml` is internally consistent (required runtime
  inputs filled in, dependency wiring resolves, repo pins match, etc.).
  Those are per-block concerns and belong in `subblock/<name>/tests/`.
- Whether scripts actually run. No side effects in this test file.
- Whether referenced submodule commits exist on the remote. Submodules
  themselves are validated in each block's `02_repo_pin*.sh` test.

Cross-block invariants (dependency wiring resolves, no duplicate block
names, every block ships its 4 standard skills, pinned commits agree
across blocks that share a repo) are reasonable future additions if
breakage in those areas becomes a pain point.

---

## Layout

```
tests/
  README.md            (this file)
  test_root_block.py   single pytest module covering everything above
```

Each subblock has its own `tests/` directory with the same shape (README +
shell tests aggregated by `run.sh`).
