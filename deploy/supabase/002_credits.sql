-- Labs-Agent: credits. Run after 001_users.sql, in the Supabase SQL editor.
-- Safe to run again.
--
-- A LEDGER, NOT A BALANCE COLUMN. Every change to an account's credits is a
-- row: the monthly grant (+), a hold when a run starts (-), the release of
-- what it did not use (+), a manual top-up (adjust). The balance is the sum.
-- A wrong balance can always be explained, and a correction is a new row,
-- never an edit.

create table if not exists public.holds (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references public.profiles (user_id) on delete cascade,
  job_id          text not null,
  kind            text not null check (kind in ('run', 'followup')),
  credits         int not null check (credits > 0),
  usd_per_credit  numeric not null,
  created_at      timestamptz not null default now(),
  settled_at      timestamptz,
  charged_credits int,
  actual_usd      numeric
);
create index if not exists holds_open on public.holds (created_at) where settled_at is null;

create table if not exists public.credit_ledger (
  id       bigint generated always as identity primary key,
  user_id  uuid not null references public.profiles (user_id) on delete cascade,
  at       timestamptz not null default now(),
  kind     text not null check (kind in ('grant', 'hold', 'release', 'adjust')),
  credits  int not null,
  hold_id  uuid references public.holds (id),
  month    date,
  note     text not null default ''
);
create index if not exists ledger_user on public.credit_ledger (user_id, at);
-- One monthly grant per account per month, however many requests race to it.
create unique index if not exists ledger_one_grant
  on public.credit_ledger (user_id, month) where kind = 'grant';

-- Real model spend per UTC day, across every account -- refused labs included.
create table if not exists public.platform_spend (
  day date primary key,
  usd numeric not null default 0
);

alter table public.holds enable row level security;
alter table public.credit_ledger enable row level security;
alter table public.platform_spend enable row level security;
revoke all on public.holds, public.credit_ledger, public.platform_spend from anon, authenticated;


-- The month's allowance, written by the first request of the month. No cron
-- job to forget, and an account that never comes back costs no rows.
create or replace function public.ensure_month_grant(p_user_id uuid, p_monthly int)
returns void language sql security definer set search_path = public as $$
  insert into credit_ledger (user_id, kind, credits, month, note)
  values (p_user_id, 'grant', p_monthly,
          date_trunc('month', now() at time zone 'utc')::date, 'monthly allowance')
  on conflict (user_id, month) where kind = 'grant' do nothing;
$$;

create or replace function public.my_balance(p_user_id uuid, p_monthly int)
returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform ensure_month_grant(p_user_id, p_monthly);
  return jsonb_build_object(
    'balance', (select coalesce(sum(credits), 0) from credit_ledger where user_id = p_user_id));
end $$;


-- RESERVE: hold min(p_max, balance) credits for one run, atomically.
--
-- The account's profile row is locked FOR UPDATE first, so two runs started
-- at once by the same account are serialised: the second sees the balance
-- the first left. Today's platform_spend row is locked too, so the global cap
-- is checked against every hold, including ones being made right now.
-- Returns {hold_id, credits, balance} or {error: no_credits | daily_cap}.
create or replace function public.reserve(
  p_user_id uuid, p_job_id text, p_kind text, p_max int, p_min int,
  p_monthly int, p_usd_per_credit numeric, p_daily_cap_usd numeric)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_today   date := (now() at time zone 'utc')::date;
  v_balance int;
  v_hold    int;
  v_spent   numeric;
  v_open    numeric;
  v_id      uuid;
begin
  perform 1 from profiles where user_id = p_user_id for update;
  if not found then
    return jsonb_build_object('error', 'no_profile');
  end if;
  perform ensure_month_grant(p_user_id, p_monthly);

  select coalesce(sum(credits), 0) into v_balance from credit_ledger where user_id = p_user_id;
  if v_balance < p_min then
    return jsonb_build_object('error', 'no_credits', 'balance', v_balance);
  end if;
  v_hold := least(p_max, v_balance);

  insert into platform_spend (day, usd) values (v_today, 0) on conflict (day) do nothing;
  select usd into v_spent from platform_spend where day = v_today for update;
  select coalesce(sum(credits * usd_per_credit), 0) into v_open from holds where settled_at is null;
  if v_spent + v_open + v_hold * p_usd_per_credit > p_daily_cap_usd then
    return jsonb_build_object('error', 'daily_cap');
  end if;

  insert into holds (user_id, job_id, kind, credits, usd_per_credit)
  values (p_user_id, p_job_id, p_kind, v_hold, p_usd_per_credit)
  returning id into v_id;
  insert into credit_ledger (user_id, kind, credits, hold_id, note)
  values (p_user_id, 'hold', -v_hold, v_id, p_kind);

  return jsonb_build_object('hold_id', v_id, 'credits', v_hold, 'balance', v_balance - v_hold);
end $$;


-- SETTLE: charge min(charged, held), give back the rest, record real spend.
-- Settling twice is a no-op, so a retry after a timeout cannot double-refund.
create or replace function public.settle(p_hold_id uuid, p_charged int, p_actual_usd numeric)
returns void language plpgsql security definer set search_path = public as $$
declare
  h        holds%rowtype;
  v_charge int;
begin
  select * into h from holds where id = p_hold_id for update;
  if not found or h.settled_at is not null then
    return;
  end if;
  v_charge := least(greatest(coalesce(p_charged, 0), 0), h.credits);
  update holds
     set settled_at = now(), charged_credits = v_charge, actual_usd = coalesce(p_actual_usd, 0)
   where id = p_hold_id;
  if h.credits > v_charge then
    insert into credit_ledger (user_id, kind, credits, hold_id, note)
    values (h.user_id, 'release', h.credits - v_charge, h.id, 'unused');
  end if;
  -- The REAL cost, even when it ran past the hold or the run was free: the
  -- daily cap guards money actually spent, not money charged.
  insert into platform_spend (day, usd)
  values ((now() at time zone 'utc')::date, coalesce(p_actual_usd, 0))
  on conflict (day) do update set usd = platform_spend.usd + excluded.usd;
end $$;


-- At server start: holds from before it belong to runs that died. Release
-- them in full (nobody pays for a dead run) but count their worst case as
-- spent, because what they really cost is no longer known.
create or replace function public.release_open_holds(p_before timestamptz)
returns int language plpgsql security definer set search_path = public as $$
declare
  h holds%rowtype;
  n int := 0;
begin
  for h in select * from holds where settled_at is null and created_at < p_before loop
    perform settle(h.id, 0, h.credits * h.usd_per_credit);
    n := n + 1;
  end loop;
  return n;
end $$;


do $$
declare f text;
begin
  foreach f in array array[
    'ensure_month_grant(uuid,int)', 'my_balance(uuid,int)',
    'reserve(uuid,text,text,int,int,int,numeric,numeric)',
    'settle(uuid,int,numeric)', 'release_open_holds(timestamptz)'
  ] loop
    execute format('revoke all on function public.%s from public, anon, authenticated', f);
    execute format('grant execute on function public.%s to service_role', f);
  end loop;
end $$;

-- ADMIN (run by hand in the SQL editor; see deploy/supabase/admin.sql).
