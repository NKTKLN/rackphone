---
name: karpathy-coder
description: >-
  Enforce four coding principles when writing, reviewing, or committing code —
  surface assumptions before coding, keep it simple, make surgical changes, define
  verifiable goals. Use on "review my diff", "check complexity", "am I
  overcomplicating this", "karpathy check", or before a commit. Ships stdlib
  checkers for each principle.
metadata:
  short-description: Four-principle coding discipline with checkers
---

# Karpathy Coder — Active Coding Discipline

Derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls. Not just guidelines — it ships Python checkers that detect each violation.

> "The models make wrong assumptions on your behalf and just run along with them without checking. They don't manage their confusion, don't seek clarifications, don't surface inconsistencies, don't present tradeoffs, don't push back when they should."
>
> "They really like to overcomplicate code and APIs, bloat abstractions, don't clean up dead code... implement a bloated construction over 1000 lines when 100 would do."
>
> "LLMs are exceptionally good at looping until they meet specific goals... Don't tell it what to do, give it success criteria and watch it go."
>
> — Andrej Karpathy

## The four principles

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

**The test:** Would a senior engineer say this is overcomplicated? If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

**The test:** Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

| Instead of... | Transform to... |
|---|---|
| "Add validation" | "Write tests for invalid inputs, then make them pass" |
| "Fix the bug" | "Write a test that reproduces it, then make it pass" |
| "Refactor X" | "Ensure tests pass before and after" |

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

In this repo the verify step is usually a task: `task test`, `task lint`, `task ruff`,
`task typecheck`, or `task ci` for the whole gate.

## The checkers (`scripts/`)

Stdlib-only, one per principle. Run them from this skill's directory.

| Script | Principle | What it detects |
|---|---|---|
| `assumption_linter.py` | 1 | Hidden assumptions in a plan: unasked features, missing clarifications, silent interpretation choices |
| `complexity_checker.py` | 2 | Over-engineering: too many classes, deep nesting, high cyclomatic complexity, unused params, premature abstractions |
| `diff_surgeon.py` | 3 | Diff noise: lines that don't trace to the stated goal — comment churn, style drift, drive-by refactors |
| `goal_verifier.py` | 4 | Weak success criteria: vague plans without verifiable checks, missing assertions |

```sh
python scripts/assumption_linter.py plan.md
python scripts/complexity_checker.py ../../../src --threshold medium
python scripts/diff_surgeon.py                     # staged changes by default
python scripts/goal_verifier.py plan.md
```

Every script takes `--json`. `complexity_checker.py` takes `--threshold {relaxed,medium,strict}` and `--ext`; `diff_surgeon.py` takes `--diff <range>` or `--file <patch>`.

`expected_outputs/` holds a known-good JSON result per script — compare against it if a script's behaviour looks wrong after an edit.

## Running the review

There is no separate sub-agent here: read the diff and apply all four principles yourself, backed by the checkers. A full pass before a commit:

```sh
python scripts/diff_surgeon.py --json
python scripts/complexity_checker.py <changed dirs> --threshold medium --json
```

Then report per principle: what the checkers found, what they missed, and the one change you would make. Findings are advisory — a high complexity score on code that is genuinely complex is not automatically a defect.

## When to relax

These principles bias toward **caution over speed**. For trivial tasks (typo fixes, obvious one-liners), use judgment. They matter most on:

- Non-trivial implementations (>20 lines changed)
- Code you don't fully understand
- Multi-step tasks with unclear requirements
- Anything that will be reviewed by humans

## References

Read these when the situation calls for them, not by default:

- [references/karpathy-principles.md](references/karpathy-principles.md) — the source quotes, deeper context, when to relax each principle
- [references/anti-patterns.md](references/anti-patterns.md) — before/after examples across Python, TypeScript, and shell
- [references/enforcement-patterns.md](references/enforcement-patterns.md) — wiring the checkers into pre-commit and CI in this repo
