# Block Intake Form

Fill in this file and hand it to the agent. The agent will scaffold your full block directory from it.

Only the fields marked **required** need answers. Leave optional fields as `null` if you don't know yet.

---

## 1. Identity (required)

```yaml
# A short, stable snake_case name. No spaces. e.g. data_curation, sft_training
name:

# A human-readable label. e.g. "Data Curation" (optional)
label:

# One sentence: what does this block do?
role:

# Where should the block directory be created? (absolute or relative path)
path:
```

---

## 2. Position in the Tree (required)

```yaml
# Name of the parent block. Write null if this is the root block.
parent:

# Names of direct child blocks this block owns. Write [] if none (leaf block).
children: []
```

---

## 3. Code Repositories (optional)

List any existing code repos that belong to this block. Each will be placed under `repos/<name>/` as a git submodule.

```yaml
repos:
  - name:        # short identifier, used as the subdirectory name
    url:         # git remote URL or local path
    commit_id:   # pin to a specific commit, or null for latest
    role:        # one phrase: what does this repo do for the block?
```

---

## 4. Runtime Inputs (required)

What values does this block need before it can run? Include API keys, dataset paths, hyperparameters, and values produced by other blocks. Values produced by a sibling block become entries in the new block's `meta_info.dependencies.from` (`<input.dot.path>: <src>.output.<key>`) — and, since the producer is expected to mirror the edge in its own `dependencies.to`, plan to also add `<output_key>: <this_block>.input.<input.dot.path>` to that sibling's config. Purely external values go straight into `runtime_info.input` (marked `human` until the user fills them).

```yaml
inputs:
  - name:         # short snake_case identifier
    description:  # what is it?
    required:     # true or false
    from:         # optional: <sibling>.output.<key> when wired from another block
```

---

## 5. Runtime Outputs (required)

What does this block produce after a run?

```yaml
outputs:
  - name:         # short snake_case identifier
    description:  # what is it?
```

---

## 6. Resources (optional)

Remote node address and credentials. If set, the agent will SSH into this node and run scripts there.

```yaml
resources:
  ip:    # null means run locally
  user:  root
  pwd:   null
  model: null   # LLM model name if applicable
```

