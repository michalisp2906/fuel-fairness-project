#!/data/data/com.termux/files/usr/bin/bash
# run_collection.sh
# POSIX/bash port of run_collection.ps1, for collecting from an Android phone
# via Termux while the Windows PC is off (e.g. travel). Loads credentials from
# .env, runs the Python collector, then commits and pushes new snapshots to
# GitHub. All output is appended to logs/collection.log and printed to the
# console so you can watch a manual run.
#
# The shebang points at Termux's bash. On a normal Linux box, run it with
# `bash run_collection.sh` instead of relying on the shebang.
#
# Mirrors the Windows script's fail-loudly behaviour (2026-07-13 fix): pull
# --rebase before push, and exit non-zero if the pull or push fails so the
# snapshot is retried next run instead of silently going stale.

set -u

# Project root = the directory this script lives in (like $PSScriptRoot).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
LOG_FILE="$PROJECT_ROOT/logs/collection.log"
SCRIPT_PATH="$PROJECT_ROOT/fuel_snapshot.py"
ENV_FILE="$PROJECT_ROOT/.env"

# python: Termux installs a plain `python`. Allow an override via $PYTHON_BIN.
PYTHON_BIN="${PYTHON_BIN:-python}"

mkdir -p "$PROJECT_ROOT/logs"

write_log() {
    ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    line="[$ts] $1"
    printf '%s\n' "$line" >> "$LOG_FILE"
    printf '%s\n' "$line"
}

write_log "=== run started ==="
write_log "root: $PROJECT_ROOT"
write_log ".env exists: $( [ -f "$ENV_FILE" ] && echo True || echo False )"
write_log "python: $PYTHON_BIN"

if [ ! -f "$ENV_FILE" ]; then
    write_log "ERROR: .env not found - aborting"
    exit 1
fi

# Load credentials from .env. Skips blanks and comments; strips surrounding
# whitespace and matching single/double quotes from the value.
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|\#*) continue ;;
    esac
    if printf '%s' "$line" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*='; then
        key="${line%%=*}"
        val="${line#*=}"
        val="$(printf '%s' "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//")"
        export "$key=$val"
    fi
done < "$ENV_FILE"
write_log "credentials loaded"

# Run the Python collector from the project root so data/raw/ lands correctly.
write_log "launching Python collector..."
cd "$PROJECT_ROOT" || { write_log "ERROR: cannot cd to project root"; exit 1; }
python_out="$("$PYTHON_BIN" "$SCRIPT_PATH" 2>&1)"
exit_code=$?
write_log "Python exited with code $exit_code"
printf '%s\n' "$python_out" | while IFS= read -r l; do write_log "  $l"; done

if [ "$exit_code" -ne 0 ]; then
    write_log "ERROR: collector exited $exit_code - skipping commit"
    exit "$exit_code"
fi

# Stage new snapshot files only, then commit and push.
add_out="$(git add "data/raw/" 2>&1)"
[ -n "$add_out" ] && printf '%s\n' "$add_out" | while IFS= read -r l; do write_log "  git add: $l"; done

staged="$(git diff --cached --name-only)"
if [ -z "$staged" ]; then
    write_log "nothing new to commit"
    write_log "=== done ==="
    exit 0
fi

commit_ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
commit_out="$(git commit -m "snapshot: $commit_ts" 2>&1)"
printf '%s\n' "$commit_out" | while IFS= read -r l; do write_log "  git commit: $l"; done

# Integrate remote commits first (the CI bot pushes gold rebuilds to main).
# Snapshot commits touch only data/raw/ and the bot touches only data/gold/,
# so this rebase should never conflict in normal operation.
pull_out="$(git pull --rebase origin main 2>&1)"
pull_rc=$?
printf '%s\n' "$pull_out" | while IFS= read -r l; do write_log "  git pull --rebase: $l"; done
if [ "$pull_rc" -ne 0 ]; then
    abort_out="$(git rebase --abort 2>&1)"
    printf '%s\n' "$abort_out" | while IFS= read -r l; do write_log "  git rebase --abort: $l"; done
    write_log "ERROR: git pull --rebase failed - snapshot committed locally, push skipped, will retry next run"
    exit 1
fi

push_out="$(git push 2>&1)"
push_rc=$?
printf '%s\n' "$push_out" | while IFS= read -r l; do write_log "  git push: $l"; done
if [ "$push_rc" -ne 0 ]; then
    write_log "ERROR: git push failed - snapshot committed locally, will retry next run"
    exit 1
fi

file_count="$(printf '%s\n' "$staged" | grep -c .)"
write_log "pushed $file_count file(s)"
write_log "=== done ==="
