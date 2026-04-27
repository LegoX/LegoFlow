# Block Intake Form

Fill in this file and hand it to the agent. The agent will use it to scaffold your block directory.

You only need to answer the questions marked **required**. Leave optional fields as `null` if you don't know yet.

---

## 1. Identity (required)

```yaml
# A short, stable snake_case name. No spaces. e.g. data_curation, sft_training
name: root

# A human-readable label. e.g. "Data Curation" (optional)
label: root

# One sentence: what does this block do?
role: This is the root block, orchestrating the overall process of SWE-Lego-Live, including SWE instance generation, trajectory generation, SFT and RL training. The block will monitor the number of generated SWE instances and trajectories, as well as their priorities for either SFT or RL training.
```

---

## 2. Position in the Tree (required)

```yaml
# Name of the parent block. Write null if this is the root block.
parent: null

# Names of direct child blocks this block owns. Write [] if none (leaf block).
children: 
  - swegen
  - trajgen
  - sft
  - rl
```

---

## 3. Code Repositories (optional)

List any existing code repos that belong to this block.

```yaml
repos:
  - path: null  # relative path or URL to the repo
    role: null  # one phrase: what does this repo do for the block?
```

---

## 4. Input Dependencies (required)

What does this block need before it can run? Include both external inputs (API keys, datasets, human decisions) and inputs produced by other blocks.

```yaml
inputs:
  - name: null         # short snake_case identifier
    description: null  # what is it?
    source_block: null # which block produces this? write null if external/human-provided
    required: false     # true or false
```

---

## 5. Output Dependencies (required)

What does this block produce? Who consumes it?

```yaml
outputs:
  - name: null          # short snake_case identifier
    description: null    # what is it?
    consumer_block: null # which block consumes this? write null if it's a final output
```

---

## 6. Resources (optional)

The GPU/CPU node address and mounted storage path assigned for this block. Once the agent starts, it will automatically march to the assigned server.

```yaml
resources:
  ip: null  # null by default, meanning the current running node
  user: root
  pwd: null
```

---

## 7. Monitoring (required)

Tell how the agent how to monitor the progress and status of this block. What are the key results to be presented back to users. According to youur requirement, the agent will integrate the `/logs`, `status.yaml`, all related stuff and present them on the html page.

```yaml
Summarize the results from subblocks, including an 1) the progress of SWE instance creatoin; 2) the progress of trajectory generation; 3) the data priority from different SFT training; 4) the RL data composition and know-hows 
```

---

## 8. Evolving (optional)

Tell the agent what can be involved, e.g., by adjusting what input parameters in @inputs.yaml, and what are the results to be observed. What are the experiences that can be turned into `/memory`.

```yaml
N/A
```

