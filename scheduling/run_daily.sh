#!/bin/zsh
# Daily AEO run: scan + regenerate dashboard, then publish to Cloudflare Pages.
# Called by launchd (com.latitude.aeo-tracker) every day at 13:00.
#
# Deliberately NOT `set -e`. The scan is the fragile step — browser automation
# against chatgpt.com — and a partial scan is still worth publishing and
# exporting. Under `set -e` a scan crash skipped the deploy, the export and the
# aeo-data push, which is why exports silently stopped after 2026-08-06.
# Every step now runs regardless; the script exits non-zero at the end if
# anything failed, so launchd still records it.

TRACKER_DIR="$(dirname "$0")/.."
cd "$TRACKER_DIR" || exit 1

failed=0

if ! /usr/bin/python3 run.py now; then
    echo "run_daily: scan failed — continuing with whatever data was collected"
    failed=1
fi

if ! zsh scheduling/deploy_dashboard.sh; then
    echo "run_daily: dashboard deploy failed"
    failed=1
fi

# Export data for agents and push to the private aeo-data repo
if /usr/bin/python3 src/export_data.py; then
    if cd /Users/paula/aeo-data; then
        git add -A
        if git diff --cached --quiet; then
            echo "run_daily: no data changes to commit"
        elif git commit -q -m "Data export $(date +%Y-%m-%d)" && git push -q origin main; then
            echo "run_daily: data exported and pushed"
        else
            echo "run_daily: data commit/push failed"
            failed=1
        fi
    else
        echo "run_daily: /Users/paula/aeo-data not found"
        failed=1
    fi
else
    echo "run_daily: export_data.py failed"
    failed=1
fi

exit $failed
