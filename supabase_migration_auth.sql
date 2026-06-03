-- ============================================================
-- MyOffice Auth Migration — run once in Supabase SQL Editor
-- Creates: user_profiles table, auto-create trigger, RLS
-- ============================================================

-- 1. user_profiles table
CREATE TABLE IF NOT EXISTS public.user_profiles (
  id          UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email       TEXT        NOT NULL,
  full_name   TEXT,
  avatar_url  TEXT,
  role        TEXT        NOT NULL DEFAULT 'user'
                          CHECK (role IN ('viewer','user','manager','admin','super_admin')),
  permissions JSONB       NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Enable Row Level Security
ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;

-- 3. Security-definer helper (avoids RLS recursion)
CREATE OR REPLACE FUNCTION public.my_role()
RETURNS TEXT
LANGUAGE SQL
SECURITY DEFINER
STABLE AS $$
  SELECT role FROM public.user_profiles WHERE id = auth.uid();
$$;

-- 4. RLS policies

-- All authenticated users can read all profiles (internal tool)
DROP POLICY IF EXISTS "Authenticated users can view profiles" ON public.user_profiles;
CREATE POLICY "Authenticated users can view profiles"
  ON public.user_profiles FOR SELECT
  TO authenticated
  USING (true);

-- Users can update their own non-role fields
DROP POLICY IF EXISTS "Users update own profile" ON public.user_profiles;
CREATE POLICY "Users update own profile"
  ON public.user_profiles FOR UPDATE
  TO authenticated
  USING  (auth.uid() = id)
  WITH CHECK (auth.uid() = id AND role = (SELECT role FROM public.user_profiles WHERE id = auth.uid()));

-- Admins and super_admins can update any row (including role)
DROP POLICY IF EXISTS "Admins update any profile" ON public.user_profiles;
CREATE POLICY "Admins update any profile"
  ON public.user_profiles FOR UPDATE
  TO authenticated
  USING  (public.my_role() IN ('admin','super_admin'))
  WITH CHECK (public.my_role() IN ('admin','super_admin'));

-- Inserts are handled by the trigger (not by client directly)
DROP POLICY IF EXISTS "Allow profile insert from trigger" ON public.user_profiles;
CREATE POLICY "Allow profile insert from trigger"
  ON public.user_profiles FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = id);

-- 5. Auto-create profile on first sign-up
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.user_profiles (id, email, full_name, avatar_url)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(
      NEW.raw_user_meta_data->>'full_name',
      NEW.raw_user_meta_data->>'name',
      split_part(NEW.email, '@', 1)
    ),
    COALESCE(
      NEW.raw_user_meta_data->>'avatar_url',
      NEW.raw_user_meta_data->>'picture'
    )
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- 6. updated_at auto-stamp
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS set_user_profiles_updated_at ON public.user_profiles;
CREATE TRIGGER set_user_profiles_updated_at
  BEFORE UPDATE ON public.user_profiles
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- 7. Security-definer role lookup (used by FastAPI backend — bypasses RLS so anon key can call it)
CREATE OR REPLACE FUNCTION public.get_user_role_by_id(user_id UUID)
RETURNS TEXT
LANGUAGE SQL
SECURITY DEFINER
STABLE AS $$
  SELECT COALESCE(role, 'user') FROM public.user_profiles WHERE id = user_id;
$$;

-- 8. Elevate yourself to super_admin (run ONCE, replace with your email)
-- UPDATE public.user_profiles SET role = 'super_admin' WHERE email = 'your@email.com';

-- Done!
SELECT 'user_profiles table and auth triggers created.' AS result;
