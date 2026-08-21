# LegoFlow Codex Plugin

This plugin is the Codex-native entry point for LegoFlow. It mirrors the Claude Code root plugin while using Codex skills and the repository's `AGENTS.md` instructions.

The plugin orchestrates existing block scripts instead of replacing them. Every expensive operation follows `check -> confirm -> run`; Codex must not launch a multi-hour rollout or training job without explicit user confirmation.

Install the repository-local marketplace from `.agents/plugins/marketplace.json`, then install `legoflow-root` and the block plugins that are needed for the requested workflow.
