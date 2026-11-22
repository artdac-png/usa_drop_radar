-- Выполни этот SQL один раз в Supabase -> SQL Editor -> New query -> Run

-- 1. Новые колонки в tracked_items (минимум цены, наличие, все доступные размеры, отслеживаемые размеры)
ALTER TABLE tracked_items
  ADD COLUMN IF NOT EXISTS min_price TEXT,
  ADD COLUMN IF NOT EXISTS in_stock BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS available_sizes JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS target_sizes JSONB DEFAULT '[]'::jsonb;

-- 2. Ссылка на товар в posted_deals (для /search и кнопки «➕ Отслеживать» под постами в канале)
ALTER TABLE posted_deals
  ADD COLUMN IF NOT EXISTS url TEXT;

-- 3. Таблица личных алертов по ключевым словам (/alert)
CREATE TABLE IF NOT EXISTS user_alerts (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  keyword TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, keyword)
);
