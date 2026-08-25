# LegoFlow Codex Plugin

This plugin is the Codex-native entry point for LegoFlow. It mirrors the Claude Code root plugin while using Codex skills and the repository's `AGENTS.md` instructions.

The plugin orchestrates existing block scripts instead of replacing them. Every expensive operation follows `check -> confirm -> run`; Codex must not launch a multi-hour rollout or training job without explicit user confirmation.

Install the repository-local marketplace from `.agents/plugins/marketplace.json`, then install the `root` plugin and the block plugins needed for the requested workflow.

Claude Code exposes the following slash commands:

```text
/root:check
/root:setup
/root:run
/root:dashboard
/root:create
```

Codex invokes the corresponding native skills as `$root-check`, `$root-setup`,
`$root-run`, `$root-dashboard`, and `$root-create`. The shared CLI accepts the
exact Claude-style command as `./bin/legoflow /root:check` when a direct CLI
fallback is preferred.
