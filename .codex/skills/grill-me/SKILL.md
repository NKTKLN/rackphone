---
name: grill-me
description: >-
  Interview the user relentlessly about a plan or design until reaching shared
  understanding, walking the decision tree one branch and one question at a time
  with a recommended answer for each. Use when they want a plan stress-tested,
  interrogated, or say "grill me". Not for open-ended brainstorming, where forcing
  questions get in the way.
metadata:
  short-description: One-question-at-a-time plan interrogation
---

# Grill Me

> Derived from [Matt Pocock's grill-me](https://github.com/mattpocock/skills/tree/main/skills/productivity/grill-me) (MIT). Matt's interview discipline preserved verbatim; the tooling and references are additions. See [references/companion_tooling.md](references/companion_tooling.md).

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time.

If a question can be answered by exploring the codebase, explore the codebase instead.

## Rules

1. **One question per turn.** Never bundle. A numbered list of questions is a survey, not an interrogation.
2. **Provide a recommended answer with each question.** Defaulting to "what do you think?" is lazy.
3. **Explore the codebase before asking.** If `rg` or reading a file resolves it, do that first — it saves a turn and a wrong assumption.
4. **Walk the tree depth-first.** Finish a branch before opening another.
5. **Track dependencies.** If decision B depends on decision A, ask A first.

## Workflow

1. The user provides a plan or design, or a path to one.
2. Extract the branches: `python scripts/decision_tree_extractor.py <plan.md>`
3. Turn them into ordered forcing questions: `python scripts/question_generator.py <plan.md>`
4. Open a session: `python scripts/grill_session_tracker.py --action start --session <slug> --plan <plan.md>`
5. Walk the tree one question per turn, recording each answer:
   `python scripts/grill_session_tracker.py --action record --session <slug> --question-id <n> --answer "..."`
6. When every branch is resolved, report "shared understanding reached" and print the locked-in decisions.

The session is the reason for the scripts: a real grill spans turns and often days, and the tracker keeps answers out of the context window. For a plan with three or four branches, skip the scripts and just ask.

## Output pattern

Per question turn:

```
Q[i]/[total]: [question]
Recommended answer: [your call + 1-sentence rationale]

(Or: I explored the codebase and found [evidence]. Confirm?)
```

## References

Read these when the situation calls for them, not by default:

- [references/forcing_question_patterns.md](references/forcing_question_patterns.md) — the six forcing-question patterns, and the soft variants to avoid
- [references/when_to_stop_grilling.md](references/when_to_stop_grilling.md) — what "shared understanding" means operationally, and when to stop early
- [references/companion_tooling.md](references/companion_tooling.md) — what each script does and where sessions are stored
