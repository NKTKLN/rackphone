#!/usr/bin/env python3
"""skill_recommender.py — Recommend which skills the next session should use.

Stdlib-only. Scans a handoff document for content signals and matches them to
skills in this repo. Output: ranked recommendations with rationale.

Signal-to-skill mapping (the skills actually installed in this repo, plus Codex's
own system skills):

  - "commit" / "conventional commit" / "git flow" / "branch"  -> commit
  - "docstring" / "Google style" / "D417" / "document this"   -> docstrings
  - "code quality" / "refactor" / "complexity" / "karpathy"   -> karpathy-coder
  - "grill" / "stress-test" / "decision tree"                 -> grill-me
  - "less tokens" / "be brief" / "caveman"                    -> caveman
  - "UI" / "design" / "typography" / "layout"                 -> frontend-design
  - "handoff" / "next session" / "continue the work"          -> handoff
  - "review" / "diff review" / "regression"                   -> review-agent (Codex system skill)
  - "write a skill" / "new skill"                             -> skill-creator (Codex system skill)

NO LLM CALLS. Pattern-match recommender.

Usage:
    python skill_recommender.py                          # uses embedded sample
    python skill_recommender.py path/to/handoff.md
    python skill_recommender.py handoff.md --output json
"""

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Tuple


# (keyword pattern, skill name, rationale template)
SKILL_SIGNALS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(commit|conventional\s+commit|commitizen|git\s?flow|feature\s+branch|merge\s+back)\b", re.IGNORECASE),
     "commit",
     "Next session lands changes in git; commit enforces the single-line type(scope): subject format and the Git Flow branch rules."),
    (re.compile(r"\b(docstring|google\s+style|D4\d\d|pydocstyle|document\s+(this|the\s+code))\b", re.IGNORECASE),
     "docstrings",
     "Next session writes or fixes docstrings; docstrings carries this repo's convention plus the DS001-DS003 checker."),
    (re.compile(r"\b(karpathy|complexity|refactor|code\s+quality|overcomplicat|over[-\s]?engineer)\b", re.IGNORECASE),
     "karpathy-coder",
     "Next session involves code-quality discipline; karpathy-coder runs complexity_checker, diff_surgeon, assumption_linter and goal_verifier."),
    (re.compile(r"\b(grill|stress[-\s]?test|interrog|decision\s+tree|open\s+decisions?)\b", re.IGNORECASE),
     "grill-me",
     "Next session must resolve open design decisions; grill-me walks the branches one forcing question at a time."),
    (re.compile(r"\b(caveman|less\s+tokens|be\s+brief|compress)\b", re.IGNORECASE),
     "caveman",
     "Next session benefits from token-compressed replies; caveman applies the compression rules consistently."),
    (re.compile(r"\b(UI|UX|visual\s+design|typography|palette|layout|landing\s+page)\b", re.IGNORECASE),
     "frontend-design",
     "Next session shapes an interface; frontend-design pushes for deliberate type, color and layout choices instead of templated defaults."),
    (re.compile(r"\b(review|regression|defect|inspect\s+the\s+diff)\b", re.IGNORECASE),
     "review-agent",
     "Next session reviews a change; review-agent is Codex's read-only defect-first review skill."),
    (re.compile(r"\b(write|create|author)\s+(a\s+)?skill\b", re.IGNORECASE),
     "skill-creator",
     "Next session authors or edits a skill; skill-creator is Codex's own skill-authoring guidance."),
    (re.compile(r"\b(handoff|next\s+session|continue\s+the\s+work)\b", re.IGNORECASE),
     "handoff",
     "Next session may need to be handed off again; handoff produces continuity docs."),
]


SAMPLE_HANDOFF = """# Handoff — ship Matt Pocock skills batch

## Goal of next session
Open PR for caveman + grill-me + handoff skills. Validate against the karpathy-coder
gate (complexity checker + assumption linter) and the write-a-skill 6-item checklist.
Investigate any CI failures.

## State of play
Done: write-a-skill plugin shipped + merged.
In progress: 3 sibling skills built locally, need PR.
Blocking: nothing.

## Open decisions
- Should we caveman the PR description?
- Re-grill the plan before opening PR?

## Artifacts
- Branch: feature/pocock-productivity-batch
- Issues: none
- PRD: documentation/implementation/pocock-derived-skills-plan.md
"""


def recommend(text: str) -> List[Dict[str, Any]]:
    hits: Dict[str, Dict[str, Any]] = {}
    for pattern, skill, rationale in SKILL_SIGNALS:
        matches = pattern.findall(text)
        if not matches:
            continue
        if skill not in hits:
            hits[skill] = {"skill": skill, "rationale": rationale, "hits": 0, "matched_keywords": []}
        hits[skill]["hits"] += len(matches)
        hits[skill]["matched_keywords"].extend(
            m if isinstance(m, str) else " ".join(filter(None, m))
            for m in matches[:3]
        )
    ranked = sorted(hits.values(), key=lambda x: -x["hits"])
    return ranked


def analyze(text: str) -> Dict[str, Any]:
    recommendations = recommend(text)
    return {
        "total_skills_recommended": len(recommendations),
        "recommendations": recommendations,
    }


def render_text(r: Dict[str, Any]) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("SKILL RECOMMENDER FOR NEXT SESSION")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"Skills recommended: {r['total_skills_recommended']}")
    lines.append("")
    if not r["recommendations"]:
        lines.append("No skill signals detected. Next session may not need a specific skill.")
    else:
        for i, rec in enumerate(r["recommendations"], start=1):
            kw_preview = ", ".join(rec["matched_keywords"][:3])
            lines.append(f"  [{i}] {rec['skill']:30s} (matched {rec['hits']}x: {kw_preview})")
            lines.append(f"      {rec['rationale']}")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recommend skills for the next session based on handoff content.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("path", nargs="?", help="Path to handoff markdown (uses embedded sample if omitted)")
    parser.add_argument("--output", choices=("text", "json"), default="text", help="Output format")
    args = parser.parse_args()

    if args.path:
        try:
            with open(args.path, "r", encoding="utf-8") as f:
                text = f.read()
        except (IOError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    else:
        text = SAMPLE_HANDOFF

    result = analyze(text)
    if args.output == "json":
        print(json.dumps(result, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
