#!/bin/bash
# Sentry issue check — run before every bug investigation
SENTRY_AUTH_TOKEN="${SENTRY_AUTH_TOKEN:-}"
ORG="novalios"
PERIOD="${1:-24h}"

echo "=== Sentry Issues (last $PERIOD) ==="
echo ""

for PROJECT in luminacast-orchestrator luminacast-gpu-worker elumetra-backend; do
  echo "--- $PROJECT ---"
  RESP=$(curl -s "https://sentry.io/api/0/projects/$ORG/$PROJECT/issues/?statsPeriod=$PERIOD&query=is:unresolved&limit=10" \
    -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" 2>/dev/null)
  
  echo "$RESP" | python3 -c "
import sys, json
try:
    issues = json.load(sys.stdin)
    if isinstance(issues, list):
        if not issues:
            print('  No unresolved issues ✓')
        for i in issues:
            print(f'  [{i[\"shortId\"]}] {i[\"title\"][:100]} ({i[\"count\"]}x, last: {i[\"lastSeen\"][:19]})')
    else:
        detail = issues.get('detail', str(issues)[:200])
        print(f'  API: {detail}')
except Exception as e:
    print(f'  Parse error: {e}')
" 2>/dev/null || echo "  (failed to parse)"
  echo ""
done
