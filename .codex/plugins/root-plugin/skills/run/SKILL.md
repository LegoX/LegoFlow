---
name: run
description: Run an approved LegoFlow block or the configured end-to-end pipeline and inspect its artifacts.
---

# Run LegoFlow

Resolve the requested block from `config.yaml`, run `./bin/legoflow run [<block>]` inside a named tmux session, and stream only safe progress information. Never skip the check or confirmation step. After completion inspect `artifacts/index.yaml`, logs, summaries, and output paths; report partial or failed stages explicitly.
## LegoFlow Command Convention

The canonical command for this skill is `/root:run`. The shared CLI accepts the same command as `./bin/legoflow /root:run` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
