-- Saved admin queries. Paste one at a time into the Supabase SQL editor.

-- Top up an account (find the user id in Authentication -> Users).
insert into public.credit_ledger (user_id, kind, credits, note)
values ('00000000-0000-0000-0000-000000000000', 'adjust', 300, 'top-up: reason here');

-- Today's real spend against the daily cap, and holds still open.
select (select usd from public.platform_spend
         where day = (now() at time zone 'utc')::date) as spent_today_usd,
       (select coalesce(sum(credits * usd_per_credit), 0) from public.holds
         where settled_at is null) as held_usd;

-- Top spenders this month, in credits.
select p.email, -sum(l.credits) filter (where l.kind in ('hold', 'release')) as used,
       sum(l.credits) as balance
  from public.credit_ledger l join public.profiles p using (user_id)
 where l.at >= date_trunc('month', now())
 group by p.email order by used desc nulls last limit 20;

-- One account's ledger, newest first.
select at, kind, credits, note from public.credit_ledger
 where user_id = '00000000-0000-0000-0000-000000000000' order by at desc limit 50;
