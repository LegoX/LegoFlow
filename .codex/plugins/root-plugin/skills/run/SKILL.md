---
name: legoflow-run
description: Run an approved LegoFlow block or the configured end-to-end pipeline and inspect its artifacts.
---

# Run LegoFlow

Resolve the requested block from `config.yaml`, run its existing `scripts/start.sh` inside a named tmux session, and stream only safe progress information. Never skip the check or confirmation step. After completion inspect `artifacts/index.yaml`, logs, summaries, and output paths; report partial or failed stages explicitly.
