#!/bin/bash
set -e
cd /opt/luminacast-omni
SINCE_MINUTES="${1:-15}"
set -a
source .env 2>/dev/null || true
set +a

echo "=== Monitoring check (last ${SINCE_MINUTES} min) ==="
echo

HAS_ERRORS=0

# --- Sentry projects ---
for PROJECT in luminacast-orchestrator luminacast-gpu-worker elumetra-backend elumetra-frontend; do
    echo "[Sentry: $PROJECT]"
    if [ -n "$SENTRY_AUTH_TOKEN" ] && [ -n "$SENTRY_ORG_SLUG" ]; then
        # Sentry statsPeriod only accepts: '', '24h', '14d' — use 24h as default
        SENTRY_PERIOD="24h"
        if [ "$SINCE_MINUTES" -ge 20160 ] 2>/dev/null; then SENTRY_PERIOD="14d"; fi
        RESP=$(curl -sf -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
            "https://sentry.io/api/0/projects/$SENTRY_ORG_SLUG/$PROJECT/issues/?statsPeriod=${SENTRY_PERIOD}&query=is:unresolved" 2>&1) || { echo "  ERROR: Sentry API unreachable or auth failed"; HAS_ERRORS=2; echo; continue; }
        echo "$RESP" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if not data:
        print('  No new unresolved issues')
    else:
        for issue in data[:10]:
            print(f\"  [{issue.get('level','?')}] {issue.get('title','?')} (count: {issue.get('count','?')}, last: {issue.get('lastSeen','?')})\")
            print(f\"    {issue.get('permalink','')}\")
except Exception as e:
    print(f'  Parse error: {e}')
" || echo "  ERROR: Failed to parse Sentry response"
    else
        echo "  SKIPPED: SENTRY_AUTH_TOKEN or SENTRY_ORG_SLUG not set"
    fi
    echo
done

# --- Local Docker logs (VPS) ---
echo "[Docker logs: VPS services (last ${SINCE_MINUTES}min)]"
ERROR_LINES=$(docker compose logs --since="${SINCE_MINUTES}m" 2>&1 | grep -iE "error|exception|traceback|critical" | grep -viE "no error|error_message|error handling|StrictHostKeyChecking" | tail -20)
if [ -z "$ERROR_LINES" ]; then
    echo "  No errors in Docker logs"
else
    echo "$ERROR_LINES" | head -20 | sed 's/^/  /'
    HAS_ERRORS=1
fi
echo

# --- GPU worker logs (HOSTKEY) ---
echo "[GPU worker logs: HOSTKEY (last ${SINCE_MINUTES}min)]"
GPU_ERRORS=$(sshpass -p 'Fh3jGW+X%W' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@194.247.183.12 "cd /opt/gpu-worker && cat nohup.out 2>/dev/null | tail -200 | grep -iE 'error|exception|traceback|critical' | grep -viE 'no error|error_message' | tail -20" 2>/dev/null) || GPU_ERRORS="  ERROR: GPU server unreachable"
if [ -z "$GPU_ERRORS" ]; then
    echo "  No errors in GPU worker logs"
else
    echo "$GPU_ERRORS" | head -20 | sed 's/^/  /'
fi
echo

echo "=== End monitoring check ==="
exit $HAS_ERRORS
