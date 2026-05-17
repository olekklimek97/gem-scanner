#!/usr/bin/env bash
# Daily backup of gem-scanner state.
# Copies the live JSON / NDJSON / SQLite state files into backups/YYYY-MM-DD/
# and prunes anything older than RETENTION_DAYS.
#
# Designed to be run from cron:
#   0 3 * * * /home/ubuntu/gem-scanner/backup_data.sh

set -u  # error on undefined vars; do NOT use -e so a single missing file
        # does not abort the whole backup run

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="${SCRIPT_DIR}/backups"
RETENTION_DAYS=14

DATE_STAMP="$(date +%Y-%m-%d)"
TARGET_DIR="${BACKUP_ROOT}/${DATE_STAMP}"
LOG_FILE="${BACKUP_ROOT}/backup.log"

mkdir -p "${TARGET_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "${LOG_FILE}"
}

log "── Starting backup → ${TARGET_DIR} ──"

# Files to back up (relative to script dir). trade_log.ndjson is preferred;
# trade_log.json is the legacy pre-migration name and may still exist on some hosts.
FILES=(
    "positions.json"
    "trade_log.ndjson"
    "trade_log.json"
    "processed_alerts.json"
    "scanner_history.db"
)

copied=0
missing=0
for f in "${FILES[@]}"; do
    src="${SCRIPT_DIR}/${f}"
    if [[ -f "${src}" ]]; then
        if cp -p "${src}" "${TARGET_DIR}/"; then
            log "  ✓ copied ${f}"
            copied=$((copied + 1))
        else
            log "  ✗ FAILED to copy ${f}"
        fi
    else
        log "  · skipped ${f} (not present)"
        missing=$((missing + 1))
    fi
done

log "Backup complete: ${copied} copied, ${missing} missing"

# ── Retention: prune directories older than RETENTION_DAYS ──
# Only delete directories named YYYY-MM-DD (defensive — never touch unrelated dirs).
pruned=0
while IFS= read -r -d '' old; do
    dir_name="$(basename "${old}")"
    if [[ "${dir_name}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        rm -rf "${old}"
        log "  🗑️  pruned ${dir_name}"
        pruned=$((pruned + 1))
    fi
done < <(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -print0 2>/dev/null)

log "Retention: ${pruned} old backups pruned (kept last ${RETENTION_DAYS} days)"
log "── Done ──"
