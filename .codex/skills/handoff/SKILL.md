---
name: handoff
description: >-
  Compact the current conversation into a handoff document so a fresh session can
  pick the work up. Use when the user wants to hand off, wrap up, or start a new
  session that continues prior work. Existing artifacts — PRDs, plans, ADRs, issues,
  commits, diffs — are referenced by path or URL, never copied in.
metadata:
  short-description: Write a continuity doc for the next session
---

# Handoff

> Derived from [Matt Pocock's handoff](https://github.com/mattpocock/skills/tree/main/skills/productivity/handoff) (MIT). Matt's no-duplication discipline preserved verbatim; the tooling and references are additions. See [references/companion_tooling.md](references/companion_tooling.md).

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save it to a path produced by `mktemp -t handoff-XXXXXX.md` (read the file before you write to it).

Suggest the skills to be used, if any, by the next session.

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

If the user described what the next session will focus on, tailor the document to it — a deploy handoff and a debug handoff emphasise different sections.

## Sections

- **Goal of next session** — what success looks like, in 2-3 sentences. The next agent should be able to read this alone and know what to do.
- **State of play** — done / in progress / blocking, each with a concrete path, branch or SHA.
- **Open decisions** — what the next agent must decide, with options and the user's current lean.
- **Skills to use** — a concrete list, not "consider using".
- **Artifacts** — paths and URLs only. This is where the no-duplication rule is most often broken.

## Workflow

```sh
python scripts/handoff_template_generator.py --next-focus "<what the next session is for>" --mktemp
python scripts/skill_recommender.py <handoff.md>      # fills the "Skills to use" section
python scripts/artifact_deduplicator.py <handoff.md>  # pre-flight: flags content that should be a link
```

The deduplicator is advisory — it surfaces candidates, it does not rewrite. More than three findings usually means the draft is copying an artifact instead of pointing at it.

A good handoff is 50-100 lines. Past that, the length itself is the signal that something is being duplicated.

## References

Read these when the situation calls for them, not by default:

- [references/handoff_structure.md](references/handoff_structure.md) — what belongs in each section, and how to tailor to the next-session focus
- [references/deduplication_discipline.md](references/deduplication_discipline.md) — the five duplication categories and what to replace them with
- [references/next_session_skill_matching.md](references/next_session_skill_matching.md) — the signal-to-skill mapping the recommender uses
- [references/companion_tooling.md](references/companion_tooling.md) — what each script does
