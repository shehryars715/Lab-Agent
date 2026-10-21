-- Labs-Agent: users and run ownership.
-- Run once in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
-- Safe to run again: every statement is idempotent.
--
-- THE SECURITY MODEL, in three lines:
--   1. Row-level security is ON for every table, with NO policies. The public
--      anon key (which the browser has) can therefore read and write nothing.
--   2. The server calls only the functions below, with the service key, which
--      lives in the server's .env and nowhere else.
--   3. Who may see which run is decided in FastAPI (web/server/ownership.py),
--      using `owns_run` and `my_runs`; a stranger's run answers 404.

create table if not exists public.profiles (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  email      text not null default '',
  name       text not null default '',
  cms_id     text not null default '',
  section    text not null default '',
  program    text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One row per run: who started it (job_id, from the server's job registry)
-- and, once the solve began, which folder on disk it wrote (run_dir).
create table if not exists public.runs (
  job_id     text primary key,
  user_id    uuid not null references public.profiles (user_id) on delete cascade,
  run_dir    text unique,
  created_at timestamptz not null default now()
);
create index if not exists runs_user_created on public.runs (user_id, created_at desc);

alter table public.profiles enable row level security;
alter table public.runs enable row level security;
revoke all on public.profiles, public.runs from anon, authenticated;


-- First sign-in creates the profile; later sign-ins refresh the email. The
-- Google name pre-fills `name` only while the student has not set their own.
create or replace function public.ensure_user(p_user_id uuid, p_email text, p_name text)
returns void language sql security definer set search_path = public as $$
  insert into profiles (user_id, email, name)
  values (p_user_id, coalesce(p_email, ''), coalesce(p_name, ''))
  on conflict (user_id) do update
    set email = excluded.email,
        name = case when profiles.name = '' then excluded.name else profiles.name end,
        updated_at = now();
$$;

create or replace function public.get_profile(p_user_id uuid)
returns jsonb language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'name', name, 'cms_id', cms_id, 'section', section, 'program', program, 'email', email)
  from profiles where user_id = p_user_id;
$$;

create or replace function public.save_profile(
  p_user_id uuid, p_name text, p_cms_id text, p_section text, p_program text)
returns void language sql security definer set search_path = public as $$
  update profiles
     set name = left(coalesce(p_name, ''), 120),
         cms_id = left(coalesce(p_cms_id, ''), 120),
         section = left(coalesce(p_section, ''), 120),
         program = left(coalesce(p_program, ''), 120),
         updated_at = now()
   where user_id = p_user_id;
$$;

create or replace function public.record_run(p_job_id text, p_user_id uuid)
returns void language sql security definer set search_path = public as $$
  insert into runs (job_id, user_id) values (p_job_id, p_user_id);
$$;

create or replace function public.attach_run_dir(p_job_id text, p_run_dir text)
returns void language sql security definer set search_path = public as $$
  update runs set run_dir = p_run_dir where job_id = p_job_id;
$$;

create or replace function public.owns_run(p_user_id uuid, p_run_dir text)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from runs where user_id = p_user_id and run_dir = p_run_dir);
$$;

create or replace function public.my_runs(p_user_id uuid, p_limit int)
returns table (run_dir text) language sql stable security definer set search_path = public as $$
  select r.run_dir from runs r
   where r.user_id = p_user_id and r.run_dir is not null
   order by r.created_at desc
   limit least(greatest(p_limit, 1), 100);
$$;

-- Retention: the server deletes the folders, then forgets them here.
create or replace function public.forget_runs(p_run_dirs text[])
returns void language sql security definer set search_path = public as $$
  delete from runs where run_dir = any (p_run_dirs);
$$;


-- Functions are callable by PUBLIC by default. Only the server may call these.
do $$
declare f text;
begin
  foreach f in array array[
    'ensure_user(uuid,text,text)', 'get_profile(uuid)',
    'save_profile(uuid,text,text,text,text)', 'record_run(text,uuid)',
    'attach_run_dir(text,text)', 'owns_run(uuid,text)', 'my_runs(uuid,int)',
    'forget_runs(text[])'
  ] loop
    execute format('revoke all on function public.%s from public, anon, authenticated', f);
    execute format('grant execute on function public.%s to service_role', f);
  end loop;
end $$;
