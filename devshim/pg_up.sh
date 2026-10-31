#!/bin/bash
# DEV-ONLY: (re)start the sandbox Postgres used with devshim (data dir /home/pg/data, port 5433).
PGBIN=/usr/lib/postgresql/16/bin
$PGBIN/pg_isready -h /tmp -p 5433 -q && exit 0
rm -f /home/pg/data/postmaster.pid
runuser -u pg -- $PGBIN/pg_ctl -D /home/pg/data -o "-p 5433 -k /tmp" -l /home/pg/pg.log start >/dev/null 2>&1; sleep 3
$PGBIN/pg_isready -h /tmp -p 5433
