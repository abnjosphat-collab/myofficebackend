#!/usr/bin/env bash
# Rehearses supabase_migration_maintenance_audit.sql on a throwaway local PostgreSQL (never Supabase).
# Needs the PostgreSQL 16 server binaries and a non-root user (it re-executes itself as `postgres` when run as root).
# Usage: scripts/test_maintenance_audit_sql.sh
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  id postgres >/dev/null 2>&1 || { echo "need a 'postgres' user when running as root"; exit 2; }
  cp "$0" /tmp/_wo_audit_test.sh
  cp "$(dirname "$0")/../supabase_migration_maintenance_audit.sql" /tmp/_wo_audit_migration.sql
  chmod 644 /tmp/_wo_audit_test.sh /tmp/_wo_audit_migration.sql
  exec su postgres -c "MIGRATION=/tmp/_wo_audit_migration.sql bash /tmp/_wo_audit_test.sh"
fi

BIN=$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)
MIGRATION=${MIGRATION:-"$(dirname "$0")/../supabase_migration_maintenance_audit.sql"}
DIR=$(mktemp -d)
PORT=54339
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
CREATE TABLE public.work_orders (id bigserial PRIMARY KEY, status text, title text);
INSERT INTO public.work_orders (status, title) VALUES ('pending', 'existing');
SQL

# 1. Applies, and applies again (idempotent).
"${PSQL[@]}" -f "$MIGRATION"
"${PSQL[@]}" -f "$MIGRATION"
echo "ok  applied twice"

# 2. Existing rows keep working and get version 1.
[ "$(sql "select version from work_orders where id=1")" = "1" ] || fail "existing row should have version 1"
echo "ok  existing row has version 1"

# 3. Every UPDATE bumps the version.
sql "update work_orders set title='edited' where id=1" >/dev/null
[ "$(sql "select version from work_orders where id=1")" = "2" ] || fail "version should be 2 after one update"
echo "ok  update bumps version"

# 4. A stale write is detectable: UPDATE ... WHERE version = old touches no row.
[ "$(sql "with u as (update work_orders set title='stale' where id=1 and version=1 returning id) select count(*) from u")" = "0" ] || fail "stale update should touch 0 rows"
[ "$(sql "with u as (update work_orders set title='fresh' where id=1 and version=2 returning id) select count(*) from u")" = "1" ] || fail "current-version update should touch 1 row"
echo "ok  stale write touches nothing"

# 5. Audit rows append, and cannot be changed or removed.
sql "insert into maintenance_events (entity, entity_id, action, changes) values ('work_order', 1, 'updated', '{\"title\":[\"a\",\"b\"]}')" >/dev/null
expect_error "update maintenance_events set action='x'" "append-only"
expect_error "delete from maintenance_events" "append-only"
expect_error "insert into maintenance_events (entity, entity_id, action) values ('bogus', 1, 'x')" "check"
expect_error "insert into maintenance_events (entity, entity_id, action, changes) values ('work_order', 1, 'x', '[]')" "check"
echo "ok  audit rows are append-only and validated"

# 6. The trail outlives its work order.
sql "delete from work_orders where id=1" >/dev/null
[ "$(sql "select count(*) from maintenance_events where entity_id=1")" = "1" ] || fail "audit row should survive the work order"
echo "ok  audit survives deletion of the work order"

# 7. Comments: blank refused, removed with the work order.
sql "insert into work_orders (status, title) values ('pending','second')" >/dev/null
expect_error "insert into work_order_comments (work_order_id, body) values (2, '   ')" "check"
sql "insert into work_order_comments (work_order_id, body, author_name) values (2, 'Bearing ordered', 'a@b.c')" >/dev/null
sql "delete from work_orders where id=2" >/dev/null
[ "$(sql "select count(*) from work_order_comments")" = "0" ] || fail "comments should go with the work order"
echo "ok  comments validated and cascade"

# 8. client_token is unique only when set.
sql "insert into work_orders (status, client_token) values ('pending','t1'),('pending',null),('pending',null)" >/dev/null
expect_error "insert into work_orders (status, client_token) values ('pending','t1')" "unique"
echo "ok  client_token unique when present"

echo "ALL PASSED"
