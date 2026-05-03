-- ============================================================
-- 0018_users_auth.sql — Multi-User-Authentifizierung
-- ============================================================
--   Ersetzt das Single-Password-Modell durch eine Users-Tabelle:
--     - bcrypt-gehashtes Password (NICHT plain in DB)
--     - optional TOTP-Secret für 2FA
--     - Rollen (admin/mitarbeiter/viewer) — vorbereitet, noch nicht
--       enforced
--     - Failed-Login-Counter mit automatischer Sperre
--     - last_login_at für Aktivitäts-Übersicht
--
--   auth_events: Append-only-Log aller Login-Versuche (Erfolg + Fail).
--   Trigger blockt UPDATE/DELETE.
--
-- Idempotent.
-- ============================================================

create table if not exists users (
  id                    uuid primary key default gen_random_uuid(),
  email                 text not null unique,
  full_name             text,
  password_hash         text not null,             -- bcrypt
  totp_secret           text,                      -- base32, NULL = kein 2FA
  totp_enabled          boolean not null default false,
  role                  text not null default 'mitarbeiter'
    check (role in ('admin', 'mitarbeiter', 'viewer')),
  is_active             boolean not null default true,
  last_login_at         timestamptz,
  failed_login_count    int not null default 0,
  locked_until          timestamptz,               -- bei zu vielen Fails
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists users_email_active_idx on users(email) where is_active = true;

create trigger users_updated_at
  before update on users for each row execute function set_updated_at();


-- Auth-Events (append-only)
create table if not exists auth_events (
  id            bigserial primary key,
  at            timestamptz not null default now(),
  email         text,                              -- Login-Versuch (auch bei Fail)
  user_id       uuid references users(id) on delete set null,
  event_type    text not null
    check (event_type in (
      'login_success', 'login_fail_password', 'login_fail_totp',
      'login_fail_locked', 'login_fail_inactive', 'login_fail_unknown_email',
      'logout', 'totp_enabled', 'totp_disabled', 'password_changed',
      'user_created', 'user_deactivated', 'user_reactivated', 'role_changed'
    )),
  ip_address    text,                              -- Streamlit liefert das nicht direkt — best-effort
  user_agent    text,
  payload       jsonb not null default '{}'::jsonb
);

create index if not exists auth_events_email_idx on auth_events(email, at desc);
create index if not exists auth_events_user_idx  on auth_events(user_id, at desc);
create index if not exists auth_events_at_idx    on auth_events(at desc);


-- Append-only-Trigger
create or replace function _gobd_auth_events_append_only()
returns trigger language plpgsql as $$
begin
  if TG_OP = 'INSERT' then
    return NEW;
  end if;
  raise exception 'auth_events ist append-only — % nicht erlaubt', TG_OP
    using errcode = '42501';
end $$;

drop trigger if exists trg_auth_events_append_only on auth_events;
create trigger trg_auth_events_append_only
  before update or delete on auth_events
  for each row execute function _gobd_auth_events_append_only();
