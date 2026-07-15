#!/bin/bash
# BetterStack log check — run before every bug investigation
set -euo pipefail

MINUTES="${1:-60}"
FROM_TS=$(date -d "$MINUTES minutes ago" -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -v-${MINUTES}M -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null)

echo "=== BetterStack Logs (last ${MINUTES}m, since $FROM_TS) ==="
echo ""

for ENTRY in "VPS:km6Lo7gBoX8NQrWU9Embi4Gv" "GPU:m2z2NcGy3L8qXNW59Nm679tm" "Docker:E2DKVqGkdgQQcK3imQt7veCq"; do
  NAME="${ENTRY%%:*}"
  TOKEN="${ENTRY##*:}"
  echo "--- $NAME ---"
  
  RESP=$(curl -s "https://telemetry.betterstack.com/api/v1/query" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"level:error OR level:warn\",\"from\":\"$FROM_TS\",\"limit\":10}" 2>/dev/null)
  
  echo "$RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    entries = d.get('data', [])
    print(f'  {len(entries)} log entries')
    if not entries:
        print('  No error/warn logs')
    for e in entries[:5]:
        msg = str(e.get('message', e.get('msg', '')))[:120]
        ts = str(e.get('dt', e.get('timestamp', '?')))[:19]
        print(f'  [{ts}] {msg}')
except:
    print(f'  Raw: {sys.stdin.read()[:200]}')
" 2>/dev/null || echo "  (parse error)"
  echo ""
done
