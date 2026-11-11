-- Marketplace Data Bridge — schemat bazowy (idempotentny).
-- Działa na Supabase oraz na czystym Postgresie po wgraniu worker/dev/local_auth_shim.sql.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- organizacje
create table if not exists organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists organization_members (
  organization_id uuid not null references organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('owner', 'member')),
  created_at timestamptz not null default now(),
  primary key (organization_id, user_id)
);
create index if not exists organization_members_user_idx on organization_members(user_id);

-- SECURITY DEFINER, aby polityka na organization_members nie wpadała w rekurencję.
create or replace function is_org_member(org uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from organization_members m
    where m.organization_id = org and m.user_id = auth.uid()
  );
$$;

-- ------------------------------------------------------------------ słowniki
create table if not exists marketplace_targets (
  id text primary key,
  name text not null,
  platform text not null default 'mirakl',
  note text
);
insert into marketplace_targets (id, name, note) values
  ('empik', 'Empik', 'Wymaga szablonu pobranego z panelu sprzedawcy.'),
  ('media_expert', 'Media Expert', 'Wymaga szablonu pobranego z panelu sprzedawcy.'),
  ('castorama', 'Castorama', 'Wymaga szablonu pobranego z panelu sprzedawcy.')
on conflict (id) do nothing;

-- -------------------------------------------------------------------- importy
create table if not exists imports (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  filename text not null,
  storage_path text not null,
  sha256 text not null,
  format text not null,
  size_bytes bigint not null,
  status text not null default 'queued',
  parse_options jsonb not null default '{}'::jsonb,
  detected jsonb not null default '{}'::jsonb,
  columns jsonb not null default '[]'::jsonb,
  column_map jsonb not null default '{}'::jsonb,
  column_map_confirmed boolean not null default false,
  row_count integer not null default 0,
  error text,
  created_by uuid,
  created_at timestamptz not null default now()
);
create index if not exists imports_org_idx on imports(organization_id, created_at desc);

create table if not exists import_rows (
  id bigserial primary key,
  organization_id uuid not null references organizations(id) on delete cascade,
  import_id uuid not null references imports(id) on delete cascade,
  row_number integer not null,
  raw jsonb not null,
  unique (import_id, row_number)
);

create table if not exists canonical_products (
  id bigserial primary key,
  organization_id uuid not null references organizations(id) on delete cascade,
  import_id uuid not null references imports(id) on delete cascade,
  row_number integer not null,
  sku text,
  gtin text,
  title text,
  category_path text,
  data jsonb not null,
  source_issues jsonb not null default '[]'::jsonb,
  unique (import_id, row_number)
);
create index if not exists canonical_products_org_idx on canonical_products(organization_id);

create table if not exists source_mapping_profiles (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  name text not null,
  column_map jsonb not null,
  parse_options jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (organization_id, name)
);

-- ------------------------------------------------------------------- szablony
create table if not exists target_templates (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  marketplace_id text not null references marketplace_targets(id),
  name text not null,
  version integer not null default 1,
  filename text not null,
  storage_path text not null,
  sha256 text not null,
  format text not null,
  status text not null default 'queued',
  kind text not null default 'unknown' check (kind in ('products', 'offers', 'mixed', 'unknown')),
  is_demo_fixture boolean not null default false,
  structure jsonb not null default '{}'::jsonb,
  structure_options jsonb not null default '{}'::jsonb,
  requirements jsonb not null default '{}'::jsonb,
  rule_profile jsonb not null default '{}'::jsonb,
  confirmed boolean not null default false,
  confirmed_by uuid,
  confirmed_at timestamptz,
  error text,
  created_at timestamptz not null default now(),
  unique (organization_id, marketplace_id, name, version)
);

-- ------------------------------------------------------------------- projekty
create table if not exists projects (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  name text not null,
  import_id uuid not null references imports(id) on delete cascade,
  template_id uuid not null references target_templates(id) on delete cascade,
  marketplace_id text not null references marketplace_targets(id),
  status text not null default 'needs_mapping',
  rules_version integer not null default 0,
  rules_template_sha256 text,
  last_run_version integer,
  counters jsonb not null default '{}'::jsonb,
  project_issues jsonb not null default '[]'::jsonb,
  error text,
  created_by uuid,
  created_at timestamptz not null default now()
);
create index if not exists projects_org_idx on projects(organization_id, created_at desc);

create table if not exists mapping_rules (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  project_id uuid not null references projects(id) on delete cascade,
  version integer not null,
  position integer not null,
  target_column text not null,
  steps jsonb not null,
  created_at timestamptz not null default now(),
  unique (project_id, version, target_column)
);

create table if not exists mapping_presets (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  marketplace_id text not null references marketplace_targets(id),
  name text not null,
  template_sha256 text,
  rules jsonb not null,
  created_at timestamptz not null default now(),
  unique (organization_id, name)
);

create table if not exists category_mappings (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  marketplace_id text not null references marketplace_targets(id),
  source_category text not null,
  source_key text not null,
  target_code text not null,
  target_label text,
  approved_by uuid,
  created_at timestamptz not null default now(),
  unique (organization_id, marketplace_id, source_key)
);

create table if not exists value_mappings (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  marketplace_id text not null references marketplace_targets(id),
  target_column text not null,
  category_code text not null default '',
  source_value text not null,
  source_key text not null,
  target_value text not null,
  approved_by uuid,
  created_at timestamptz not null default now(),
  unique (organization_id, marketplace_id, target_column, category_code, source_key)
);

-- ------------------------------------------------------------ wynik i korekty
create table if not exists output_rows (
  id bigserial primary key,
  organization_id uuid not null references organizations(id) on delete cascade,
  project_id uuid not null references projects(id) on delete cascade,
  row_number integer not null,
  sku text,
  gtin text,
  category text,
  status text not null,
  skipped boolean not null default false,
  "values" jsonb not null,
  provenance jsonb not null,
  unique (project_id, row_number)
);

create table if not exists value_overrides (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  project_id uuid not null references projects(id) on delete cascade,
  row_number integer not null,
  target_column text not null,
  value text not null,
  previous_value text,
  batch_id uuid,
  created_by uuid,
  created_at timestamptz not null default now(),
  reverted_at timestamptz
);
create index if not exists value_overrides_project_idx on value_overrides(project_id, row_number, target_column);

create table if not exists validation_issues (
  id bigserial primary key,
  organization_id uuid not null references organizations(id) on delete cascade,
  project_id uuid not null references projects(id) on delete cascade,
  row_number integer,
  sku text,
  gtin text,
  category text,
  target_column text,
  severity text not null check (severity in ('BLOCKER', 'REVIEW', 'WARNING')),
  code text not null,
  message text not null,
  raw_value text,
  processed_value text,
  suggestions jsonb not null default '[]'::jsonb
);
create index if not exists validation_issues_project_idx on validation_issues(project_id, severity, code);

-- --------------------------------------------------------- zadania i eksporty
create table if not exists processing_jobs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  kind text not null,
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued' check (status in ('queued', 'running', 'completed', 'failed')),
  progress integer not null default 0,
  attempts integer not null default 0,
  max_attempts integer not null default 3,
  idempotency_key text,
  error text,
  result jsonb,
  run_after timestamptz not null default now(),
  locked_at timestamptz,
  created_by uuid,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
create unique index if not exists processing_jobs_idem_idx
  on processing_jobs(organization_id, idempotency_key) where idempotency_key is not null;
create index if not exists processing_jobs_queue_idx on processing_jobs(status, run_after);

create table if not exists exports (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  project_id uuid not null references projects(id) on delete cascade,
  format text not null,
  scope text not null,
  status text not null default 'queued',
  filename text,
  storage_path text,
  report_filename text,
  report_path text,
  report_csv_filename text,
  report_csv_path text,
  counters jsonb not null default '{}'::jsonb,
  compatibility jsonb not null default '{}'::jsonb,
  error text,
  expires_at timestamptz,
  deleted_at timestamptz,
  created_by uuid,
  created_at timestamptz not null default now()
);

create table if not exists audit_events (
  id bigserial primary key,
  organization_id uuid not null references organizations(id) on delete cascade,
  user_id uuid,
  action text not null,
  entity text,
  entity_id text,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists audit_events_org_idx on audit_events(organization_id, created_at desc);

-- ------------------------------------------------------------------------ RLS
do $$
declare t text;
begin
  foreach t in array array[
    'imports', 'import_rows', 'canonical_products', 'source_mapping_profiles',
    'target_templates', 'projects', 'mapping_rules', 'mapping_presets',
    'category_mappings', 'value_mappings', 'output_rows', 'value_overrides',
    'validation_issues', 'processing_jobs', 'exports', 'audit_events'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists tenant_all on %I', t);
    execute format(
      'create policy tenant_all on %I for all to authenticated
         using (is_org_member(organization_id)) with check (is_org_member(organization_id))', t);
    execute format('grant select, insert, update, delete on %I to authenticated', t);
  end loop;
end $$;

-- Dziennik audytu: tylko dopisywanie i odczyt.
revoke update, delete on audit_events from authenticated;

alter table organizations enable row level security;
drop policy if exists org_select on organizations;
create policy org_select on organizations for select to authenticated using (is_org_member(id));
grant select on organizations to authenticated;

alter table organization_members enable row level security;
drop policy if exists members_select on organization_members;
create policy members_select on organization_members for select to authenticated
  using (is_org_member(organization_id));
grant select on organization_members to authenticated;

alter table marketplace_targets enable row level security;
drop policy if exists targets_select on marketplace_targets;
create policy targets_select on marketplace_targets for select to authenticated using (true);
grant select on marketplace_targets to authenticated;

grant usage, select on all sequences in schema public to authenticated;

-- Prywatne buckety Storage (tylko Supabase). Brak polityk dla `authenticated`:
-- pliki są dostępne wyłącznie przez backend (service_role) po sprawdzeniu członkostwa.
do $$
begin
  if exists (select 1 from information_schema.tables
             where table_schema = 'storage' and table_name = 'buckets') then
    insert into storage.buckets (id, name, public)
    values ('mdb-files', 'mdb-files', false)
    on conflict (id) do nothing;
  end if;
end $$;
