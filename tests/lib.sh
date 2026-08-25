#!/usr/bin/env bash
# Minimal assertion helpers. Kept dependency-free so the module tests run
# anywhere a POSIX shell does, including on the phone itself.
PASS=0; FAIL=0; CURRENT=""

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }
_ok()   { PASS=$((PASS+1)); printf '  \033[32m✓\033[0m %s\n' "$1"; }
_bad()  { FAIL=$((FAIL+1)); printf '  \033[31m✗\033[0m %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2"; }

assert_eq() {
  if [ "$2" = "$3" ]; then _ok "$1"; else _bad "$1" "expected [$3] got [$2]"; fi
}
assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then _ok "$1"; else _bad "$1" "missing: $3"; fi
}
assert_not_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then _bad "$1" "unexpectedly present: $3"; else _ok "$1"; fi
}
assert_matches() {
  if printf '%s' "$2" | grep -qE -- "$3"; then _ok "$1"; else _bad "$1" "no match for: $3"; fi
}
assert_count() {
  actual=$(printf '%s' "$2" | grep -cE -- "$3" || true)
  if [ "$actual" = "$4" ]; then _ok "$1"; else _bad "$1" "expected $4 matches of [$3], got $actual"; fi
}
summary() {
  printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
  [ "$FAIL" -eq 0 ]
}
