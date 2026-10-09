#!/usr/bin/env bash
# Rehearses supabase_migration_maintenance_lifecycle.sql on a throwaway local PostgreSQL (never Supabase).
# Needs the PostgreSQL 16 server binaries and a non-root user (it re-executes itself as `postgres` when run as root).
# Usage: scripts/test_maintenance_lifecycle_sql.sh
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  id postgres >/dev/null 2>&1 || { echo "need a 'postgres' user when running as root"; exit 2; }
  cp "$0" /tmp/_wo_life_test.sh
  cp "$(dirname "$0")/../supabase_migration_maintenance_lifecycle.sql" /tmp/_wo_life_migration.sql
  chmod 644 /tmp/_wo_life_test.sh /tmp/_wo_life_migration.sql
  exec su postgres -c "MIGRATION=/tmp/_wo_life_migration.sql bash /tmp/_wo_life_test.sh"
fi

BIN=$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)
MIGRATION=${MIGRATION:-"$(dirname "$0")/../supabase_migration_maintenance_lifecycle.sql"}
DIR=$(mktemp -d)
PORT=54341
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
CREATE TABLE public.work_orders (id bigserial PRIMARY KEY, status text, title text);
INSERT INTO public.work_orders (status, title) VALUES ('pending', 'existing'), ('completed', 'done before');
SQL

"${PSQL[@]}" -f "$MIGRATION"
"${PSQL[@]}" -f "$MIGRATION"
echo "ok  applied twice"

[ "$(sql "select count(*) from information_schema.columns where table_name='work_orders' and column_name in ('permits','started_at','completed_at','artisan_signed_by','artisan_signed_at','foreman_signed_by','foreman_signed_at')")" = "7" ] || fail "expected 7 columns"
echo "ok  seven columns present"

[ "$(sql "select permits::text from work_orders where id=1")" = "{}" ] || fail "existing row should have empty permits"
[ "$(sql "select count(*) from work_orders where started_at is null and completed_at is null")" = "2" ] || fail "existing rows keep null lifecycle facts"
echo "ok  existing rows keep working with empty permits"

sql "update work_orders set permits='{\"permit_to_work\":{\"required\":true,\"reference\":\"PTW-1\"}}' where id=1" >/dev/null
echo "ok  an object is accepted"
expect_error "update work_orders set permits='[]' where id=1" "permits_is_object"
expect_error "update work_orders set permits=null where id=1" "null"
echo "ok  a non-object and null are refused"

echo "ALL PASSED"

