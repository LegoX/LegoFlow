# <Block Name>

This file declares that the current directory is a `block`.

For the canonical definition of a block, the field semantics, and the default directory contract, read `what_is_a_block.md`.

## How To Use This File

Use this file as the local template for describing the current block.

This document should not re-explain the global block concept. Instead, it should describe:
- what this block is responsible for
- what it takes as input
- what it produces as output
- what evidence it stores
- how it relates to parent and child blocks
- how to run and validate it

## Block Summary

```md
Name: <block_name>
Type: <root block | child block | leaf block>
Main doc: `docs/main.md`
Definition reference: `what_is_a_block.md`
```

## Functional Positioning

Describe the purpose of this block in 3-8 lines.

Template:

```md
This block is responsible for <primary responsibility>.

It exists to <why this block exists>.

Its boundary is:
- in scope: <what this block owns>
- out of scope: <what this block does not own>
```

## Inputs

Describe the required upstream inputs of this block. Keep the detailed machine-readable structure in `inputs/index.yaml`, and use this section to explain intent.

Template:

```md
This block depends on the following input categories:
- `<input_name_1>`: <why it is needed>
- `<input_name_2>`: <why it is needed>

Input readiness rule:
- this block can start when <condition>
- this block is blocked when <condition>
```

## Outputs

Describe the main logical outputs of this block. Keep the detailed machine-readable structure in `outputs/index.yaml`.

Template:

```md
This block produces:
- `<output_name_1>`: <meaning>
- `<output_name_2>`: <meaning>

Outputs should answer:
- what result was produced
- what decision can be made from it
- what downstream block may consume it
```

## Artifacts And Memory

Explain what kinds of evidence and long-form records this block keeps.

Template:

```md
Artifacts stored by this block include:
- <log files / reports / datasets / exports / traces>

Long-form memory maintained by this block includes:
- <design notes>
- <decision records>
- <postmortems or analysis reports>
```

## Parent And Child Relationships

Describe where this block sits in the larger tree.

Template:

```md
Parent relationship:
- parent block: <name or none>
- this block receives <what comes from parent>
- this block reports back <what goes to parent>

Child relationship:
- child blocks under `subblock/`: <list or none>
- this block delegates <what is delegated downward>
- this block integrates <what is collected upward>
```

## Execution Interface

Describe how this block is operated through `scripts/`.

Template:

```md
Available scripts:
- `scripts/start.sh`: <how the block is started>
- `scripts/dryrun.sh`: <how to perform the default health check>
- `scripts/clean.sh`: <how temporary or generated state is cleaned>

Dryrun expectation:
- `scripts/dryrun.sh` should verify that this block can be read, resolved, and exercised safely without side effects.
```

## Collaboration Rules

Template:

```md
When updating this block:
- read `docs/main.md` first
- use `inputs/index.yaml` and `outputs/index.yaml` for structured state
- use `artifacts/` for raw evidence
- use `memory/` for long-form context
- use `subblock/` for nested child blocks
```

## Example Skeleton

```md
# Data Intake Block

This file declares that the current directory is a `block`.

For the canonical definition of a block, read `what_is_a_block.md`.

## Functional Positioning
This block is responsible for normalizing incoming source material into a stable internal format.

Its boundary is:
- in scope: source ingestion, validation, normalization
- out of scope: downstream execution and final reporting

## Inputs
- `source_catalog`: records of input sources
- `ingest_policy`: rules for validating and accepting inputs

## Outputs
- `normalized_items`: accepted and normalized units
- `ingest_report`: summary of accepted, rejected, and blocked items

## Parent And Child Relationships
- parent block: root pipeline block
- child blocks under `subblock/`: `fetch`, `normalize`
- delegates source retrieval downward and aggregates normalized results upward

## Execution Interface
- `scripts/start.sh`: starts the intake flow
- `scripts/dryrun.sh`: validates config, inputs, and required paths
- `scripts/clean.sh`: removes temporary working files
```
