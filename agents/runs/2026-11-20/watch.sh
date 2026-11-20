#!/bin/sh
# Lead watcher: exits when a new report appears or a worker pane is not "working".
cd /d/projects/versioned-rag/agents/runs/2026-11-20
seen="$(ls reports | grep -E "^W[0-9]+.md$")"
while true; do
  sleep 30
  now="$(ls reports | grep -E "^W[0-9]+.md$")"
  [ "$now" != "$seen" ] && { echo "NEW REPORT: $now"; exit 0; }
  out=$(herdr agent list 2>/dev/null | python -c "
import json,sys
d=json.load(sys.stdin)['result']['agents']
for a in d:
    if a['pane_id'] in ('w2M:p1','w2N:p1','w2P:p1') and a['agent_status']=='blocked':
        print(a['pane_id'],a['agent_status'])
")
  [ -n "$out" ] && { echo "STATUS: $out"; exit 0; }
done
