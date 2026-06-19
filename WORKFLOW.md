# Hermes Agent — Noor's working workflow

> **Read this first** if you're touching this repo. The rules below exist
> because a specific footgun in `hermes update` will silently destroy local
> commits, and several agents have hit it.

## The footgun

`hermes update` is designed to bring the local checkout in sync with the
remote. Its internal flow (see `hermes_cli/main.py:8825-8851`) is:

1. `git pull --ff-only origin <branch>` — try a clean fast-forward.
2. **If that fails** (because the local branch has diverged from the
   remote), it runs `git reset --hard origin/<branch>`. This **silently
   discards all local commits**.

The auto-stash only captures **uncommitted working-tree changes**.
**Committed-but-unpushed work is hard-reset away.** This has happened at
least three times in the working history of this repo (see `git reflog`).

## The rule

**Never commit on a branch that `hermes update` is going to reset.**

In this repo we have two remotes:

- `origin` → `github.com/noor-latif/hermes-agent` (your fork)
- `upstream` → `github.com/NousResearch/hermes-agent` (read-only)

The setup expects:

1. `main` is always fast-forward-compatible with `upstream/main`.
2. All real work happens on a **working branch** (current convention:
   `noor-fleet`).
3. The working branch is **pushed to `origin`** before `hermes update` runs.

If you break this rule, the next `hermes update` will hard-reset your work
and the only way to recover is `git reflog` (commits survive there until
garbage collection, typically a few weeks).

## Daily workflow

```bash
cd ~/.hermes/hermes-agent

# Start of work: switch to your working branch
git checkout noor-fleet          # or create a new feature branch

# ... make changes, commit ...
git add -A
git -c user.email=... -c user.name=... commit -m "..."

# Push to fork so the next hermes update doesn't reset it
git push origin noor-fleet
```

To update upstream (regular maintenance, e.g. once a week):

```bash
# Stash any in-progress work first
git stash push -u -m "before-update"

# Run the official update
hermes update

# Fast-forward your local main + working branch to match
git fetch origin
git checkout main
git merge --ff-only origin/main
git checkout noor-fleet
git merge --ff-only main
# (noor-fleet may now have unpushed local commits on top of upstream;
#  push them back to origin)
git push origin noor-fleet

# Restore your work
git stash pop
```

The key invariant: **`origin/main` and `upstream/main` should always be
either identical or `origin/main` should be a fast-forward of
`upstream/main`**. When that's true, `hermes update`'s `git pull
--ff-only origin main` succeeds, no reset, your work survives.

## Branches

| Branch | Purpose | Pushes to |
|---|---|---|
| `main` | Tracking upstream + my merged work. Never commit on this directly. | `origin/main` |
| `noor-fleet` | Working branch. All commits go here. | `origin/noor-fleet` |

**Current main HEAD (2026-06-19):** `6187f54e1 fix(phase2x): batch up the working-tree fixes that were sitting dirty`. This is on top of `00a1b9d1a` (the upstream merge) on top of `3f0e9849e` (upstream tip). Main is "ahead 4" of upstream.

## Recovery if you break the rule

If `hermes update` reset your local main and your work is gone from
`git log`:

```bash
# Your commits are still in the reflog for a few weeks
git reflog | head -20

# Find the SHA of your lost commit (look for "moving to HEAD" entries)
git cherry-pick <sha>

# Push back to your fork
git push origin noor-fleet
```

The reflog entries are your safety net. But don't rely on them — push
often.

## Why not use a "real" branch strategy with PRs?

`hermes update` does not understand feature-branch workflows. It only
checks out and fast-forwards the configured branch. A multi-PR workflow
against your own fork requires either:

- Modifying `hermes update` to support `--branch` properly (out of scope
  for now), or
- Just running on `noor-fleet` directly, fast-forwarding `main` from
  upstream, and pushing back. (What we do today.)

When you have a stable piece of work that's worth sharing, open a PR
from `noor-fleet` against `NousResearch/hermes-agent:main` via
`gh pr create --repo NousResearch/hermes-agent --head
noor-latif:noor-fleet --base main`.

## Dirty working tree — what to do

The dirty working tree in this repo is **not yours** — it's the output
of the auto-orchestrator from previous sessions. The right answer is:

- **Don't commit it on `main` directly** (this is what got it lost in
  the first place).
- **Either** commit it on `noor-fleet` (so it's tracked and pushed),
  **or** drop it via `git checkout -- <files>` if you know it's been
  superseded by upstream.
- If you're not sure, `git diff <file>` to inspect before deciding.

If `hermes update` shows you "Local changes detected — stashing before
update", **read what's in the stash** (`git stash show -p stash@{0}`)
before assuming the changes are noise. The auto-orchestrator's edits
are usually real work, just not properly committed.

## Other operational notes

- The `plugins/model-providers/copilot*.disabled/` directories are
  upstream's. Don't touch them.
- After `hermes update`, the dashboard needs a manual restart:
  `hermes dashboard --port 9119`.
- When in doubt: push to the fork first, then experiment. Cheap insurance
  against the next `hermes update` reset.
