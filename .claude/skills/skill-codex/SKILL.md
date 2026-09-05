---
name: skill-codex
description: >-
  Delegate a self-contained coding or review task to the Codex CLI (`codex exec`)
  and integrate the result. Use when the user asks to hand work to Codex, wants a
  second model's opinion on a diff, or when a task is large, mechanical and easy to
  verify — a wide rename, a migration across many files, a port, or an independent
  review of the current changes.
---

# skill-codex — delegate to the Codex CLI

Codex runs as a separate agent in its own sandbox. Delegation is worth it only when
the task is **self-contained and cheap to verify**. Everything Codex produces must be
reviewed here before it reaches a commit.

## 1. When to delegate

Delegate when:

- The task is mechanical and wide — a rename across many files, a mechanical API
  migration, generating tests for existing modules.
- You want an independent second opinion on a diff (`codex review`).
- The work is long-running and the result is verifiable by `task ci` or `task check`.

Do **not** delegate when:

- The task needs conversation context Codex cannot see. Codex starts cold; anything
  it needs must be in the prompt or in the repo.
- The change is small enough to just make here.
- The task is exploratory or a design decision. Those stay in this session.

## 2. Preflight

```sh
codex --version
codex login status
```

If `codex` is missing or unauthenticated, say so and do the work here instead. Never
silently fall back without telling the user.

Delegation runs an external agent against the working tree. **Confirm with the user
before the first `codex exec` in a session**, and show the exact prompt you will send.

## 3. Running a task

Non-interactive form, always with an explicit sandbox:

```sh
codex exec --sandbox workspace-write -C "$PWD" \
  -o /tmp/codex-last.md \
  "Rewrite src/app/parser.py to use pathlib instead of os.path. Do not change behaviour. Run: uv run pytest"
```

Flags that matter:

| Flag | Why |
| --- | --- |
| `--sandbox read-only` | analysis and review; Codex cannot touch files |
| `--sandbox workspace-write` | the default for edits — writes limited to the workspace |
| `--sandbox danger-full-access` | never use unless the user explicitly asks |
| `-C <dir>` | pin the working root instead of relying on the shell cwd |
| `-o <file>` | write Codex's final message to a file so you can read it back |
| `--json` | JSONL events, when you need to parse progress rather than read prose |
| `-m <model>` | override the model for one run |

Prefer `read-only` first when you are unsure — let Codex propose, then apply the
change yourself.

## 4. Writing the prompt

Codex has no access to this conversation. A good delegation prompt states:

1. **The goal**, in one sentence.
2. **The boundary** — which files or directories it may touch.
3. **The invariant** — what must not change (public API, behaviour, formatting).
4. **The verification command**, so Codex can check itself. In this repo:
   `uv run pytest`, `uv run ruff check .`, `uv run mypy .`.

Bad: "clean up the parser". Good: "In `src/app/parser.py` only, replace `os.path`
with `pathlib`. Public function signatures must not change. Verify with
`uv run pytest && uv run mypy .`."

## 5. Independent review of the current diff

```sh
codex review --uncommitted
```

Use this to get a second pass over changes made in this session. Treat the findings
as claims, not verdicts: confirm each one against the code before acting, and tell
the user which findings you rejected and why.

## 6. Integrating the result

After any run that wrote files:

```sh
git diff --stat
git diff
```

Then:

1. Read the diff in full. Codex's changes are unreviewed until you have read them.
2. Run the repo's own gate: `task ci` (lint + tests + build), or `uv run pytest` if
   Task is not installed.
3. Reject and redo rather than patching over a diff that went out of scope — a
   scope-creeping delegation is usually a prompt problem, not a code problem.
4. Commit through the `commit` skill. The commit is the user's, whichever agent
   wrote the code; no attribution to Codex or Claude.

If Codex left the tree in a bad state, `git checkout -- <paths>` or
`git stash` before retrying, and tell the user what you discarded.

## 7. Reporting back

Say plainly: what was delegated, which sandbox, what Codex changed, what you
rejected, and whether the gate passed. Never present a Codex result as verified
unless you ran the checks yourself.
