-- Story persistence — Supabase single-table JSONB + RLS skeleton.
--
-- This migration lands the cloud schema for the COMMERCIAL build only. The
-- open-source build persists stories browser-locally (IndexedDB) and never
-- reaches this table. Cloud sync is NOT wired to any client this phase — the
-- table + RLS exist so next-phase local-first bidirectional sync can layer on
-- without a schema change.
--
-- One row mirrors the local StoryRecord's shared SlimStoryBlob payload
-- (lib/persistence/types.ts): list-view metadata is denormalized into columns
-- and the slim Session lives in session_jsonb. Per-user isolation is enforced
-- entirely by RLS (auth.uid() = user_id) against the SSR client's anon key +
-- user cookie — no service_role key is used.
--
-- Idempotent: safe to re-run. Tables/indexes use `if not exists`; policies are
-- dropped-then-created (Postgres has no `create policy if not exists`).

create table if not exists public.stories (
  id            text not null,                                     -- = Session.id ("s_xxx"), unique only per user
  user_id       uuid not null references auth.users (id) on delete cascade,
  world_setting text not null default '',
  style_guide   text not null default '',
  orientation   text not null default 'landscape',                 -- "portrait" | "landscape"
  scene_count   integer not null default 0,
  rev           integer not null default 1,                        -- revision; new = 1, +1 per save
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz,                                       -- soft-delete tombstone; null = live
  session_jsonb jsonb not null,                                    -- slim Session blob (voice + styleReferenceImage stripped)
  -- Composite PK: a random Session.id ("s_xxx") is unique only within a user, so
  -- scope the key by user_id — otherwise a cross-user id collision would reject
  -- the second user's save with a PK violation.
  primary key (user_id, id)
);

-- List query path: a user's stories newest-first.
create index if not exists stories_user_updated_idx
  on public.stories (user_id, updated_at desc);

alter table public.stories enable row level security;

-- Authenticated users may read/write ONLY their own rows. Four policies, one
-- per command, all keyed on auth.uid() = user_id.
drop policy if exists "stories_select_own" on public.stories;
create policy "stories_select_own" on public.stories
  for select using (auth.uid() = user_id);

drop policy if exists "stories_insert_own" on public.stories;
create policy "stories_insert_own" on public.stories
  for insert with check (auth.uid() = user_id);

drop policy if exists "stories_update_own" on public.stories;
create policy "stories_update_own" on public.stories
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "stories_delete_own" on public.stories;
create policy "stories_delete_own" on public.stories
  for delete using (auth.uid() = user_id);
