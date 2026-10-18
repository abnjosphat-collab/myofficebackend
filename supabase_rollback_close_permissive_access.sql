-- ============================================================
-- ROLLBACK for supabase_migration_close_permissive_access.sql
-- Run ONLY if the app breaks after that migration. It restores the OPEN state
-- (anon and any signed-in user can again read/write these tables), so use it as
-- a stop-gap while the real cause is fixed, then re-run the migration.
-- State captured live on 2026-10-18 immediately before the migration:
--   anon and authenticated held ALL table privileges (DELETE, INSERT,
--   REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE) on all 83 public
--   tables and views; default privileges granted the same to new objects.
-- ============================================================

-- 1. Privileges: back to ALL for anon and authenticated.
-- Skips the 27 tables closed by supabase_migration_enable_rls_public_tables.sql (they must stay closed).
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
      AND c.relname NOT IN (
        'employees', 'leaves', 'overtime', 'duty_roster', 'duty_rotations', 'rotation_covers',
        'standby_rotations', 'standby_schedules', 'drivers', 'password_reset_requests', 'work_orders',
        'breakdowns', 'daily_reports', 'sheq_reports', 'sheq_findings', 'sheq_inspections', 'ppe_records',
        'spare_parts', 'spare_requisitions', 'stock_issues', 'notices', 'document_categories',
        'visualizations', 'timesheets_backup_20261014_holiday', 'artisan_timesheets', 'lookup_lists',
        'ppe_matrix')
  LOOP
    EXECUTE format('GRANT ALL ON TABLE public.%I TO anon, authenticated', r.relname);
  END LOOP;
END $$;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon, authenticated;

-- 2. Policies (the exact definitions that existed; user_profiles was never changed).
CREATE POLICY auth_all_accounting_assets ON public.accounting_assets AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_accounting_assets ON public.accounting_assets AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY auth_all_accounting_expenses ON public.accounting_expenses AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_accounting_expenses ON public.accounting_expenses AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY auth_all_accounting_liabilities ON public.accounting_liabilities AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_accounting_liabilities ON public.accounting_liabilities AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY auth_all_accounting_transactions ON public.accounting_transactions AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_accounting_transactions ON public.accounting_transactions AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Enable all for service role" ON public.alerts AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY auth_all_availabilities ON public.availabilities AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_availabilities ON public.availabilities AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.competency_matrix AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.compliance_register AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Enable all for service role" ON public.compressor_readings AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Enable all for service role" ON public.compressors AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Authenticated users full access" ON public.condition_monitoring AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.contractor_jobs AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.contractors AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Allow all access" ON public.corrective_actions AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Enable all for service role" ON public.daily_entries AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY auth_all_document_folders ON public.document_folders AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_document_folders ON public.document_folders AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY auth_all_documents ON public.documents AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_documents ON public.documents AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Allow anyone to delete equipment" ON public.equipment AS PERMISSIVE FOR DELETE TO public USING (true);
CREATE POLICY "Allow anyone to insert equipment" ON public.equipment AS PERMISSIVE FOR INSERT TO public WITH CHECK (true);
CREATE POLICY "Allow anyone to read equipment" ON public.equipment AS PERMISSIVE FOR SELECT TO public USING (true);
CREATE POLICY "Allow anyone to update equipment" ON public.equipment AS PERMISSIVE FOR UPDATE TO public USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.failure_modes AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_inventory_items ON public.inventory_items AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.job_cards AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.kpi_targets AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.lube_records AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.lube_schedules AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Enable all for service role" ON public.maintenance_schedule AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Authenticated read schedule runs" ON public.maintenance_schedule_runs AS PERMISSIVE FOR SELECT TO public USING ((auth.role() = 'authenticated'::text));
CREATE POLICY "Authenticated read schedules" ON public.maintenance_schedules AS PERMISSIVE FOR SELECT TO public USING ((auth.role() = 'authenticated'::text));
CREATE POLICY "Managers write schedules" ON public.maintenance_schedules AS PERMISSIVE FOR ALL TO public USING ((my_role() = ANY (ARRAY['manager'::text, 'admin'::text, 'super_admin'::text]))) WITH CHECK ((my_role() = ANY (ARRAY['manager'::text, 'admin'::text, 'super_admin'::text])));
CREATE POLICY "Allow all access" ON public.nearmiss_reports AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Allow all access" ON public.pachedu_reports AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY auth_all_ppe_order_list ON public.ppe_order_list AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_ppe_order_list ON public.ppe_order_list AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.production_data AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Allow all access" ON public.pto_action_plan AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Allow all access" ON public.pto_reports AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Enable all for authenticated users" ON public.requisition_items AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Enable all for authenticated users" ON public.requisitions AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Allow all for authenticated" ON public.safety_complaints AS PERMISSIVE FOR ALL TO public USING (true) WITH CHECK (true);
CREATE POLICY auth_all_saved_quotations ON public.saved_quotations AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_saved_quotations ON public.saved_quotations AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY auth_all_attachments ON public.service_attachments AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_attachments ON public.service_attachments AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Enable all for service role" ON public.service_intervals AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Enable all for service role" ON public.service_records AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY auth_all_services ON public.services AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_services ON public.services AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users full access" ON public.shift_handovers AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY auth_all_sop_documents ON public.sop_documents AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_sop_documents ON public.sop_documents AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY auth_all_sop_revisions ON public.sop_revisions AS PERMISSIVE FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY service_role_sop_revisions ON public.sop_revisions AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Enable public access" ON public.spares AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY timesheets_all_access ON public.timesheets AS PERMISSIVE FOR ALL TO public USING (true) WITH CHECK (true);
CREATE POLICY service_role_training_certifications ON public.training_certifications AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Users delete own signature" ON public.user_signatures AS PERMISSIVE FOR DELETE TO public USING ((auth.uid() = user_id));
CREATE POLICY "Users insert own signature" ON public.user_signatures AS PERMISSIVE FOR INSERT TO public WITH CHECK ((auth.uid() = user_id));
CREATE POLICY "Users read own signature" ON public.user_signatures AS PERMISSIVE FOR SELECT TO public USING ((auth.uid() = user_id));
CREATE POLICY "Users update own signature" ON public.user_signatures AS PERMISSIVE FOR UPDATE TO public USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));
CREATE POLICY "Allow all access" ON public.vfl_action_plan AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Allow all access" ON public.vfl_reports AS PERMISSIVE FOR ALL TO public USING (true);
CREATE POLICY "Allow all access" ON public.work_stoppage_reports AS PERMISSIVE FOR ALL TO public USING (true);

-- 3. Only if the first two statements above are not enough: the earlier files
-- (enable_rls_public_tables / lock_down_functions) closed 27 tables and 7
-- functions deliberately. Do NOT roll those back for this migration's problem.
-- Note: policies on tables that were dropped here are recreated above, but RLS stays ON for every table,
-- exactly as it was before this migration.
