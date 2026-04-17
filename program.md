# SWE-Lego-Live Architecture

## Purpose
`SWE-Lego-Live` is an agent-driven system for running the full LLM development loop through natural language interaction. The system should let users describe goals, let the agent coordinate the pipeline, and keep all project and module state human-readable.

This project uses software engineering as the first concrete domain, but the architecture is intended to stay generic.

## Core Idea
The architecture is built around two top-level concepts:

- `AgentOrchestrator`: decides what to do next
- `WorkSpace`: stores what the system knows right now

All other modules are execution modules that read from and write to `WorkSpace`.

## Layers

### UI Layer
- `AgentOrchestrator`
- `WorkSpace`

### Core Layer
- `SWEGen`
- `DataComposer`
- `TrainingEngine`
- `Evaluation`

### Execution Layer
- `HaborExecution`

### Infrastructure Layer
- `InfrastructureK8S`

## WorkSpace
`WorkSpace` is a uniform recursive structure and a reusable module object.

- The outer `WorkSpace` represents the whole project.
- Each submodule owns one child `WorkSpace`.
- Outer and inner nodes use the same shape.
- Different `WorkSpace` nodes may specialize or extend a shared base shape, similar to objects in object-oriented design.
- A `WorkSpace` may optionally attach a concrete code repository or a small repo set.
- A `WorkSpace` may own its own rule document `program.md`.

```yaml
name: workspace_name
summary: human_readable_summary

repo: null
program: program.md

inputs: {}
config: {}
status: {}
outputs: {}
artifacts: []
memory: {}
subspace: {}
```

### Field Meaning
- `repo`: optional attached code repository or repo collection for this `WorkSpace`
- `program`: the rule document that defines how this `WorkSpace` should be interpreted and extended
- `inputs`: dependencies required before the scope can run; in the filesystem this is represented by `inputs/index.yaml` plus optional supporting files
- `config`: editable settings and local policy
- `status`: current runtime state
- `outputs`: structured logical results produced by this scope; in the filesystem this is represented by `outputs/index.yaml` plus optional supporting files
- `summary`: human-facing summary of what happened and what is next
- `artifacts`: archived files or object references stored in a directory parallel to `outputs/`
- `memory`: long-form notes, reports, and decision history behind the short `summary`
- `subspace`: nested `WorkSpace` nodes

## Default WorkSpace Filesystem

```text
<workspace_dir>/
├── associated_repo/
├── program.md
├── summary.md
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
└── subspace/
```

### Filesystem Rules
- `inputs/` and `outputs/` are directories, not single yaml files
- `inputs/index.yaml` is the entry point for dependency inputs
- `outputs/index.yaml` is the entry point for logical results
- `artifacts/` stays parallel to `outputs/`, because result semantics and stored evidence are different concerns
- `scripts/` contains the execution hooks for starting, checking, archiving, and cleaning the current `WorkSpace`
- `associated_repo/` should exist only when the current `WorkSpace` really has an attached repository
- `inputs/index.yaml` and `outputs/index.yaml` should follow the standard templates described in `what_is_workspace.md`

For the detailed `WorkSpace` authoring contract, see `program.md`.

### Root WorkSpace Repo Example
For the root `WorkSpace`, `repo` may point to multiple concrete work areas owned by the project. For example:

```yaml
repo:
  - path: ../SWE-gen-orig
  - path: ../harbor
```

Each attached repo may also own its own local `program.md`.