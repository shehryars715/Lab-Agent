-- 004: whose job is this, for a server that restarted (PLAN.md N1, durable jobs).
--
-- A job's chat now survives a restart (runs/_web_uploads/<job>/events.jsonl),
-- and the server reads it back the first time someone asks for it. Whose it
-- is comes from here -- the row record_run wrote when the job started -- and
-- never from the job's own folder: the disk is data, not authority.
--
-- Apply in the Supabase SQL editor BEFORE deploying the code that calls it.
-- Without it, a chat from before a restart answers 503 until it exists.

create or replace function public.job_owner(p_job_id text)
returns uuid language sql stable security definer set search_path = public as $$
  select user_id from runs where job_id = p_job_id;
$$;

-- Functions are callable by PUBLIC by default. Only the server may call this.
revoke all on function public.job_owner(text) from public, anon, authenticated;
grant execute on function public.job_owner(text) to service_role;
