# block — a Claude Code plugin for block-structured projects

This plugin packages the **block** convention used by [SWE-Lego-Live](https://github.com/) into two slash commands you can use in any Claude Code session.

A **block** is the basic collaboration unit in a block-structured project: a self-describing directory containing a `config.yaml` (identity, I/O, status), a `CLAUDE.md` (agent contract), `scripts/{start,dryrun,clean}.sh`, `artifacts/`, and a `subblock/` tree of children. See `references/BLOCK_DEFINITION.md` (bundled) for the full specification.

## Commands

| Command | What it does |
| --- | --- |
| `/block:create` | Interview-driven scaffolding of a new block — produces the full directory tree per `BLOCK_DEFINITION.md`. Accepts a filled-in `BLOCK_INTAKE.md` or a chat description. |
| `/block:run` | Preflight the block in the current working directory: validates `config.yaml`, all `runtime_info.input` values, inter-block dependencies, repos, environment, and `scripts/start.sh`; then executes `start.sh` (locally, or in a tmux+SSH session if `meta_info.resources.ip` is set). Archives the run on completion. |

## Install

### Local development

```bash
claude --plugin-dir /gpufs/haoli/code/block-plugin
```

In the session, `/help` will list `/block:create` and `/block:run` under the `block` plugin namespace. Run `/reload-plugins` after editing any file in the plugin.

### Via a marketplace

Once published, install from a marketplace with `/plugin install block@<marketplace>`. See the [Claude Code plugin docs](https://code.claude.com/docs/en/plugins) for marketplace setup.

## Layout

```
block-plugin/
├── .claude-plugin/plugin.json   # manifest (name: "block")
├── README.md
├── references/                  # bundled docs and templates, used by both commands
│   ├── BLOCK_DEFINITION.md      # authoritative block spec
│   ├── BLOCK_INTAKE.md          # intake form for /block:create
│   ├── config.template.yaml     # empty config schema
│   └── example_block/           # full sft_training reference block
└── skills/
    ├── create/SKILL.md          # /block:create
    └── run/SKILL.md             # /block:run
```

The plugin is self-contained — it does not require the SWE-Lego-Live repo to be present.

## Quick usage

```text
# Scaffold a new block
$ cd ~/projects/my-pipeline
/block:create
> "Make a leaf block called data_curation with one input dataset_url, one output curated_dir, no children, no remote resources."

# Fill in the inputs the scaffold left as null
$ $EDITOR data_curation/config.yaml

# Preflight + run
$ cd data_curation
/block:run
```
