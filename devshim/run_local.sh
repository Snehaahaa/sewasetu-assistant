#!/bin/bash
# DEV-ONLY: run the portal + SMS gateway + both MCP servers locally (no Docker), using the psql-based shim.
#   devshim/run_local.sh [start|stop|restart]
R=$(cd "$(dirname "$0")/.." && pwd); PIDS=/tmp/ssp-logs/pids
export PYTHONPATH=$R/devshim DB_HOST=${DB_HOST:-/tmp} DB_PORT=${DB_PORT:-5433} SMS_GATEWAY_URL=http://localhost:8025 \
  UPLOAD_DIR=/tmp/ssp-uploads PUBLIC_BASE_URL=http://localhost:8000 ADMIN_PASSWORD=${ADMIN_PASSWORD:-officer-pass-1} \
  SCHEME_DEADLINE=${SCHEME_DEADLINE:-2027-12-31 23:59} PORTAL_INTERNAL_URL=http://localhost:8000 \
  CITIZEN_MCP_URL=http://localhost:8001/citizen/mcp OFFICER_MCP_URL=http://localhost:8002/officer/mcp
mkdir -p /tmp/ssp-uploads /tmp/ssp-logs
stop() { [ -f $PIDS ] && xargs -r kill < $PIDS 2>/dev/null; rm -f $PIDS; sleep 1; }
start() {
  : > $PIDS
  (cd $R/smsgw && nohup python3 -c "import app;app.app.run(port=8025,threaded=True)" >/tmp/ssp-logs/sms.log 2>&1 & echo $! >> $PIDS)
  (cd $R/app && nohup python3 -c "import app;app.app.run(port=8000,threaded=True)" >/tmp/ssp-logs/portal.log 2>&1 & echo $! >> $PIDS)
  sleep 2
  (cd $R/app && nohup python3 -c "from agent.citizen import app;app.run(port=8001,threaded=True)" >/tmp/ssp-logs/citizen.log 2>&1 & echo $! >> $PIDS)
  (cd $R/app && nohup python3 -c "from agent.officer import app;app.run(port=8002,threaded=True)" >/tmp/ssp-logs/officer.log 2>&1 & echo $! >> $PIDS)
  sleep 3; echo started
}
case "${1:-start}" in stop) stop;; restart) stop; start;; *) start;; esac
