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
# Step failures accumulate here and get passed to the health check, so one
# email covers both "a step broke" and "the data looks thin".
notes=""

note() {
    echo "run_daily: $1"
    notes="${notes}${1}"$'\n'
    failed=1
}

if ! /usr/bin/python3 run.py now; then
    note "scan failed — continued with whatever data was collected"
fi

if ! zsh scheduling/deploy_dashboard.sh; then
    note "dashboard deploy failed"
fi

# Export agent-friendly JSON into the private aeo-data repo
if ! /usr/bin/python3 src/export_data.py; then
    note "export_data.py failed"
fi

# Weekly database backup, Sundays. The raw response history lives only on this
# machine (data/ is gitignored) and can't be rebuilt from the JSON exports, so a
# gzipped dump rides the aeo-data push. Same filename every week, so git keeps
# one rolling blob instead of 52. Only overwrites the good copy once the dump
# has succeeded and is non-empty — a failed dump leaves last week's intact.
if [ "$(date +%u)" -eq 7 ]; then
    tmp_dump=$(mktemp)
    if /usr/bin/sqlite3 data/results.db .dump > "$tmp_dump" && [ -s "$tmp_dump" ]; then
        gzip -9 -c "$tmp_dump" > /Users/paula/aeo-data/results-weekly.sql.gz
        echo "run_daily: weekly db backup written ($(du -h /Users/paula/aeo-data/results-weekly.sql.gz | cut -f1))"
    else
        note "weekly db backup failed — dump errored or was empty"
    fi
    rm -f "$tmp_dump"
fi

# Commit and push whatever changed in the private data repo
if cd /Users/paula/aeo-data; then
    git add -A
    if git diff --cached --quiet; then
        echo "run_daily: no data changes to commit"
    elif git commit -q -m "Data export $(date +%Y-%m-%d)" && git push -q origin main; then
        echo "run_daily: data exported and pushed"
    else
        note "data commit/push failed"
    fi
else
    note "/Users/paula/aeo-data not found"
fi

# Health check last: emails if a step failed OR the day's coverage is thin.
# Partial collection is what went unnoticed for 18 days, and it looks identical
# to success from an exit code alone.
cd "$TRACKER_DIR" || exit 1
if ! printf '%s' "$notes" | /usr/bin/python3 src/health_check.py --alert; then
    failed=1
fi

exit $failed
