-- Labs-Agent: credits become rupees. Run after 002_credits.sql. Safe to re-run.
--
-- From 2026-10-22, 1 credit = Rs 1 (never said on screen), and every amount
-- in the ledger is counted in PAISA (1/100 credit), still as integers. The
-- functions in 002 are unit-free -- they add and compare whatever the server
-- passes -- so only the rows written in the old unit need converting.
--
-- Old unit -> paisa: 1 old credit = $0.0001 = Rs 0.028 (at Rs 280 = $1)
--                     = 2.8 paisa.
-- This month's grant is set to the new allowance (Rs 50 = 5000 paisa)
-- instead of being converted, so an account opened before the switch gets
-- the same allowance as one opened after it.

create table if not exists public.migrations (
  name text primary key,
  at   timestamptz not null default now()
);
alter table public.migrations enable row level security;
revoke all on public.migrations from anon, authenticated;

do $$
declare
  this_month date := date_trunc('month', now() at time zone 'utc')::date;
begin
  if exists (select 1 from migrations where name = '003_rupee_credits') then
    return;
  end if;

  update credit_ledger set credits = 5000
   where kind = 'grant' and month = this_month;
  update credit_ledger set credits = round(credits * 2.8)
   where not (kind = 'grant' and month = this_month);
  update holds
     set credits = round(credits * 2.8),
         charged_credits = round(charged_credits * 2.8),
         usd_per_credit = usd_per_credit / 2.8;

  insert into migrations (name) values ('003_rupee_credits');
end $$;

comment on column public.credit_ledger.credits is
  'paisa: 1/100 of a credit; 1 credit = Rs 1 (never shown as rupees)';
comment on column public.holds.credits is 'paisa held for one run';
comment on column public.holds.usd_per_credit is 'dollars per paisa at the time of the hold';
