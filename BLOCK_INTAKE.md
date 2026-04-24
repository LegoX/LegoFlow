# Block Intake Form

Fill in this file and hand it to the agent. The agent will use it to scaffold your block directory.

You only need to answer the questions marked **required**. Leave optional fields as `null` if you don't know yet.

---

## 1. Identity (required)

```yaml
# A short, stable snake_case name. No spaces. e.g. data_curation, sft_training
name: 

# A human-readable label. e.g. "Data Curation" (optional)
label: 

# One sentence: what does this block do?
role: 
```

---

## 2. Position in the Tree (required)

```yaml
# Name of the parent block. Write null if this is the root block.
parent: 

# Names of direct child blocks this block owns. Write [] if none (leaf block).
children:
  - 
```

---

## 3. Code Repositories (optional)

List any existing code repos that belong to this block.

```yaml
repos:
  - path:   # relative path or URL to the repo
    role:   # one phrase: what does this repo do for the block?
```

---

## 4. Input Dependencies (required)

What does this block need before it can run? Include both external inputs (API keys, datasets, human decisions) and inputs produced by other blocks.

```yaml
inputs:
  - name:          # short snake_case identifier
    description:   # what is it?
    source_block:  # which block produces this? write null if external/human-provided
    required:      # true or false
```

---

## 5. Output Dependencies (required)

What does this block produce? Who consumes it?

```yaml
outputs:
  - name:           # short snake_case identifier
    description:    # what is it?
    consumer_block: # which block consumes this? write null if it's a final output
```

---

## 6. Resources (optional)

Fill in only what you know. Leave null otherwise.

```yaml
resources:
  cpu:               # e.g. "8 cores" or null
  gpu:               # e.g. "2x A100" or null
  memory:            # e.g. "64GB" or null
  estimated_runtime: # e.g. "~4 hours per run" or null
```

---

## 7. Monitoring (required)

Tell how the agent how to monitor the progress and status of this block. What are the key results to be presented back to users. According to youur requirement, the agent will integrate the `/logs`, `status.yaml`, all related stuff and present them on the html page.

---

## 8. Evolving (optional)

Tell the agent what can be involved, e.g., by adjusting what input parameters in @inputs.yaml, and what are the results to be observed. What are the experiences that can be turned into `/memory`.

