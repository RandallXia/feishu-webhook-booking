# Release checklist

## Language

- English: [Release checklist](release-checklist.md)
- Chinese: [发布核对](../release-checklist.md)

## Goal

Before opening the repository to the public, make sure the current dynamic-routing version is ready for release.

## Required checks

- All real tokens, account names, and domain names have been replaced with placeholders
- Sensitive screenshots have been removed or redacted
- `LICENSE` has been added
- `.gitignore` has been added
- `.env.example` and `runtime/feishu-targets.toml.example` match the current config surface
- The README clearly explains the project, runtime modes, and doc entry points
- All document links are clickable and valid
- All deployment examples match the current code
- At least one real verification path has been run
- Annual book switching has been exercised: update external config and restart or reload without rebuilding the image

## Before release

1. Re-check `app/main.py`, `app/config.py`, and `app/target_registry.py` against the docs
2. Confirm the README language links do not break
3. Confirm `runtime/feishu-targets.toml.example` contains placeholders only
4. Confirm no real runtime files were accidentally committed

## Pass criteria

When all items above pass, the repository is ready to publish as an open-source project.
