-- ============================================================
-- Admin User Status Migration — Run in Supabase SQL Editor
--
-- Backs the Admin Panel's deactivate/reactivate action. The Supabase Auth
-- admin API can ban/unban a user (auth.users.banned_until), but the
-- installed supabase-py SDK's User model doesn't expose that field back on
-- read (Pydantic silently drops undeclared fields) — so this column is our
-- own source of truth for "did an admin deactivate this account", kept in
-- sync by app/routers/admin.py's set_user_active endpoint alongside the
-- actual Supabase ban call. Defaults true so every existing user stays
-- active with no backfill needed.
-- ============================================================

alter table public.user_profiles
  add column if not exists is_active boolean not null default true;
