---
name: ship
description: >-
  Ship the current work in one safe, fixed sequence: stage → verify → commit →
  tidy worktrees → sync → push to the main branch. Use this WHENEVER the user
  says "ship", "ship it", "ship now", "let's ship", "ship this", or otherwise
  asks to commit-and-push / release / "get this onto main" / "push my changes up"
  — even if they don't mention git explicitly. The word "ship" is the cue. A
  verify gate runs first and the whole sequence aborts (no commit, no push) if
  verification fails, so it is safe to invoke liberally.
---

# Ship

A one-word release command. When the user says **ship** (or "ship now", "ship
it", etc.), run the steps below **in this exact order**. The order is the safety
property: nothing gets committed unless verification passes, and nothing gets
pushed unless the local state is sound. Do not reorder, skip, or parallelize.

This skill intentionally commits and pushes to the repo's **main line**
(`main`, or `master` if that's the integration branch). That's what "ship"
means here; the verify gate is the safeguard that makes it safe.

## Guardrails (apply throughout)

- **Verify failure aborts everything** — no commit, no push. Report the output.
- Never use `--no-verify`, never force-push, never bypass commit signing.
- Never auto-resolve rebase/merge conflicts — stop and hand back to the user.
- Never switch branches silently, and never commit a feature branch to main.
- Never remove a worktree that has uncommitted changes or unmerged commits.
- If a step's precondition isn't met, stop and explain — don't improvise around it.

---

## The sequence

### 1. Preflight

Establish where you are and what needs doing before touching anything.

- **In a repo?** `git rev-parse --is-inside-work-tree`. If not, stop and offer
  to `git init` — there's nothing to ship yet.
- **Main line?** Determine the integration branch: prefer `main`; if `main`
  doesn't exist but `master` does, use `master` (call it `MAIN` below). Get the
  current branch with `git branch --show-current`. If you're on a *different*
  (feature) branch, **stop** — shipping a feature branch to main isn't this
  skill's job. Tell the user what branch they're on and let them decide.
- **Remote?** `git remote`. If `origin` exists, `git fetch origin` so the
  ahead/behind comparison is accurate. If there's **no remote**, note it — the
  local steps still run, but the push step will be skipped with instructions.
- **Classify the state** so you know which path to take:
  - Local edits: `git status --porcelain` (non-empty → there are changes to commit).
  - Position vs. upstream (only if an upstream exists):
    `git rev-list --left-right --count @{u}...HEAD` → `behind  ahead`.
  - Resolve to one of:
    - **(A) Local changes present** → full path: stage, verify, commit, then sync + push.
    - **(B) Clean tree but ahead (unpushed commits)** → skip stage/verify/commit; go to worktree cleanup, then sync + push.
    - **(C) Clean and nothing ahead** → nothing to do. Report and stop.

### 2. Stage  *(path A only)*

```
git add -A
```
Stage everything so the verify gate checks exactly what will be committed.

### 3. Verify gate  *(path A only — this is the abort point)*

Run the project's verification and **abort the entire ship if it fails**. The
point is to never publish something broken. Auto-detect the right command:

1. **Node** — if `package.json` exists and defines a `verify` script: run
   `pnpm verify` (or `npm run verify` if pnpm isn't installed). If there's no
   `verify` script, fall back to a `test` (and `lint`) script if present.
2. **Python** — if there's `requirements.txt` / `pyproject.toml` / `*.py`:
   pick the interpreter (`.venv/Scripts/python` on Windows or `.venv/bin/python`
   on macOS/Linux if a venv exists, else `python`/`python3`), then:
   - syntax-check every tracked module: compile the files from
     `git ls-files '*.py'` with `python -m py_compile ...`;
   - if a test suite is present (`pytest` installed and tests exist), run
     `pytest -q`; otherwise do an import smoke-test of the entry points
     (e.g. `python -c "import app, serve"`) to catch import-time breakage.
3. **Other** — honor an obvious project gate if present: `make verify` /
   `make test`, `cargo check`, `go build ./...`, etc.
4. **Nothing found** — don't block the ship just because no verify step exists;
   **warn clearly** that verification was skipped, then continue.

If the gate runs and **fails**, stop here: report the failing output, leave the
changes staged but uncommitted, and do not push.

> This repo (Python/Flask, no test suite) resolves to: compile `app.py` +
> `serve.py` and import-smoke-test them with the `.venv` interpreter.

### 4. Commit  *(path A only)*

Draft a [Conventional Commits](https://www.conventionalcommits.org) message from
the staged diff — read `git diff --cached --stat` and the diff itself to infer
the type, scope, and a concise summary. Add a Co-Authored-By trailer crediting
Claude (use your environment's model identity).

```
git commit -m "<type>(<scope>): <summary>" -m "<optional body>" -m "Co-Authored-By: Claude <model> <noreply@anthropic.com>"
```

Use `feat`, `fix`, `docs`, `refactor`, `chore`, `test`, `perf`, `style`, or
`build`. Keep the subject imperative and under ~72 chars. Don't open an
interactive editor; pass the message via `-m` (or a heredoc for long bodies).

**Examples**
- Edited a SPARQL query + frontend copy → `feat(artwork): add medium to artwork details`
- Pinned deps and added a WSGI entry point → `chore(deploy): add waitress server and requirements`
- Fixed label-less items showing a QID → `fix(details): show "Untitled" when an item has no label`

### 5. Worktree cleanup

Leave the repo tidy, but conservatively.

```
git worktree prune          # drop administrative entries for deleted worktrees
git worktree list           # inspect what remains
```
For each *additional* worktree (not the current one), remove it **only if** it
is both clean (no uncommitted changes) and fully merged into `MAIN`. If it has
uncommitted work or unmerged commits, **leave it** — it may hold work in
progress. Most repos have none here, so this is usually a no-op.

```
git worktree remove <path>  # only for clean, fully-merged worktrees
```

### 6. Sync  *(only if a remote + upstream exist)*

Bring in any remote work before pushing, to avoid a rejected push:

```
git pull --rebase origin <MAIN>
```
- On conflicts: **stop**, report them, and let the user resolve — never
  auto-resolve.
- **If (and only if) the rebase actually pulled in new remote commits**, re-run
  the verify gate (step 3): the merged result is new code that hasn't been
  verified. If nothing came in, skip re-verification — it would be wasted work.

If there's no remote/upstream, skip this step.

### 7. Push

```
git push origin <MAIN>          # add -u on the very first push to set upstream
```
- **No remote configured** → skip the push and tell the user exactly how to
  finish: create a remote repo, then
  `git remote add origin <url> && git push -u origin <MAIN>`.
- A rejected push usually means the remote moved after your fetch — return to
  step 6 (sync), then push again.

### 8. Report

Give a concise summary of what happened, e.g.:
- the commit (short hash + subject), or "no new commit (already clean)";
- verify result (passed / skipped + why);
- worktrees removed (or "none");
- sync result (up to date / rebased N commits);
- push result (pushed to `origin/<MAIN>`, or "skipped — no remote", with the
  exact command to do it).

---

## Worked examples

**Example 1 — local changes, clean push**
State: edits in `app.py` and `static/index.html`; remote up to date.
→ `git add -A` → verify (compile + import smoke-test) passes → commit
`feat(artwork): show creation date and medium` → no extra worktrees →
`git pull --rebase` (nothing new) → `git push origin main`. Report: 1 commit
pushed, verify passed.

**Example 2 — verify fails, ship aborts**
State: edits that break an import.
→ `git add -A` → verify fails (ImportError) → **abort**. Changes remain staged
and uncommitted; nothing pushed. Report the failing output and stop.

**Example 3 — clean tree, ahead 1 (this repo's current shape once a remote exists)**
State: working tree clean, 1 unpushed commit, no remote moves.
→ skip stage/verify/commit (nothing to commit) → prune worktrees (none) →
`git pull --rebase origin main` (no change, so no re-verify) →
`git push origin main`. Report: pushed 1 commit, no new verification needed.

**Example 4 — no remote configured**
State: local changes, but `git remote` is empty (e.g. right after `git init`).
→ stage → verify → commit → worktree cleanup → skip sync → **skip push** and
tell the user: "Committed locally. No remote is set — create one and run
`git remote add origin <url> && git push -u origin main` to publish."

**Example 5 — on a feature branch**
State: current branch is `feature/x`.
→ **stop** at preflight: "You're on `feature/x`, not `main`. Ship pushes the
main line — switch to `main` (or tell me to ship this branch instead) and say
ship again."
