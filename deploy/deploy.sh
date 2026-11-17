#!/usr/bin/env bash
# Deploy one commit of the ticket agent on this instance.
#
# Usage: deploy/deploy.sh <full-40-char-git-sha>
#
# Called by CD (SSM Run Command, as root) on every merge to main, and once by the boot
# script on a fresh instance. Installing Docker, cloning the repo, and fetching the
# Qdrant data are the boot script's job, not this one's.
#
# Reads the secrets from SSM Parameter Store (/ticket-agent/<NAME>, SecureString) with
# the instance's IAM role, writes them to .env (mode 600), pulls the image tagged with
# the SHA, restarts the stack, and waits until /health answers.
#
# Exit codes: 0 deployed and healthy, 1 failed (the old stack keeps running unless the
# new one was already started), 2 bad usage.
#
# Never add `set -x`: it would print the secrets into the SSM/GitHub log.
set -euo pipefail

SSM_PREFIX="/ticket-agent"
SECRETS=(OPENAI_API_KEY GRAFANA_ADMIN_PASSWORD ORIGIN_SECRET)
HEALTH_URL="http://localhost:8000/health"  # localhost: /health needs no origin secret
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-60}"
HEALTH_INTERVAL_S="${HEALTH_INTERVAL_S:-2}"
LOCK_FILE="${LOCK_FILE:-/tmp/ticket-agent-deploy.lock}"
export AWS_REGION="${AWS_REGION:-us-east-1}"

log() { printf '[deploy] %s\n' "$*"; }
fail() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

# A short SHA or a branch name could resolve to another commit than the image tag.
if [[ $# -ne 1 || ! $1 =~ ^[0-9a-f]{40}$ ]]; then
    printf 'usage: %s <full-40-char-git-sha>\n' "$0" >&2
    exit 2
fi
sha="$1"

# Two quick merges start two deploys: the second waits for the first.
exec 9>"$LOCK_FILE"
flock 9

cd "$(dirname "$0")/.."  # the repo root, whatever the caller's working directory
compose=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

log "checking out $sha"
git fetch --quiet origin
git checkout --quiet --detach "$sha"  # compose files and monitoring config match the image

# Without the index the API starts, finds no collections, and only fails the health
# check, which would hide the cause.
[[ -d data/qdrant_storage ]] || fail "data/qdrant_storage is missing (the boot script fetches it)"

# Build the new .env next to the old one and swap it in only when complete: if any
# SSM read fails, the running stack keeps its old .env.
umask 077
trap 'rm -f .env.tmp' EXIT
: >.env.tmp
for name in "${SECRETS[@]}"; do
    # assigned first: under set -e a failing $(...) inside printf's arguments would be
    # ignored and an empty secret written
    value="$(aws ssm get-parameter --name "$SSM_PREFIX/$name" --with-decryption \
        --query Parameter.Value --output text)" || fail "cannot read $SSM_PREFIX/$name from SSM"
    # single quotes stop compose from interpolating a "$" inside the value
    [[ -n $value && $value != *"'"* && $value != *$'\n'* ]] ||
        fail "$SSM_PREFIX/$name is empty or contains a quote or newline"
    printf "%s='%s'\n" "$name" "$value" >>.env.tmp
done
printf "IMAGE_TAG='%s'\n" "$sha" >>.env.tmp
mv .env.tmp .env
log "wrote .env (${#SECRETS[@]} secrets from SSM, IMAGE_TAG=$sha)"

log "pulling images"
"${compose[@]}" pull --quiet || fail "docker compose pull failed (was the image for $sha pushed?)"
log "starting the stack"
"${compose[@]}" up -d --remove-orphans

log "waiting up to ${HEALTH_TIMEOUT_S}s for $HEALTH_URL"
deadline=$((SECONDS + HEALTH_TIMEOUT_S))
until curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
        "${compose[@]}" logs --tail 100 api >&2 || true
        fail "the API did not become healthy within ${HEALTH_TIMEOUT_S}s"
    fi
    sleep "$HEALTH_INTERVAL_S"
done

docker image prune -f >/dev/null  # old api images are ~760 MB each on a 20 GB disk
log "deployed $sha"
