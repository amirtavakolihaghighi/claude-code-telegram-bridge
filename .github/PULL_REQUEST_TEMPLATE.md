## What this changes

## Why

The problem you hit, not just the diff.

## How you know it works

- [ ] `python -m pytest` passes
- [ ] Added or updated tests for the change
- [ ] Tried it against real chat data with `python -m bridge --dry-run --once`

## Does anything break for existing users?

Say plainly if a setting, command or behaviour changed. This decides whether
the next release is a patch or a minor version.

## Checklist

- [ ] No tokens, IDs, personal paths or real chat content in the diff
- [ ] Tests still use a temporary `claude_home`, never the real one
- [ ] Anything user-facing is reflected in the README
