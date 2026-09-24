#!/usr/bin/env bash
# CommitFlow — PreToolUse (Bash) guard.
# Blocks any `git commit` that carries AI attribution/signature or a commit body.
# Reads the hook event JSON from stdin: {"tool_name":"Bash","tool_input":{"command":"..."}}
# Denies via JSON permissionDecision:"deny" (exit 0), with a legacy "decision" field
# for older Claude Code versions.

set -uo pipefail

INPUT="$(cat)"

if command -v jq >/dev/null 2>&1; then
  CMD="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')"
else
  # Fallback: no jq. Scan the raw payload; slightly coarser but still effective.
  CMD="$INPUT"
fi

# Only act on git commit invocations. Note: matches "git commit" (space), not the
# wrapper "git-commit.sh" (hyphen), so the safe wrapper passes through untouched.
case "$CMD" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

deny() {
  reason="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -n --arg r "$reason" '{
      decision: "block",
      reason: $r,
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $r
      }
    }'
  else
    esc=$(printf '%s' "$reason" | sed 's/"/\\"/g')
    printf '{"decision":"block","reason":"%s","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$esc" "$esc"
  fi
  exit 0
}

# 1) Forbidden AI attribution / signature lines (case-insensitive).
if printf '%s' "$CMD" | grep -qiE 'co-authored-by|generated with|claude code|noreply@anthropic\.com|🤖'; then
  deny "CommitFlow: the commit message contains AI attribution or a signature. Commits are authored as the user — one line, no Co-Authored-By / Generated-with. Rewrite as \"type(scope): subject\" and commit again (ideally via scripts/git-commit.sh)."
fi

# 2) More than one message flag => a commit body. Only single-line messages allowed.
m1=$(printf '%s' "$CMD" | grep -oE '(^|[[:space:]])-m' | wc -l | tr -d ' ')
m2=$(printf '%s' "$CMD" | grep -oE -- '--message' | wc -l | tr -d ' ')
if [ "$(( m1 + m2 ))" -gt 1 ]; then
  deny "CommitFlow: multiple -m/--message flags create a commit body. Commits must be a single \"type(scope): subject\" line — use exactly one -m."
fi

# 3) -F/--file can smuggle a body or signature from a file.
if printf '%s' "$CMD" | grep -qE -- '(^|[[:space:]])(-F|--file)([[:space:]]|=)'; then
  deny "CommitFlow: committing from a file (-F/--file) can include a body or signature. Use a single -m \"type(scope): subject\" instead."
fi

exit 0
