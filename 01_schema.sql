-- =============================================================================
-- GameStore: PostgreSQL Schema (OLTP)
-- =============================================================================

-- Расширение для UUID
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -----------------------------------------------------------------------------
-- Справочники
-- -----------------------------------------------------------------------------

CREATE TABLE genres (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE
);

CREATE TABLE developers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT    NOT NULL,
    country     CHAR(2),
    founded_at  DATE
);

-- -----------------------------------------------------------------------------
-- Пользователи
-- -----------------------------------------------------------------------------

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      TEXT           NOT NULL UNIQUE,
    email         TEXT           NOT NULL UNIQUE,
    country       CHAR(2),
    balance       NUMERIC(10, 2) NOT NULL DEFAULT 0,
    registered_at TIMESTAMPTZ    NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Каталог игр
-- -----------------------------------------------------------------------------

CREATE TABLE games (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title         TEXT           NOT NULL,
    developer_id  UUID           NOT NULL REFERENCES developers(id),
    genre_id      INT            NOT NULL REFERENCES genres(id),
    price         NUMERIC(8, 2)  NOT NULL,
    release_date  DATE,
    platform      TEXT[]         NOT NULL DEFAULT '{}',
    is_active     BOOLEAN        NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX idx_games_genre    ON games(genre_id);
CREATE INDEX idx_games_developer ON games(developer_id);

-- -----------------------------------------------------------------------------
-- Покупки (факты)
-- -----------------------------------------------------------------------------

CREATE TABLE purchases (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID           NOT NULL REFERENCES users(id),
    game_id      UUID           NOT NULL REFERENCES games(id),
    price_paid   NUMERIC(8, 2)  NOT NULL,
    discount_pct SMALLINT       NOT NULL DEFAULT 0
                                CHECK (discount_pct BETWEEN 0 AND 100),
    currency     CHAR(3)        NOT NULL DEFAULT 'USD',
    purchased_at TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX idx_purchases_user ON purchases(user_id);
CREATE INDEX idx_purchases_game ON purchases(game_id);
CREATE INDEX idx_purchases_at   ON purchases(purchased_at);

-- Уникальность: один пользователь — одна игра
CREATE UNIQUE INDEX idx_purchases_unique ON purchases(user_id, game_id);

-- -----------------------------------------------------------------------------
-- Отзывы
-- -----------------------------------------------------------------------------

CREATE TABLE reviews (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id),
    game_id     UUID        NOT NULL REFERENCES games(id),
    rating      SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 10),
    body        TEXT,
    is_positive BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Один пользователь — один отзыв на игру
    UNIQUE (user_id, game_id)
);

CREATE INDEX idx_reviews_game ON reviews(game_id);

-- -----------------------------------------------------------------------------
-- Игровые сессии
-- -----------------------------------------------------------------------------

CREATE TABLE game_sessions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES users(id),
    game_id      UUID        NOT NULL REFERENCES games(id),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ,
    duration_min INT         GENERATED ALWAYS AS (
                     EXTRACT(EPOCH FROM (ended_at - started_at)) / 60
                 )::INT STORED,
    CONSTRAINT ended_after_started CHECK (ended_at IS NULL OR ended_at > started_at)
);

CREATE INDEX idx_sessions_user ON game_sessions(user_id);
CREATE INDEX idx_sessions_game ON game_sessions(game_id);
CREATE INDEX idx_sessions_at   ON game_sessions(started_at);
