#!/bin/zsh
# Publishes dashboard.html to Cloudflare Pages.
# Reads CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, AEO_PAGES_PROJECT from .env.
set -e

TRACKER_DIR="$(dirname "$0")/.."
cd "$TRACKER_DIR"

export $(grep -E '^(CLOUDFLARE_ACCOUNT_ID|CLOUDFLARE_API_TOKEN|AEO_PAGES_PROJECT)=' .env | xargs)

if [ ! -f dashboard.html ]; then
    echo "deploy: dashboard.html not found, skipping"
    exit 1
fi

# Pages serves index.html at the root URL, so deploy a one-file folder
DEPLOY_DIR=$(mktemp -d)
cp dashboard.html "$DEPLOY_DIR/index.html"

# launchd jobs don't get Homebrew's PATH
export PATH="/opt/homebrew/bin:$PATH"

npx --yes wrangler pages deploy "$DEPLOY_DIR" \
    --project-name "$AEO_PAGES_PROJECT" \
    --branch main \
    --commit-dirty=true

rm -rf "$DEPLOY_DIR"
echo "deploy: dashboard published to https://$AEO_PAGES_PROJECT.pages.dev"
