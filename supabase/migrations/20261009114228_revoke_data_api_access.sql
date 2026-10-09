-- Close the Data API off from this app's tables, and drop the superseded
-- token table.
--
-- Nothing in this product uses the Data API. The backend connects as `postgres`
-- over asyncpg and every read goes through FastAPI; supabase-js is used purely
-- for Auth (getSession / signInWithOAuth / linkIdentity / signOut) and never
-- issues .from(), .rpc(), storage or realtime calls. So `anon` and
-- `authenticated` need no privileges on public at all.
--
-- They had all of them. Supabase grants ALL on tables in `public` to the Data
-- API roles by default (locally, the `auto_expose_new_tables` behaviour noted in
-- config.toml; in the cloud, the project-creation default), and `public` is an
-- exposed schema. Today that is inert for eight of the ten tables because RLS is
-- on with no policies, which denies everything to a non-bypassing role. The
-- problem is that the safety comes from an absence rather than a decision:
--
--   * Adding any policy to any of those tables - a perfectly reasonable thing to
--     do later - instantly publishes it to anyone holding the anon key. The
--     grant is already there, so nothing in that change would look dangerous.
--   * TRUNCATE and REFERENCES are not governed by RLS at all. They are not
--     reachable through PostgREST, so this is not a present hole, but there is
--     no reason for those grants to exist either.
--   * connected_accounts holds encrypted OAuth access and refresh tokens for
--     every mailbox. It is the highest-value table in the database and should
--     not be one `create policy` away from being readable.
--
-- Revoking is precise about who loses what: only `anon` and `authenticated`.
-- `postgres` owns the tables and `service_role` carries bypassrls, so neither is
-- touched and the backend is unaffected.
--
-- The profiles policies are deliberately left in place. They are correctly
-- written (`to authenticated` with `(select auth.uid()) = id`, and the UPDATE
-- has both USING and WITH CHECK), and keeping them means that if the Data API is
-- ever wanted, the one table a client would legitimately read is already
-- modelled correctly.

revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all routines in schema public from anon, authenticated;

-- The statements above only affect objects that exist right now. Without this,
-- the next table created by `postgres` arrives with the default grants again and
-- the hardening quietly decays.
alter default privileges for role postgres in schema public
    revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
    revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
    revoke all on routines from anon, authenticated;

-- USAGE on the schema is deliberately left alone. With no object privileges
-- there is nothing to reach through it, and revoking schema usage is the one
-- change here that could plausibly disturb a Supabase-managed role in a way
-- that cannot be verified locally (the local stack runs only Postgres - Auth is
-- not started, see the spec's §11.3).

-- ms_graph_tokens was replaced by connected_accounts, which lifted the
-- one-Microsoft-account-per-user limit its `user_id uuid primary key` imposed.
-- 20261009000002 copies every row across before this point, so dropping it here
-- cannot lose tokens regardless of what the table held.
--
-- Worth dropping rather than leaving: it still looks exactly like a live token
-- store, so it invites someone to add a policy or write to it, and it keeps a
-- second copy of encrypted refresh tokens around for no reason.
drop table if exists public.ms_graph_tokens;
