-- Generische Key-Value-Settings für Runtime-Toggles (Mail-Pipeline, Feature-Flags, ...).
-- Vorteil gegenüber spaltenbasierten Settings: neue Toggles ohne ALTER TABLE.

create table if not exists app_settings (
  key         text primary key,
  value       text not null default '',
  description text,
  updated_at  timestamptz not null default now(),
  updated_by  text
);

create or replace function app_settings_set_updated_at()
returns trigger as $$
begin
  new.updated_at := now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists app_settings_updated_at on app_settings;
create trigger app_settings_updated_at
  before update on app_settings
  for each row execute function app_settings_set_updated_at();

-- Mail-Pipeline-Defaults seeden (idempotent)
insert into app_settings (key, value, description) values
  ('mail.auto_classify',     'true',  'KI-Klassifikation direkt beim IMAP-Pull'),
  ('mail.auto_convert',      'false', 'Bei high-Konfidenz + Domain-Match Auftrag automatisch anlegen'),
  ('mail.auto_convert_min_confidence', 'high', 'Mindest-Konfidenz für Auto-Convert (high/medium/low)')
on conflict (key) do nothing;
