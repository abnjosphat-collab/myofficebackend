-- ============================================================
-- MyOffice Engineering Module Migration — Gold Mine Operations
-- Run in Supabase SQL Editor
-- ============================================================

-- ── 1. Job Cards (Work Orders) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.job_cards (
  id              BIGSERIAL PRIMARY KEY,
  job_no          TEXT UNIQUE NOT NULL,
  title           TEXT NOT NULL,
  equipment_id    TEXT,
  equipment_name  TEXT,
  type            TEXT NOT NULL DEFAULT 'corrective'
                  CHECK (type IN ('corrective','preventive','predictive','shutdown','project')),
  priority        TEXT NOT NULL DEFAULT 'medium'
                  CHECK (priority IN ('critical','high','medium','low')),
  status          TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','in_progress','on_hold','completed','cancelled')),
  description     TEXT,
  tasks           JSONB NOT NULL DEFAULT '[]',
  parts_used      JSONB NOT NULL DEFAULT '[]',
  labour_hours    NUMERIC(6,2) DEFAULT 0,
  assigned_to     TEXT,
  supervisor      TEXT,
  section         TEXT,
  scheduled_date  DATE,
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  sign_off_by     TEXT,
  sign_off_at     TIMESTAMPTZ,
  photos          TEXT[] DEFAULT '{}',
  notes           TEXT,
  created_by      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 2. Shift Handover Reports ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.shift_handovers (
  id                    BIGSERIAL PRIMARY KEY,
  handover_date         DATE NOT NULL,
  shift                 TEXT NOT NULL CHECK (shift IN ('day','night','afternoon')),
  outgoing_supervisor   TEXT NOT NULL,
  incoming_supervisor   TEXT,
  section               TEXT,
  equipment_summary     JSONB NOT NULL DEFAULT '[]',
  completed_work        TEXT,
  outstanding_work      TEXT,
  safety_concerns       TEXT,
  environmental_issues  TEXT,
  production_notes      TEXT,
  general_notes         TEXT,
  acknowledged_by       TEXT,
  acknowledged_at       TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 3. Statutory Compliance Register ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.compliance_register (
  id               BIGSERIAL PRIMARY KEY,
  equipment_id     TEXT,
  equipment_name   TEXT NOT NULL,
  inspection_type  TEXT NOT NULL,
  regulatory_body  TEXT,
  certificate_no   TEXT,
  issue_date       DATE,
  expiry_date      DATE NOT NULL,
  status           TEXT NOT NULL DEFAULT 'current'
                   CHECK (status IN ('current','due_soon','overdue','not_applicable')),
  responsible      TEXT,
  inspector        TEXT,
  document_url     TEXT,
  notes            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 4. Lubrication Management ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.lube_schedules (
  id               BIGSERIAL PRIMARY KEY,
  equipment_id     TEXT,
  equipment_name   TEXT NOT NULL,
  lube_point       TEXT NOT NULL,
  lubricant_type   TEXT NOT NULL,
  lubricant_grade  TEXT,
  quantity_litres  NUMERIC(8,2),
  interval_hours   INTEGER,
  interval_days    INTEGER,
  last_done_date   DATE,
  last_done_hours  INTEGER,
  next_due_date    DATE,
  next_due_hours   INTEGER,
  status           TEXT NOT NULL DEFAULT 'current'
                   CHECK (status IN ('current','due_soon','overdue')),
  section          TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.lube_records (
  id               BIGSERIAL PRIMARY KEY,
  schedule_id      BIGINT REFERENCES public.lube_schedules(id),
  equipment_name   TEXT NOT NULL,
  lube_point       TEXT NOT NULL,
  done_date        DATE NOT NULL,
  done_hours       INTEGER,
  quantity_used    NUMERIC(8,2),
  technician       TEXT,
  notes            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 5. Oil / Condition Monitoring Samples ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.condition_monitoring (
  id               BIGSERIAL PRIMARY KEY,
  equipment_id     TEXT,
  equipment_name   TEXT NOT NULL,
  component        TEXT,
  monitoring_type  TEXT NOT NULL
                   CHECK (monitoring_type IN ('oil_analysis','vibration','thermography','visual','ultrasonic')),
  sampled_date     DATE NOT NULL,
  value            NUMERIC(12,4),
  unit             TEXT,
  iron_ppm         NUMERIC(8,2),
  copper_ppm       NUMERIC(8,2),
  lead_ppm         NUMERIC(8,2),
  viscosity        NUMERIC(8,2),
  water_pct        NUMERIC(6,3),
  result           TEXT NOT NULL DEFAULT 'normal'
                   CHECK (result IN ('normal','caution','critical')),
  lab_reference    TEXT,
  technician       TEXT,
  notes            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 6. Contractors ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.contractors (
  id                  BIGSERIAL PRIMARY KEY,
  company_name        TEXT NOT NULL,
  trade               TEXT NOT NULL,
  contact_name        TEXT,
  phone               TEXT,
  email               TEXT,
  address             TEXT,
  certifications      JSONB DEFAULT '[]',
  insurance_expiry    DATE,
  contract_start      DATE,
  contract_end        DATE,
  status              TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','inactive','blacklisted')),
  performance_rating  INTEGER CHECK (performance_rating BETWEEN 1 AND 5),
  notes               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.contractor_jobs (
  id              BIGSERIAL PRIMARY KEY,
  contractor_id   BIGINT REFERENCES public.contractors(id),
  job_title       TEXT NOT NULL,
  equipment_name  TEXT,
  scope           TEXT,
  start_date      DATE,
  end_date        DATE,
  cost            NUMERIC(12,2),
  currency        TEXT DEFAULT 'USD',
  status          TEXT NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned','in_progress','completed','cancelled')),
  notes           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 7. Production Data ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.production_data (
  id                BIGSERIAL PRIMARY KEY,
  prod_date         DATE NOT NULL,
  shift             TEXT CHECK (shift IN ('day','night','afternoon')),
  tonnes_milled     NUMERIC(10,2),
  feed_rate_tph     NUMERIC(8,2),
  grade_gpt         NUMERIC(8,4),
  recovery_pct      NUMERIC(6,3),
  gold_produced_oz  NUMERIC(10,4),
  mill_availability NUMERIC(6,3),
  power_kwh         NUMERIC(12,2),
  downtime_hours    NUMERIC(6,2) DEFAULT 0,
  downtime_reason   TEXT,
  operator          TEXT,
  comments          TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 8. Failure Mode Register ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.failure_modes (
  id                  BIGSERIAL PRIMARY KEY,
  equipment_type      TEXT NOT NULL,
  equipment_name      TEXT,
  component           TEXT NOT NULL,
  failure_mode        TEXT NOT NULL,
  failure_cause       TEXT,
  symptoms            TEXT,
  detection_method    TEXT,
  corrective_action   TEXT,
  preventive_action   TEXT,
  severity            INTEGER CHECK (severity BETWEEN 1 AND 5),
  probability         INTEGER CHECK (probability BETWEEN 1 AND 5),
  detectability       INTEGER CHECK (detectability BETWEEN 1 AND 5),
  occurrence_count    INTEGER DEFAULT 0,
  last_occurred       DATE,
  created_by          TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 9. Competency Matrix ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.competency_matrix (
  id               BIGSERIAL PRIMARY KEY,
  employee_id      TEXT NOT NULL,
  employee_name    TEXT NOT NULL,
  trade            TEXT,
  equipment_type   TEXT NOT NULL,
  skill_area       TEXT NOT NULL,
  skill_level      INTEGER NOT NULL DEFAULT 0
                   CHECK (skill_level BETWEEN 0 AND 4),
  certified        BOOLEAN DEFAULT FALSE,
  cert_date        DATE,
  cert_expiry      DATE,
  certified_by     TEXT,
  notes            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 10. KPI Targets ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.kpi_targets (
  id          BIGSERIAL PRIMARY KEY,
  kpi_name    TEXT NOT NULL,
  kpi_key     TEXT NOT NULL UNIQUE,
  target      NUMERIC(10,4) NOT NULL,
  unit        TEXT,
  period      TEXT DEFAULT 'monthly',
  category    TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Default KPI targets for a gold mine engineering dept
INSERT INTO public.kpi_targets (kpi_name, kpi_key, target, unit, category) VALUES
  ('Fleet Availability',         'fleet_availability',   85,    '%',     'reliability'),
  ('PM Compliance',              'pm_compliance',         90,    '%',     'maintenance'),
  ('MTTR',                       'mttr',                  4,     'hours', 'reliability'),
  ('Breakdown Count (monthly)',  'breakdown_count',       20,    'count', 'reliability'),
  ('Maintenance Backlog',        'maintenance_backlog',   15,    'days',  'maintenance'),
  ('Lube Compliance',            'lube_compliance',       95,    '%',     'maintenance'),
  ('Safety Incidents',           'safety_incidents',      0,     'count', 'safety'),
  ('Contractor Performance',     'contractor_performance',80,    '%',     'contractors')
ON CONFLICT (kpi_key) DO NOTHING;

-- ── 11. Spares BOM & Criticality (extend existing spares) ────────────────────
ALTER TABLE IF EXISTS public.spares
  ADD COLUMN IF NOT EXISTS criticality       TEXT DEFAULT 'C' CHECK (criticality IN ('A','B','C')),
  ADD COLUMN IF NOT EXISTS equipment_bom     TEXT[],
  ADD COLUMN IF NOT EXISTS lead_time_days    INTEGER,
  ADD COLUMN IF NOT EXISTS min_stock         INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS reorder_point     INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS max_stock         INTEGER;

-- ── Updated_at triggers ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;

DO $$ DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'job_cards','compliance_register','lube_schedules',
    'contractors','failure_modes','competency_matrix','kpi_targets'
  ] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_updated ON public.%s', t, t);
    EXECUTE format('CREATE TRIGGER trg_%s_updated BEFORE UPDATE ON public.%s FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()', t, t);
  END LOOP;
END $$;

-- ── Enable RLS on all new tables ──────────────────────────────────────────────
DO $$ DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'job_cards','shift_handovers','compliance_register','lube_schedules',
    'lube_records','condition_monitoring','contractors','contractor_jobs',
    'production_data','failure_modes','competency_matrix','kpi_targets'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS "Authenticated users full access" ON public.%s', t);
    EXECUTE format('CREATE POLICY "Authenticated users full access" ON public.%s TO authenticated USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;

SELECT 'Engineering module tables created successfully.' AS result;
