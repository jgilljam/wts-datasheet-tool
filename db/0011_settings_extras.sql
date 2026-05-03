-- Ergänzt company_settings um Felder, die im neuen Beleg-Footer genutzt werden
alter table company_settings
  add column if not exists fax        text,
  add column if not exists tax_office text,           -- z.B. "Finanzamt Mönchengladbach-Mitte"
  add column if not exists eori       text,           -- Zoll-EORI-Nr für DAP/DDP-Lieferungen
  add column if not exists website    text,
  add column if not exists logo_path  text;
