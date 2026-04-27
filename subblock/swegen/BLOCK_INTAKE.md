# Block Intake Form

Fill in this file and hand it to the agent. The agent will use it to scaffold your block directory.

You only need to answer the questions marked **required**. Leave optional fields as `null` if you don't know yet.

---

## 1. Identity (required)

```yaml
# A short, stable snake_case name. No spaces. e.g. data_curation, sft_training
name: swegen

# A human-readable label. e.g. "Data Curation" (optional)
label: "SWE-gen"

# One sentence: what does this block do?
role: >
  Converts GitHub PRs into verified SWE-Bench tasks across 8 programming languages
  (Python, JavaScript, TypeScript, Go, C, C++, Java, Rust) by running an adaptive
  agent pipeline that monitors per-language success rates and auto-tunes timeout,
  cc_timeout, and n_concurrent parameters.
```

---

## 2. Position in the Tree (required)

```yaml
# Name of the parent block. Write null if this is the root block.
parent: swe_lego_live

# Names of direct child blocks this block owns. Write [] if none (leaf block).
children: []
```

---

## 3. Code Repositories (optional)

List any existing code repos that belong to this block.

```yaml
repos:
  - path: repos/swegen
    role: core pipeline — CLI (swegen create/validate), task generation, validation, scoring, adaptive tuning
```

---

## 4. Input Dependencies (required)

What does this block need before it can run? Include both external inputs (API keys, datasets, human decisions) and inputs produced by other blocks.

```yaml
inputs:
  - name: github_tokens
    val: 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
    description: >
      Comma-separated GitHub API tokens for PR collection via GitHub REST API.
      Multiple tokens enable round-robin rotation to avoid rate limits.
    source_block: null
    required: true

  - name: openai_api_key
    val: sk-5zBX4uRUpPjNG3tcOcOIFzWAB9ST5QaKZuDfWYNj2tFYhDVF
    description: >
      LLM API key for PR evaluation (classify difficulty/complexity) and
      task instruction generation. Auto-mirrored to ANTHROPIC_API_KEY.
    source_block: null
    required: true

  - name: openai_api_base_url
    val: https://az.gptplus5.com/v1
    description: OpenAI-compatible API endpoint URL.
    source_block: null
    required: true

  - name: openai_model
    val: glm-5
    description: Model name for PR evaluation and instruction generation (e.g. glm-5-urg).
    source_block: null
    required: true

  - name: anthropic_model
    description: >
      Model for Claude Code SDK task completion (e.g. claude-sonnet-4-6).
      Used to attempt solving the generated SWE task to verify it is solvable.
    source_block: null
    required: true

  - name: docker
    description: Docker daemon must be running for task environment builds and NOP/Oracle validation.
    source_block: null
    required: true
```

---

## 5. Output Dependencies (required)

What does this block produce? Who consumes it?

```yaml
outputs:
  - name: verified_swe_tasks
    description: >
      Verified SWE tasks across all 8 languages, merged into outputs/{task_id}/.
      Each task contains instruction.md, environment/Dockerfile, environment/bug.patch,
      solution/fix.patch, solution/solve.sh, and tests/test.sh.
    consumer_block: trajgen

  - name: verifiable_tasks_index
    description: >
      Per-language verifiable task ID lists at
      artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt.
      Used by extract_verified_tasks.py to populate outputs/.
    consumer_block: null
```

---

## 6. Resources (optional)

The GPU/CPU node address and mounted storage path assigned for this block. Once the agent starts, it will automatically march to the assigned server.

```yaml
resources:
  ip: 192.168.35.240        # CPU-only; runs on any node with Docker available
  user: root
  pwd: k9#mP2$vL7@nQ4!xZ8
  description: cpu node server for k8s cluster. for swegen task creation. 
```

---

## 7. Monitoring (required)

Tell how the agent how to monitor the progress and status of this block. What are the key results to be presented back to users. According to your requirement, the agent will integrate the `/logs`, `status.yaml`, all related stuff and present them on the html page.

```yaml
Present the following in dashboard/status.mdx (updated each monitoring cycle, every 30 minutes):
  1. Per-language task counts: total_success, total_failed, total_filtered, success_rate, pr_pool_remaining
  2. Current adaptive parameters per language: timeout, cc_timeout, n_concurrent
  3. Recent adaptive decisions from artifacts/logs/adaptive_decisions.jsonl (last 10 entries)
  4. PR pool status: which languages are below the 100-PR threshold and need collection
  5. Total verified tasks across all languages (sum of verifiable_tasks.txt line counts)
  6. Phase and stage from status.yaml: idle / running / blocked
```

---

## 8. Evolving (optional)

Tell the agent what can be evolved, e.g., by adjusting what input parameters in inputs.yaml, and what are the results to be observed. What are the experiences that can be turned into `/memory`.

```yaml
Tunable parameters in inputs.yaml (per language):
  - params.timeout [2400, 5400]: total wall time for one task creation attempt (skeleton + CC session)
    → increase when CC session times out before completing; observe success_rate change next cycle
  - params.cc_timeout [1800, 4200]: Claude Code SDK session time limit
    → increase when CC sessions fail due to timeout; decrease when success_rate is high to save cost
  - params.n_concurrent [4, 32]: number of parallel task creation workers
    → increase when success_rate > 0.4 to maximize throughput; decrease when node is overloaded
  - global.pr_pool_min_threshold [default 100]: trigger threshold for PR replenishment
    → lower for languages with slow PR collection; raise for languages with abundant PRs
  - global.monitor_interval_min [default 30]: how often the agent checks and tunes
    → lower for faster feedback during initial tuning; raise once parameters stabilize

Observations to record in memory/notes.md:
  - Per-language success rate trends over time and which parameter changes caused improvements
  - Which PR sources (repos) produce the most verifiable tasks vs. high filter/fail rates
  - Language-specific failure patterns (e.g. Docker build failures, CC session timeouts, NOP/Oracle divergence)
  - Optimal parameter ranges per language once stabilized
  - Cost per verified task (CC API calls) vs. task quality tradeoff
```
