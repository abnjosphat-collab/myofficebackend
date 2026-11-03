-- Mining (and general) PPE issue/return/correction event log. Run in the Supabase
-- SQL editor together with the matrix-department migration (batch 2/3).
--
-- One row per accepted write, written by the single POST /api/ppe/events path:
-- the projected ppe_records row is derived from the event, never the reverse.
-- Replaying an idempotency_key returns the stored result instead of writing
-- twice; the UNIQUE constraint is the backstop, the lookup-first read is the
-- fast path. No UPDATE or DELETE traffic is expected on this table — a wrong
-- event is superseded by a later correction event, never edited or removed.
CREATE TABLE IF NOT EXISTS ppe_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind TEXT NOT NULL CHECK (kind IN ('issue', 'return', 'correction')),
    employee_id TEXT NOT NULL,
    ppe_type TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 1),
    department TEXT NOT NULL DEFAULT '',
    effective_date DATE NOT NULL DEFAULT CURRENT_DATE,
    section TEXT NOT NULL DEFAULT '',
    record_id INTEGER NULL,
    predecessor_event_id UUID NULL REFERENCES ppe_events(id),
    reversed_event_id UUID NULL REFERENCES ppe_events(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    evidence JSONB NOT NULL DEFAULT '{}',
    result JSONB NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ppe_events_employee_idx ON ppe_events (employee_id, ppe_type);
CREATE INDEX IF NOT EXISTS ppe_events_key_idx ON ppe_events (idempotency_key);

-- RLS: the backend service role owns this table; no anon/authenticated
-- access. The event log is written and read only through the API.
GRANT ALL ON TABLE public.ppe_events TO service_role;
REVOKE ALL ON TABLE public.ppe_events FROM anon, authenticated, PUBLIC;
ALTER TABLE public.ppe_events ENABLE ROW LEVEL SECURITY;
