#!/usr/bin/env bash
# Post-deploy smoke check for the live cantastorie site.
# Checks /health, /play and /health/version, and compares the deployed commit with origin/main.
# A commit that differs means a deploy is in progress: report it, open nothing.
# A real failure opens (or comments on) one open bug Issue, and only when GH_TOKEN is set.
# Always exits 0, so it can never block a deploy.
# Env: BASE_URL, REPO, GH_TOKEN, HEAD_SHA (overrides git), DRY_RUN=1 prints instead of creating.
set -u

BASE_URL="${BASE_URL:-https://cantastorie.onrender.com}"
REPO="${REPO:-darth-dodo/cantastorie}"
TITLE="Post-deploy smoke check failing"

head_sha="${HEAD_SHA:-$(git rev-parse origin/main 2>/dev/null || true)}"
problem=""

# GET, not HEAD: /health answers a HEAD request with 405.
status_of() { curl -s --max-time 20 -o /dev/null -w '%{http_code}' "$BASE_URL$1" || true; }

version_body="$(mktemp)"
trap 'rm -f "$version_body"' EXIT
version_status="$(curl -s --max-time 20 -o "$version_body" -w '%{http_code}' "$BASE_URL/health/version" || true)"
echo "/health/version -> ${version_status:-none}"

if [ "$version_status" != "200" ]; then
  problem="$BASE_URL/health/version answered ${version_status:-no response}"
else
  live="$(jq -r '.commit // empty' "$version_body" 2>/dev/null || true)"
  if [ -n "$head_sha" ] && [ "$live" != "$head_sha" ]; then
    echo "deploy in progress: live=${live:-none} main=$head_sha"
    exit 0
  fi
  body="$(curl -s --max-time 20 "$BASE_URL/health" || true)"
  health_status="$(status_of /health)"
  echo "/health -> $health_status"
  if [ "$health_status" != "200" ] || ! printf '%s' "$body" | grep -q '"ok"'; then
    problem="$BASE_URL/health answered $health_status: ${body:-empty}"
  else
    play_status="$(status_of /play)"
    echo "/play -> $play_status"
    [ "$play_status" = "200" ] || problem="$BASE_URL/play answered $play_status"
  fi
fi

if [ -z "$problem" ]; then
  echo "ok: ${live:-unknown} is live and healthy"
  exit 0
fi

echo "problem: $problem"
if [ -z "${GH_TOKEN:-}" ]; then
  echo "no GH_TOKEN, not opening an Issue"
  exit 0
fi

run="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-$REPO}/actions/runs/${GITHUB_RUN_ID:-local}"
text="Commit: ${head_sha:-unknown}
Failure: $problem
Run: $run"

existing="$(gh issue list --repo "$REPO" --label bug --state open \
  --search "Post-deploy smoke check failing in:title" --json number --jq '.[0].number // empty' 2>/dev/null || echo unknown)"
if [ "$existing" = "unknown" ]; then
  echo "could not check for an open Issue, skipping to avoid a duplicate"
elif [ "${DRY_RUN:-}" = "1" ]; then
  printf 'would %s:\n%s\n' "${existing:+comment on #$existing}${existing:-open an Issue titled \"$TITLE\"}" "$text"
elif [ -n "$existing" ]; then
  gh issue comment "$existing" --repo "$REPO" --body "$text" || echo "could not comment on #$existing"
else
  gh issue create --repo "$REPO" --label bug --title "$TITLE" --body "$text" || echo "could not open the Issue"
fi
exit 0
