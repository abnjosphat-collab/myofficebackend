#!/usr/bin/env bash
# Rehearses supabase_migration_maintenance_tools.sql on a throwaway local PostgreSQL (never Supabase).
# Needs the PostgreSQL 16 server binaries and a non-root user (it re-executes itself as `postgres` when run as root).
# Usage: scripts/test_maintenance_tools_sql.sh
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  id postgres >/dev/null 2>&1 || { echo "need a 'postgres' user when running as root"; exit 2; }
  cp "$0" /tmp/_wo_tools_test.sh
  cp "$(dirname "$0")/../supabase_migration_maintenance_tools.sql" /tmp/_wo_tools_migration.sql
  chmod 644 /tmp/_wo_tools_test.sh /tmp/_wo_tools_migration.sql
  exec su postgres -c "MIGRATION=/tmp/_wo_tools_migration.sql bash /tmp/_wo_tools_test.sh"
fi

BIN=$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)
MIGRATION=${MIGRATION:-"$(dirname "$0")/../supabase_migration_maintenance_tools.sql"}
DIR=$(mktemp -d)
PORT=54340
trap '"$BIN/pg_ctl" -D "$DIR" -m immediate stop >/dev/null 2>&1 || true; rm -rf "$DIR"' EXIT

"$BIN/initdb" -D "$DIR" -A trust >/dev/null
"$BIN/pg_ctl" -D "$DIR" -o "-p $PORT -k $DIR" -l "$DIR/log" -w start >/dev/null
PSQL=(psql -h "$DIR" -p "$PORT" -d postgres -v ON_ERROR_STOP=1 -q -X)

fail() { echo "FAIL: $1"; exit 1; }
sql()  { "${PSQL[@]}" -At -c "$1"; }
expect_error() {  # expect_error "<sql>" "<fragment of the error>"
  if out=$("${PSQL[@]}" -At -c "$1" 2>&1); then fail "expected an error: $1"; fi
  echo "$out" | grep -qi "$2" || fail "wrong error for [$1]: $out"
}

# Stubs for what Supabase provides and the migration depends on.
"${PSQL[@]}" <<'SQL'
CREATE SCHEMA auth;
CREATE FUNCTION auth.role() RETURNS text LANGUAGE sql AS $$ SELECT 'authenticated'::text $$;
CREATE TABLE public.work_orders (id bigserial PRIMARY KEY, status text);
CREATE TABLE public.leaves (id bigserial PRIMARY KEY, status text, start_date date, end_date date);
INSERT INTO public.work_orders (status) VALUES ('pending'), ('pending');
SQL

"${PSQL[@]}" -f "$MIGRATION"
"${PSQL[@]}" -f "$MIGRATION"
echo "ok  applied twice"

[ "$(sql "select count(*) from pg_indexes where indexname in ('idx_work_order_tools_wo','uq_work_order_tools_register','idx_leaves_status_dates')")" = "3" ] || fail "expected 3 indexes"
echo "ok  indexes present"

sql "insert into work_order_tools (work_order_id, tool_register_number, tool_name) values (1,'PP-UG-0001','Torque wrench'),(1,null,'Big hammer'),(1,null,'Big hammer')" >/dev/null
echo "ok  free-text tools may repeat (no register number)"
expect_error "insert into work_order_tools (work_order_id, tool_register_number, tool_name) values (1,'PP-UG-0001','Torque wrench again')" "unique"
echo "ok  same register tool refused twice on one job"
sql "insert into work_order_tools (work_order_id, tool_register_number, tool_name) values (2,'PP-UG-0001','Torque wrench')" >/dev/null
echo "ok  same tool allowed on another job"
expect_error "insert into work_order_tools (work_order_id, tool_name) values (1,'   ')" "check"
expect_error "insert into work_order_tools (work_order_id, tool_name) values (999,'Ghost')" "foreign key"
echo "ok  blank name and unknown work order refused"

sql "delete from work_orders where id=1" >/dev/null
[ "$(sql "select count(*) from work_order_tools where work_order_id=1")" = "0" ] || fail "tools should go with the work order"
echo "ok  tools cascade with the work order"

echo "ALL PASSED"
