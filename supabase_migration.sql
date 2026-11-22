-- Выполни этот SQL один раз в Supabase -> SQL Editor -> New query -> Run (можно безопасно запускать повторно)

-- 1. Новые колонки в tracked_items (минимум цены, наличие, все доступные размеры, отслеживаемые размеры)
ALTER TABLE tracked_items
  ADD COLUMN IF NOT EXISTS min_price TEXT,
  ADD COLUMN IF NOT EXISTS in_stock BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS available_sizes JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS target_sizes JSONB DEFAULT '[]'::jsonb;

-- 2. Ссылка и процент скидки в posted_deals (для /search, /stats, дайджеста дня и кнопки «➕ Отслеживать»)
ALTER TABLE posted_deals
  ADD COLUMN IF NOT EXISTS url TEXT,
  ADD COLUMN IF NOT EXISTS discount_pct INTEGER;

-- 3. Таблица личных алертов по ключевым словам (/alert)
CREATE TABLE IF NOT EXISTS user_alerts (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  keyword TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, keyword)
);

-- 4. Таблица активных промокодов (переживают перезапуск бота и не дублируют уведомления)
CREATE TABLE IF NOT EXISTS active_promos (
  brand TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  pct INTEGER,
  fixed DOUBLE PRECISION,
  fixed_cur TEXT,
  source TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
