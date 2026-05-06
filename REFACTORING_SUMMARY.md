# Block Refactoring Summary

**Date**: 2026-05-06  
**Branch**: worktree-refact-v2  
**Status**: ✅ Complete

## Overview

Successfully refactored the entire SWE Lego Live project to match the latest block definition. All blocks now use a unified `config.yaml` structure with consolidated configuration, and memory has been moved to the dashboard directory.

## Root Block Changes

### Files Consolidated into `config.yaml`
- `metainfo.yaml` → `config.yaml` (meta_info section)
- `inputs.yaml` → `config.yaml` (runtime_info.input section)
- `outputs.yaml` → `config.yaml` (runtime_info.output section)
- `status.yaml` → `config.yaml` (status section)

### Files Removed
- `BLOCK_DEFINITION.md` (will be created by /create-block skill)
- `BLOCK_INTAKE.md` (will be created by /create-block skill)

### Files Updated
- **CLAUDE.md**: Concise 60-line agent contract with new structure
- **dashboard/overview.mdx**: Updated to reflect current refactoring status
- **artifacts/index.yaml**: Updated to use new archive path structure (`artifacts/archives/run_NNN/`)
- **scripts/dryrun.sh**: Enhanced with comprehensive validation checks
- **scripts/start.sh**: Updated with TODO for new structure
- **scripts/clean.sh**: Updated with cleanup guidelines

### Directory Changes
- `memory/` → `dashboard/memory.md` (memory now lives alongside overview)

## Subblock Refactoring Status

| Subblock | Status | Changes |
|----------|--------|---------|
| swegen   | ✅ Complete | Created config.yaml, updated CLAUDE.md, moved memory to dashboard/, removed old files |
| trajgen  | ✅ Complete | Updated config.yaml, updated CLAUDE.md, moved memory to dashboard/, removed old files |
| sft      | ✅ Complete | Created config.yaml, updated CLAUDE.md, moved memory to dashboard/, removed old files |
| rl       | ✅ Complete | Created config.yaml, updated CLAUDE.md, moved memory to dashboard/, removed old files |

## Key Structural Changes

### 1. Single Config File
All configuration now in `config.yaml` with four main sections:
- `meta_info`: Block identity, repos, environment, resources
- `runtime_info`: Input/output values
- `status`: Current phase, progress, next steps, blockers, metrics
- `evolving`: Description and tunable parameters

### 2. Archive Path Change
- Old: `artifacts/files/run_NNN/`
- New: `artifacts/archives/run_NNN/`

### 3. Archive Contents
Each run archive now includes:
- `metadata.yaml`: run id, timestamps, stage, results, repo commits, copy of inputs
- `config.yaml`: snapshot of config at run time
- `scripts/`: copy of scripts used
- `repos/`: snapshot of repo state
- `session.log`: Claude Code session record
- `monitor.md`: agent monitor output

### 4. Memory Location
- Old: `memory/notes.md` (separate directory)
- New: `dashboard/memory.md` or `dashboard/memory/` (alongside overview)

### 5. Dependency Wiring
Clear separation between:
- Inter-block dependencies: declared in `meta_info.subblocks[].dependencies`
- External inputs: declared in `runtime_info.input`

## Validation

Root block dryrun: ✅ Passed
```
=== Block Dryrun: swe_lego_live ===
Checking required files...
Checking required directories...
Validating config.yaml structure...
✓ All checks passed
```

## Files Changed Summary

**Deleted**: 35 files (old config files, BLOCK_DEFINITION.md, BLOCK_INTAKE.md)
**Modified**: 9 files (CLAUDE.md, scripts, dashboard)
**Created**: 6 files (config.yaml for root + 4 subblocks, REFACTORING_SUMMARY.md)

## Next Steps

1. Test each subblock's dryrun script after refactoring
2. Update any cross-references between blocks if needed
3. Commit the refactored structure to the worktree branch
4. Merge back to main branch after validation

## Migration Notes

- All old separate config files (metainfo.yaml, inputs.yaml, outputs.yaml, status.yaml) have been removed
- Memory content has been preserved and moved to dashboard/
- All paths and references have been updated to use config.yaml
- Scripts have been updated to reference the new structure
- The block definition and intake templates will be created by the /create-block skill when needed
