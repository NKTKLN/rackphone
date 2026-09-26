# Skill Matching for the Next Session

This reference answers exactly one decision: **which skills should the handoff recommend for the next session, based on what's in the handoff content?**

Pair with `scripts/skill_recommender.py` for automated pattern-match recommendations.

## Matt Pocock's Implicit Rule

> "Suggest the skills to be used, if any, by the next session."
>
> — Matt Pocock, handoff SKILL.md

"If any" — Matt's hedge acknowledges that not every session needs a specific skill. But when one applies, naming it explicitly saves the next agent guesswork.

## Signal-to-Skill Mapping

The recommender matches handoff content keywords to skills. Full mapping:

| Handoff signal | Recommended skill | Why |
|---|---|---|
| "commit", "conventional commit", "commitizen", "git flow", "feature branch" | `commit` | Single-line `type(scope): subject` format + the Git Flow branch rules |
| "docstring", "Google style", "D417", "document this" | `docstrings` | This repo's docstring convention + the DS001-DS003 checker |
| "karpathy", "complexity", "refactor", "code quality", "overcomplicated" | `karpathy-coder` | complexity_checker + diff_surgeon + assumption_linter + goal_verifier |
| "grill", "stress-test", "interrogate", "decision tree", "open decisions" | `grill-me` | Resolving the open decisions the handoff names |
| "less tokens", "be brief", "caveman", "compress" | `caveman` | Token-compressed replies |
| "UI", "UX", "typography", "palette", "layout", "landing page" | `frontend-design` | Deliberate visual choices instead of templated defaults |
| "review", "regression", "defect", "inspect the diff" | `review-agent` | Codex's own read-only, defect-first review skill |
| "write a skill", "new skill", "author a skill" | `skill-creator` | Codex's own skill-authoring guidance |
| "handoff", "next session", "continue the work" | `handoff` | Continuity for the next-next session |

The table is deliberately closed: it lists the seven skills in `.codex/skills/` plus the two Codex system skills worth routing to. Recommending a skill that is not installed wastes the next session's first turn.

## Why Pattern-Match (Not LLM)

The recommender uses deterministic regex matching, not model inference. Reasons:

1. **Speed** — runs in milliseconds, not seconds
2. **Determinism** — same input always produces same recommendation
3. **Auditability** — recommendation logic is grep-able
4. **No API dependency** — stdlib-only; works offline
5. **Sufficient accuracy** — nine skill signals cover the work this repo actually does; rare cases get manual review

When pattern matching misses, the handoff author adds skills manually.

## Ranking Logic

Skills are ranked by total match count across patterns. Logic:

```
1. For each (pattern, skill, rationale) in SKILL_SIGNALS:
2.   matches = pattern.findall(handoff_text)
3.   skill_hits[skill] += len(matches)
4. Sort skills by skill_hits descending
5. Output top N (default: all matches)
```

A skill with 5 hits ranks above one with 2. This isn't perfect — a single high-signal keyword can matter more than 5 weak ones — but it works for handoff-style text where signal density correlates with relevance.

## When Recommender Is Wrong

The recommender's failure modes:

1. **Over-recommendation:** matches on tangential mentions. Fix: re-read recommendations + drop irrelevant ones.
2. **Under-recommendation:** skill is needed but no keywords trigger it. Fix: add skill manually + add the missing pattern to `SKILL_SIGNALS` for future runs.
3. **Same-keyword multiple skills:** "security" could mean ai-security OR cloud-security OR threat-detection. Recommender shows all; user picks.

## Adding New Skills to the Recommender

When a new skill is added to the repo:

1. Identify 2-3 keywords that signal the skill is relevant
2. Add to `SKILL_SIGNALS` in `skill_recommender.py`:
   ```python
   (re.compile(r"\b(keyword1|keyword2)\b", re.IGNORECASE),
    "new-skill-name",
    "Rationale why this skill matters when keyword detected."),
   ```
3. Run the recommender against a known-good handoff to verify expected matches

## The "Skills Section" Pattern in the Handoff

Output format the recommender produces (matches the handoff template):

```markdown
## Skills to use (next session)

- `karpathy-coder` (3 matches: complexity, refactor, karpathy) — code-quality validation before the PR
- `commit` (2 matches: commit, feature branch) — message format + Git Flow rules
- `caveman` (1 match: brief) — token-compressed replies
```

Each line: skill name, match count + keywords, rationale.

## Anti-Patterns

1. **Recommending every skill in the repo** — defeats the purpose; recommend 1-5 skills max
2. **Recommending without rationale** — "use karpathy-coder" without why is unhelpful
3. **Pattern-matching loosely** — single-letter keywords match too much; minimum 4-character patterns
4. **Forgetting to add new skills to recommender** — recommender goes stale fast; update with each new skill

## When This Reference Doesn't Help

- **Cross-domain handoffs** — handoff from engineering to marketing has different skill set; recommender may miss
- **Brand-new skills not yet in registry** — manual recommendation required until added to `SKILL_SIGNALS`
- **Skills outside this repo** — the recommender knows only `.codex/skills/` plus the Codex system skills; a skill installed in `~/.codex/skills` needs a manual entry

---

**Source authorities (non-exhaustive):**

- **Matt Pocock — handoff** (https://github.com/mattpocock/skills/, MIT) — the "suggest skills" rule
- **OpenAI — Codex skills documentation** (https://developers.openai.com/codex/) — descriptions as routing signals (same logic, different domain)
- **Information Retrieval — TF-IDF + BM25 ranking** — frequency-based relevance scoring
- **Recommender systems patterns (Netflix, Amazon)** — collaborative + content-based filtering simplified to keyword match
- **Skill registries in agent frameworks (LangChain, AutoGen, Codex, Claude Code)** — patterns for skill discovery
- **Karpathy, A. — LLM Wiki pattern** — vault → session → skill routing
- **Hyrum's Law** — once a skill is recommended via specific keywords, downstream depends on those mappings; keep them stable
