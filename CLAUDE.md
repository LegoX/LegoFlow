# SWE-Lego-Live

Agent-driven system for running the full LLM development loop through natural language interaction.

## What this repo is

This is a **WorkSpace repo** — a human-readable state container and orchestration layer. It contains no executable code. All state, configuration, and progress live in YAML and markdown files that both humans and agents can read and write.

## Key documents

- `draft_plan.md` — 4-layer architecture overview (UI → Core Processing → Harbor → K8S)
- `what_is_workspace.md` — the full WorkSpace authoring contract (read this first)
- `program.md` — architecture doc and WorkSpace schema reference

## WorkSpace contract (quick reference)

Every node in this system is a `WorkSpace` with this shape:

```yaml
name: workspace_name
summary: >
  One-line human-readable state: what happened, what matters, what's next.

repo: null          # optional attached code repo
program: program.md # rule document for this node

inputs: {}          # upstream dependencies required before this node can run
config: {}          # editable runtime settings
status: {}          # current execution state
outputs: {}         # structured results
artifacts: []       # archived file/object references
memory: {}          # long-form notes, decisions, reports
subspace: {}        # nested child WorkSpace nodes
```

## Directory layout

Each WorkSpace node follows this layout:

```
<workspace_dir>/
├── associated_repo/ # optional local associated repo directory
├── program.md       # rule document for this node
├── summary.md       # short human-facing state
├── config.yaml
├── status.yaml
├── inputs/
│   └── index.yaml
├── outputs/
│   └── index.yaml
├── artifacts/
│   ├── index.yaml
│   └── files/
├── memory/
│   ├── README.md
│   ├── notes.md
│   ├── decisions.md
│   └── reports/
├── scripts/
│   ├── start.sh
│   ├── status.sh
│   ├── archive.sh
│   └── clean.sh
└── subspace/        # child WorkSpace nodes
```

## Subspaces

```
subspace/
├── SWE-gen/         # generates SWE training instances
├── data_composer/   # aggregates data, manages mixtures and quality
├── training/        # runs SFT/RL training (LF or VeRL)
└── harbor/          # execution bridge (cc, opencode, openhands, etc.)
```

## Agent operating rules

- Read `summary.md` for the current state of any node before acting on it.
- Write structured logical results to `outputs/index.yaml`, not to `summary.md`.
- Keep raw evidence and archived files in `artifacts/`, not inside `outputs/`.
- Write long-form context (decisions, reports, postmortems) to `memory/`.
- Update `status.yaml` to reflect execution state (idle / running / blocked / failed / completed).
- Use `scripts/` as the executable interface for starting, checking, archiving, and cleaning a node.
- Use `subspace` as the only recursive child field name — never `children`.
- Do not invent top-level fields outside the WorkSpace schema.
- When filling in a subspace `program.md`, follow the template in `what_is_workspace.md` lines 279–333.
