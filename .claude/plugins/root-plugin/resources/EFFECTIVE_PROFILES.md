# Effective profiles for root orchestration

Read this before root setup/check/run selects producers or inspects child
configuration. A private profile is a complete config, not a patch merged with
the committed config.

| Block | Environment selector | Fallback |
| --- | --- | --- |
| `tracer` | `TRAJGEN_CONFIG` | `blocks/tracer/config.yaml` |
| `evaluator` | `EVAL_CONFIG` | `blocks/evaluator/config.yaml` |
| `terminal-curator` | `TERMINAL_CURATOR_CONFIG` | `blocks/terminal-curator/config.yaml` |

An explicitly selected profile takes precedence for its block. Otherwise use
the nonempty environment selector, then the fallback. Resolve a relative profile
filename against that block's directory, independently of the shell's current
directory. Keep the absolute local path in that block's environment selector
before dispatching child skills. Do not reset a parent-selected profile, copy it
over committed config.yaml, silently merge another profile, or print its contents.

Use these same effective configs for producer selection, input/output dependency
checks, resource/host selection, setup, child dispatch, and archiving. Root
roster/identity still comes from root config.yaml. The Tracer selector is
implemented by `blocks/tracer/scripts/task_source.py --config <effective-tracer-config>`;
an absent `local_block` defaults to `curator`, while an explicitly invalid value
(including false/null/non-string) is an error for local sources.

For runtime dependency checks, use:

```bash
python3 scripts/validate_config.py --block <block-dir> \
  --config <effective-profile> --effective-profiles
```

`--config` wins for the current block; `--effective-profiles` reads the three
environment selectors for siblings as well. Root active-pipeline checks invoke
this once per selected block; explicitly requested full-tree checks may use
`--root <repo-root> --effective-profiles`. This does not activate unused producers.
The default validator without this flag keeps checking committed production
configs, so CI does not depend on operator environment variables. A whole
`--overlay-dir` smoke config set is a separate mode and cannot be combined with
`--effective-profiles`.

For local checks/launches, retain the selectors in the child process environment.
For remote checks/launches, quote each non-secret profile path and map profiles
inside a local block to the same relative location under the remote block
checkout. The worker enters its `resources.directory`; sibling profiles inside
the same checkout map to sibling block directories. A profile outside its local
block retains its explicit absolute path and must already exist on the remote
host. Forward the corresponding selectors for sibling dependency checks too.
Do not assume local absolute paths inside the local checkout exist remotely.
File synchronization and remote secret/environment provisioning remain separate,
explicitly authorized operations; profile forwarding does not supply credentials.

The canonical `/root:run` dispatches each selected child's run skill. For terminal
tasks, that includes semantic audit of instructions/tests/reference solution,
corrections with provenance, and fresh Oracle/NOP revalidation before downstream
rollout. The legacy root shell helper cannot perform this agent step: it refuses
an implicit terminal producer-to-Tracer chain before generation. Use the root
skill, or explicit `--terminal-curator-only`, the required agent audit, then
`--tracer-only`. This is an agent workflow requirement, not an additional user
approval gate. Existing authorization for the requested source/host/budget remains
valid. A dry-run may print the plan and explains that the real implicit chain is
refused. The SWE helper keeps its existing Python `create_py.sh` launcher.
