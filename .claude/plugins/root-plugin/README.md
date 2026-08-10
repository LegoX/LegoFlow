# block — a Claude Code plugin for block-structured projects

This plugin packages the **block** convention used by [LegoFlow](https://github.com/SWE-Lego/SWE-Lego-Live) into five slash commands you can use in any Claude Code session.

A **block** is the basic collaboration unit in a block-structured project: a self-describing directory containing a `config.yaml` (identity, I/O, status), a `CLAUDE.md` (agent contract), `scripts/{start,dryrun,clean}.sh`, `artifacts/`, and a `blocks/` tree of children. See `resources/BLOCK_DEFINITION.md` (bundled) for the full specification.

## Commands

| Command | What it does |
| --- | --- |
| `/root:create` | Interview-driven scaffolding of a new block — produces the full directory tree per `BLOCK_DEFINITION.md`. Accepts a filled-in `BLOCK_INTAKE.md` or a chat description. |
| `/root:check` | Recursively sanity-check every block at and beneath the current directory: `config.yaml` schema, `runtime_info.input` completeness, inter-block dependency resolution, repo pin matches, environment + remote-resource reachability, and live availability of every OpenAI-compatible LLM endpoint declared in any block's input (probes `GET /models` — no chat completion calls). Read-only; reports every failure in one pass. |
| `/root:run` | Resolve and preflight the selected block, then execute that block's `scripts/start.sh` directly (locally, or in a tmux+SSH session if `meta_info.resources.ip` is set). Archives the run on completion. |
| `/root:setup` | One-shot bootstrap for the root block tree — verify shared tooling, ensure the root `config.yaml` matches the contract, and (on confirmation) recurse into each block's own `:setup` skill in order. Idempotent. |
| `/root:dashboard` | Open (or start) the unified dashboard aggregating live state — last run id, status, duration, key metrics — across every block into one webui. Read-only. |

## Install

### Local development

```bash
claude --plugin-dir "$PWD/.claude/plugins/root-plugin"
```

In the session, `/help` will list `/root:create`, `/root:check`, `/root:run`, `/root:setup`, and `/root:dashboard` under the `root` plugin namespace. Run `/reload-plugins` after editing any file in the plugin.

### Via a marketplace

Once published, install from a marketplace with `/plugin install root@<marketplace>`. See the [Claude Code plugin docs](https://code.claude.com/docs/en/plugins) for marketplace setup.

## Layout

```
root-plugin/
├── .claude-plugin/plugin.json   # manifest (name: "root")
├── README.md
├── resources/                   # bundled docs and templates, used by both commands
│   ├── BLOCK_DEFINITION.md      # authoritative block spec
│   ├── BLOCK_INTAKE.md          # intake form for /root:create
│   ├── config.template.yaml     # empty config schema
│   └── example_block/           # full sft_training reference block
└── skills/
    ├── create/SKILL.md          # /root:create
    ├── check/SKILL.md           # /root:check
    ├── run/SKILL.md             # /root:run
    ├── setup/SKILL.md           # /root:setup
    └── dashboard/SKILL.md       # /root:dashboard
```

The plugin is self-contained — it does not require the LegoFlow repo to be present.

## Quick usage

```text
# Scaffold a new block
$ cd ~/projects/my-pipeline
/root:create
> "Make a leaf block called data_curation with one input dataset_url, one output curated_dir, no children, no remote resources."

# Fill in the inputs the scaffold left as null
$ $EDITOR data_curation/config.yaml

# Sanity-check the whole tree (configs, deps, remotes, API keys) before running
$ cd data_curation
/root:check

# Preflight + run
/root:run
```
