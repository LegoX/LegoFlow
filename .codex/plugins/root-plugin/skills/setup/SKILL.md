---
name: setup
description: Prepare LegoFlow block environments and repositories after the user confirms the check results.
---

# Set Up LegoFlow

Run setup only after presenting the check summary and receiving explicit confirmation. Use `./bin/legoflow setup <block>`, preserve pinned submodule commits, and report environment paths and any network or credential failures.
## LegoFlow Command Convention

The canonical command for this skill is `/root:setup`. The shared CLI accepts the same command as `./bin/legoflow /root:setup` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
