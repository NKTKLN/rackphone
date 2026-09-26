---
name: commit
description: >-
  Create git commits in Conventional Commits / commitizen style and manage Git Flow
  branching in this repo. Use when staging changes, writing a commit message, starting
  a feature/release/hotfix branch, or merging one back. Commit messages are authored as
  the user, in English, as a single `type(scope): subject` line with no body and no
  agent attribution.
metadata:
  short-description: Conventional Commits + Git Flow for this repo
---

# CommitFlow — commits + Git Flow

This skill governs how commits are written and how branches are managed in this repo.
Follow every rule below. Do not skip the confirmation step.

## 1. Commit message format

Every commit message is a single line, in English, following Conventional Commits:

```
type(scope): subject
```

- **No body. No footer. No description.** The commit is exactly one line.
- **Never** add `Co-Authored-By:`, `Generated with ...`, a robot emoji, or any other
  agent/AI attribution. Commits are authored as the user.
- Subject: imperative mood, lowercase first word, no trailing period
  (e.g. `add login form`, not `Added login form.`).
- Keep the whole line under ~72 characters when reasonable.

### Allowed types

Use exactly one of these:

`feat` · `fix` · `docs` · `style` · `refactor` · `perf` · `test` · `build` · `ci` · `chore` · `revert`

Quick guide:
- `feat` — a new user-facing capability
- `fix` — a bug fix
- `docs` — documentation only
- `style` — formatting/whitespace, no logic change
- `refactor` — code change that neither fixes a bug nor adds a feature
- `perf` — performance improvement
- `test` — adding or fixing tests
- `build` — build system, dependencies, packaging
- `ci` — CI/CD configuration
- `chore` — maintenance that doesn't fit above (configs, tooling, housekeeping)
- `revert` — reverts a previous commit

### Scope

Always include a scope, derived from the changed files. Pick the narrowest
meaningful module/area, for example:
- changes under `src/app/auth/**` → `feat(auth): ...`
- changes under `src/app/main.py` → `fix(main): ...`
- changes under `tests/**` → `test(<area under test>): ...`
- CI config → `ci(pipeline): ...`
- root config / repo-wide tooling → `chore(repo): ...`

If a commit genuinely spans several unrelated areas, that's a signal to split it
(see §3), not to invent a broad scope.

### Breaking changes

Since we never write a body, signal a breaking change with `!` before the colon:

```
feat(api)!: drop support for v1 tokens
```

## 2. Staging rules

Before committing, check what is staged with `git status --short`.

- **If files are already staged** → commit only the staged files. Do not add anything else.
- **If nothing is staged** → run `git status` / `git diff --stat`, show the user the
  list of modified and untracked files, and ask which ones to include. Stage only what
  they choose (`git add <paths>`). Never blindly `git add -A`.

## 3. Splitting unrelated changes

Inspect the changes (`git diff` on the relevant files). If the working set contains
several **logically unrelated** changes, split them into separate commits by meaning,
each with its own `type(scope): subject` line.

Workflow for a split:
1. Group the changes into logical units and describe each proposed commit to the user.
2. For each unit: stage exactly that unit's files (`git add <paths>`), show the message,
   wait for confirmation (§4), then commit.
3. Move to the next unit.

If everything belongs to one logical change, make a single commit.

## 4. Confirmation (mandatory)

For every commit:
1. Show the exact commit message you intend to use and the files that will be included.
2. **Wait for the user to confirm.** Do not run `git commit` until they approve.
3. If they edit the message, use their version verbatim (still enforce §1 format).

### How to run the commit

**Use the bundled safe wrapper.** It is the enforcement point for §1: it validates the
message shape, neutralizes a configured `commit.template`, and strips any signature a
`prepare-commit-msg` hook injects after the fact.

```sh
.codex/skills/commit/scripts/git-commit.sh "feat(auth): add login form"
```

Resolve that path relative to the repo root. From inside the skill directory it is
`scripts/git-commit.sh`.

If you commit directly instead, use exactly one `-m` and neutralize the template:

```sh
git -c commit.template= commit -m "feat(auth): add login form"
```

Never pass a second `-m` (that would create a body), and never use `-F`/`--file`
(a file can smuggle in a body or a signature).

The wrapper exits non-zero and explains why when a message is malformed or carries
attribution. Treat that as a quality gate: fix the message and retry, don't work
around it and don't stop to ask the user whether to continue.

### Git config that adds a signature

Some setups inject a signature outside the message string, where it is invisible in
the command you ran:

- **`commit.template`** — only applies when git opens an editor; committing with `-m`
  (as above) ignores it. The wrapper also passes `-c commit.template=` for safety.
- **`prepare-commit-msg` hook** — runs even with `-m` and can append a signature. The
  wrapper catches this: after committing it re-reads `git log -1 --pretty=%B`, and if a
  forbidden line is present it amends with local hooks disabled
  (`git -c core.hooksPath=<empty> commit --amend`) to remove it.

If a signature still survives, inspect the global config:
`git config --get commit.template` and `git config --get core.hooksPath`.

### Commitizen

The repo is configured for commitizen (`[tool.commitizen]` in `pyproject.toml`, with
`task cz-commit` and `task cz-check`), and pre-commit runs the `commitizen` hook at
`commit-msg` plus `commitizen-branch` at `pre-push`. `cz commit` is interactive and
prompts for a body, so prefer the wrapper for agent-driven commits; the `commit-msg`
hook then validates whatever the wrapper produced.

## 5. Git Flow branching

This repo uses **Git Flow**: long-lived `main` and `develop`, plus short-lived
`feature/*`, `release/*`, and `hotfix/*` branches.

- `main` — production-ready code only.
- `develop` — integration branch; day-to-day work merges here.
- `feature/*` — branch **from `develop`**, merge back **into `develop`**.
- `release/*` — branch from `develop`, merge into **both `main` and `develop`**, tag on `main`.
- `hotfix/*` — branch from `main`, merge into **both `main` and `develop`**, tag on `main`.

### Starting work (feature branches)

When the user starts new work while on `main` or `develop`:
1. Generate a short kebab-case slug from the task description and propose the branch
   name `feature/<slug>` (e.g. task "add login form" → `feature/add-login`).
2. On confirmation: `git checkout develop && git pull --ff-only` (if a remote exists),
   then `git checkout -b feature/<slug>`.
3. Commit onto that branch per §1–§4.

Branch name rules: lowercase, words separated by `-`, keep it under ~40 chars, drop
filler words. Never reuse an existing branch name.

### Merging (always ask first)

Creating/switching branches can be done proactively, but **every merge requires explicit
confirmation.** When work on a branch is done and the user wants to integrate it:
1. State the merge plan (source → target, and for release/hotfix the second target + tag).
2. On confirmation, run the merge. Prefer `--no-ff` for feature merges so history stays
   readable. For `release/*` and `hotfix/*`, merge into `main`, create the version tag,
   then merge into `develop` as well.
3. Never force-push and never merge into `main` without confirmation.

## 6. Quick checklist before every commit

- [ ] Message is one line, `type(scope): subject`, English, imperative, no period.
- [ ] Valid type; scope reflects the changed files; `!` if breaking.
- [ ] No body, no emoji, no `Co-Authored-By` / agent attribution.
- [ ] Correct files staged per §2; unrelated changes split per §3.
- [ ] On a proper Git Flow branch; message shown and confirmed by the user.
- [ ] Committed via `scripts/git-commit.sh` (or single `-m` with `commit.template` off).
