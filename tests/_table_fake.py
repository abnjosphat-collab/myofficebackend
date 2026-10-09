# tests/_table_fake.py — an in-memory stand-in for the Supabase client, for routers that read and write one
# table (and optionally a storage bucket). It honours eq() filters, order(), limit(), insert(), update() and
# delete(), so a test can assert what was really stored. `fail = True` makes every query raise, to check that
# a database failure is reported rather than shown as an empty register.

import copy


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload, self.limit_n, self.order_col = db, table, [], "select", None, None, None

    def select(self, *_):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self.order_col = col
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        if self.db.fail:
            raise RuntimeError("database unavailable")
        rows = self.db.tables.setdefault(self.table, [])
        match = [r for r in rows if all(str(r.get(c)) == str(v) for c, v in self.filters)]
        if self.op == "insert":
            row = {"id": f"id-{len(rows) + 1}", "created_at": "2026-10-09T08:00:00+00:00", "updated_at": "2026-10-09T08:00:00+00:00", **self.payload}
            rows.append(row)
            return _Result([copy.deepcopy(row)])
        if self.op == "update":
            for r in match:
                r.update(self.payload)
            return _Result(copy.deepcopy(match))
        if self.op == "delete":
            self.db.tables[self.table] = [r for r in rows if r not in match]
            return _Result(copy.deepcopy(match))
        if self.order_col:
            match = sorted(match, key=lambda r: str(r.get(self.order_col)))
        return _Result(copy.deepcopy(match[: self.limit_n] if self.limit_n else match))


class _Bucket:
    def __init__(self, db):
        self.db = db

    def upload(self, path, content, opts):
        self.db.files[path] = content

    def get_public_url(self, path):
        return f"https://storage.example/{path}"

    def remove(self, paths):
        for p in paths:
            self.db.files.pop(p, None)


class TableFake:
    def __init__(self):
        self.tables, self.files, self.fail = {}, {}, False
        self.storage = type("Storage", (), {"from_": lambda _s, _bucket: _Bucket(self)})()

    def table(self, name):
        return _Query(self, name)
