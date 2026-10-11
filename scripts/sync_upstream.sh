#!/usr/bin/env bash
# Merge an upstream commit while retaining the fork's exact workflow set.
# Genuine product conflicts require review; additions keep their fork paths.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <upstream-ref>" >&2
  exit 2
fi
UPSTREAM=$(git rev-parse --verify --end-of-options "$1^{commit}")
BEFORE=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain)" ] || git rev-parse --verify MERGE_HEAD >/dev/null 2>&1; then
  echo "Sync requires a clean checkout with no merge in progress" >&2
  exit 1
fi

abort_on_error() {
  status=$?
  if [ "$status" -ne 0 ] && git rev-parse --verify MERGE_HEAD >/dev/null 2>&1; then
    git merge --abort
  fi
  exit "$status"
}
trap abort_on_error EXIT

# --no-ff keeps a reviewable merge commit even when upstream is ahead. In
# particular, restoring fork workflows must never amend an upstream commit.
# Directory rename inference otherwise relocates fork-only documentation into
# website/docs/developer-guide and breaks the fork's generator and README links.
if ! git -c merge.directoryRenames=false merge --no-commit --no-ff "$UPSTREAM"; then
  while IFS= read -r -d '' path; do
    case "$path" in
      .github/workflows/*) ;;
      *) echo "Unresolved upstream conflict requires review: $path" >&2; exit 1 ;;
    esac
  done < <(git diff --name-only --diff-filter=U -z)
  # A non-conflict merge failure must not be mistaken for successful resolution.
  git rev-parse --verify MERGE_HEAD >/dev/null
fi

if ! git rev-parse --verify MERGE_HEAD >/dev/null 2>&1; then
  echo "Already synchronized"
  exit 0
fi

# Remove upstream-only workflows as well as resolving changed/deleted ones.
# Restoring the entire original subtree also preserves tracked file modes.
git rm -r -f --ignore-unmatch -- .github/workflows
if [ -n "$(git ls-tree "$BEFORE" -- .github/workflows)" ]; then
  git checkout "$BEFORE" -- .github/workflows
fi
# Reference-contract fixtures are data, not deployed Actions. Keep their exact
# upstream provenance current; disappearance of an enumerated workflow fails closed.
if [ -f tests/ci/fixtures/upstream-workflows/manifest.json ]; then
  python3 scripts/refresh_upstream_workflow_fixtures.py "$UPSTREAM"
  git add -- tests/ci/fixtures/upstream-workflows
fi
git commit --no-edit
