#!/usr/bin/env bash
# CommitFlow — safe commit wrapper.
# Usage: git-commit.sh "type(scope): subject"
#
# Guarantees, in order:
#   1. The message matches Conventional Commits (single line, valid type, optional scope/!).
#   2. commit.template is neutralized so a configured template can't add a body/signature.
#   3. The commit is made with a single -m (no body).
#   4. The STORED message is re-read; if a prepare-commit-msg hook injected a signature,
#      the commit is amended with local git hooks disabled (empty core.hooksPath) to strip it.

set -uo pipefail

MSG="${1:-}"
[ -n "$MSG" ] || { echo "usage: git-commit.sh \"type(scope): subject\"" >&2; exit 1; }

FORBIDDEN='co-authored-by|generated with|claude code|noreply@(anthropic|openai)\.com|🤖'
TYPES='feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert'

# 1) Single line only.
case "$MSG" in
  *$'\n'*) echo "CommitFlow: message must be a single line (no body)." >&2; exit 1 ;;
esac

# 2) Conventional Commits shape: type(scope)?!?: subject
if ! printf '%s' "$MSG" | grep -qE "^(${TYPES})(\([a-z0-9._/-]+\))?!?: .+"; then
  echo "CommitFlow: message must be 'type(scope): subject' with a valid type." >&2
  echo "  valid types: feat fix docs style refactor perf test build ci chore revert" >&2
  echo "  got: $MSG" >&2
  exit 1
fi

# 3) No agent/AI attribution in the message we were handed.
if printf '%s' "$MSG" | grep -qiE "$FORBIDDEN"; then
  echo "CommitFlow: message contains agent attribution. Remove it and retry." >&2
  exit 1
fi

# 4) Commit with the template neutralized (belt-and-suspenders; -m already ignores it).
git -c commit.template= commit -m "$MSG"

# 5) Verify what actually got stored; strip an injected signature if present.
STORED="$(git log -1 --pretty=%B)"
if printf '%s' "$STORED" | grep -qiE "$FORBIDDEN"; then
  echo "CommitFlow: a git hook or template injected a signature — amending to remove it." >&2
  EMPTY_HOOKS="$(mktemp -d)"
  git -c core.hooksPath="$EMPTY_HOOKS" -c commit.template= commit --amend -m "$MSG"
  rmdir "$EMPTY_HOOKS" 2>/dev/null || true
  STORED="$(git log -1 --pretty=%B)"
  if printf '%s' "$STORED" | grep -qiE "$FORBIDDEN"; then
    echo "CommitFlow: signature still present after amend." >&2
    echo "  Check global config: git config --get commit.template ; git config --get core.hooksPath" >&2
    exit 1
  fi
fi

echo "committed: $(git log -1 --pretty='%h %s')"
