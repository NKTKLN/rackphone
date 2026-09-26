# Enforcement Patterns

How to wire the four principles into this repo's workflow so they're enforced, not just documented.

Effectiveness numbers below are rough field estimates, not measurements.

## Level 1 — Passive (the skill itself)

The skill lives in `.codex/skills/karpathy-coder/`. Codex loads `SKILL.md` when the request matches the description, or when it is named explicitly:

```
$karpathy-coder review the staged changes
```

**Effectiveness:** ~60%. The principles are read and usually followed, but they fade on long tasks — Codex does not carry a skill across turns unless it is re-mentioned.

## Level 2 — Active review (on demand)

Run the checkers before committing. They catch what a reading pass misses.

```sh
cd .codex/skills/karpathy-coder

python scripts/diff_surgeon.py                                   # staged changes
python scripts/complexity_checker.py ../../../src --threshold medium
python scripts/assumption_linter.py <plan.md>                    # before writing code
python scripts/goal_verifier.py <plan.md>
```

**Effectiveness:** ~85%. Catches most violations, but depends on someone remembering to run it.

## Level 3 — Automated gate (pre-commit)

This repo already runs [pre-commit](../../../../.pre-commit-config.yaml) with ruff, gitleaks, uv-lock and commitizen. Add the checkers as local hooks:

```yaml
# .pre-commit-config.yaml
  - repo: local
    hooks:
      - id: karpathy-complexity
        name: Karpathy complexity check
        entry: python .codex/skills/karpathy-coder/scripts/complexity_checker.py
        language: system
        types: [python]
        args: [--threshold, medium]
      - id: karpathy-diff
        name: Karpathy diff surgeon
        entry: python .codex/skills/karpathy-coder/scripts/diff_surgeon.py
        language: system
        always_run: true
        pass_filenames: false
```

Two cautions before adding them:

- `fail_fast: true` is set at the top of the config, so a noisy checker will stop the whole hook chain. Put these last, or drop `fail_fast` first.
- `complexity_checker.py` takes a single target path, not a file list. Wrap it in a small script if you want it to run per changed file.

**Effectiveness:** ~95%, at the cost of friction on every commit.

## Level 4 — CI

There is no workflow directory in this template yet. `task ci` (lint, coverage, build) is
the gate; a separate advisory job keeps the quality gate and the discipline check apart:

```yaml
# .github/workflows/karpathy-review.yml
name: Karpathy Review
on: [pull_request]

jobs:
  karpathy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: Complexity check
        run: |
          python .codex/skills/karpathy-coder/scripts/complexity_checker.py src \
            --threshold medium --json > complexity.json
      - name: Diff noise check
        run: |
          python .codex/skills/karpathy-coder/scripts/diff_surgeon.py \
            --diff origin/${{ github.base_ref }}...HEAD --json > noise.json
      - name: Report
        run: |
          echo "## Karpathy Review" >> $GITHUB_STEP_SUMMARY
          python - <<'PY' >> $GITHUB_STEP_SUMMARY
          import json
          c = json.load(open("complexity.json"))
          n = json.load(open("noise.json"))
          print(f'Complexity: {c["average_score"]}/100 ({c["total_findings"]} findings)')
          print(f'Diff noise: {n["noise_ratio"] * 100:.0f}% ({n["verdict"]})')
          PY
```

The scripts are stdlib-only, so the job needs no dependency install.

## Adoption order

1. **Level 1** for a week. See the principles applied before automating them.
2. **Level 2** when reviewing changes. Run the checkers on anything non-trivial.
3. **Level 3** once the findings have proven useful more often than not.
4. **Level 4** for a repo with several contributors or heavy agent-written code.

**Anti-pattern:** jumping straight to Level 4. The principles are opinionated, and a gate nobody agreed to gets disabled rather than followed.
