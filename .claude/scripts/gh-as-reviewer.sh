#!/usr/bin/env bash
# Run the GitHub CLI as the Alfred reviewer account, not the account that opened the PR.
# Reads the reviewer's token from ~/.config/alfred/reviewer-token (override with
# ALFRED_REVIEWER_TOKEN_FILE). Works in Git Bash on Windows, macOS and Linux.
# Usage: .claude/scripts/gh-as-reviewer.sh api repos/Breakwell92/Alfred/pulls ...
set -euo pipefail

TOKEN_FILE="${ALFRED_REVIEWER_TOKEN_FILE:-$HOME/.config/alfred/reviewer-token}"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "Reviewer token not found at $TOKEN_FILE." >&2
  echo "Reviews must run on a machine set up with the reviewer account's token (see CLAUDE.md, section 5)." >&2
  exit 2
fi

GH_TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
export GH_TOKEN
exec gh "$@"
