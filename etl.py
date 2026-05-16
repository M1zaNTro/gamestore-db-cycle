"""
GameStore ETL: PostgreSQL → ClickHouse
======================================
DAG 1 (postgres_to_raw): переносит данные из PostgreSQL в слои TMP → RAW.
DAG 2 (raw_to_mart):     строит витрины из RAW.

Запуск вручную (без Airflow):
    python etl/etl.py --step raw      # только postgres → raw
    python etl/etl.py --step mart     # только raw → mart
    python etl/etl.py --step all      # полный цикл

Зависимости:
    pip install psycopg2-binary clickhouse-driver python-dotenv
"""

import argparse
import logging
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from clickhouse_driver import Client as CHClient
from dotenv import load_dotenv
import os

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ── Подключения ───────────────────────────────────────────────────────────────

def pg_conn():
    return psycopg2.connect(
        host=os.getenv('PG_HOST', 'localhost'),
        port=int(os.getenv('PG_PORT', 5432)),
        dbname=os.getenv('PG_DB', 'gamestore'),
        user=os.getenv('PG_USER', 'postgres'),
        password=os.getenv('PG_PASSWORD', ''),
    )

def ch_client():
    return CHClient(
        host=os.getenv('CH_HOST', 'localhost'),
        port=int(os.getenv('CH_PORT', 9000)),
        database='default',
        user=os.getenv('CH_USER', 'default'),
        password=os.getenv('CH_PASSWORD', ''),
    )

# ── Утилиты ───────────────────────────────────────────────────────────────────

def pg_fetch(conn, query, params=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return cur.fetchall()

def ch_insert(ch, table, rows, columns):
    if not rows:
        log.info(f'  {table}: нет данных для вставки')
        return
    data = [tuple(row[c] for c in columns) for row in rows]
    ch.execute(f'INSERT INTO {table} ({", ".join(columns)}) VALUES', data)
    log.info(f'  {table}: вставлено {len(data)} строк')

# ── DAG 1: postgres → raw ─────────────────────────────────────────────────────

def load_purchases(pg, ch, since: datetime):
    log.info('Загрузка purchases...')
    rows = pg_fetch(pg, """
        SELECT id::text, user_id::text, game_id::text,
               price_paid, discount_pct, currency,
               purchased_at
        FROM purchases
        WHERE purchased_at >= %s
    """, (since,))

    # → tmp
    ch.execute('TRUNCATE TABLE tmp.purchases')
    ch_insert(ch, 'tmp.purchases', rows,
              ['id','user_id','game_id','price_paid','discount_pct','currency','purchased_at'])

    # tmp → raw
    ch.execute('INSERT INTO raw.purchases SELECT *, now() FROM tmp.purchases')
    log.info(f'  raw.purchases: перенесено из TMP')


def load_reviews(pg, ch, since: datetime):
    log.info('Загрузка reviews...')
    rows = pg_fetch(pg, """
        SELECT id::text, user_id::text, game_id::text,
               rating, is_positive::int AS is_positive,
               created_at
        FROM reviews
        WHERE created_at >= %s
    """, (since,))
    ch.execute('TRUNCATE TABLE tmp.reviews')
    ch_insert(ch, 'tmp.reviews', rows,
              ['id','user_id','game_id','rating','is_positive','created_at'])
    ch.execute('INSERT INTO raw.reviews SELECT *, now() FROM tmp.reviews')


def load_sessions(pg, ch, since: datetime):
    log.info('Загрузка game_sessions...')
    rows = pg_fetch(pg, """
        SELECT id::text, user_id::text, game_id::text,
               started_at, ended_at,
               COALESCE(duration_min, 0) AS duration_min
        FROM game_sessions
        WHERE started_at >= %s
    """, (since,))
    ch.execute('TRUNCATE TABLE tmp.game_sessions')
    ch_insert(ch, 'tmp.game_sessions', rows,
              ['id','user_id','game_id','started_at','ended_at','duration_min'])
    ch.execute('INSERT INTO raw.game_sessions SELECT *, now() FROM tmp.game_sessions')


def load_dictionaries(pg, ch):
    """Справочники грузим полностью (маленькие таблицы)."""
    log.info('Загрузка справочников...')

    # genres
    rows = pg_fetch(pg, 'SELECT id, name, slug FROM genres')
    ch_insert(ch, 'raw.genres', rows, ['id','name','slug'])

    # developers
    rows = pg_fetch(pg, """
        SELECT id::text, name, COALESCE(country,'') AS country,
               COALESCE(founded_at, '1970-01-01'::date) AS founded_at
        FROM developers
    """)
    ch_insert(ch, 'raw.developers', rows, ['id','name','country','founded_at'])

    # games
    rows = pg_fetch(pg, """
        SELECT id::text, title, developer_id::text, genre_id,
               price, platform, is_active::int AS is_active,
               updated_at
        FROM games
    """)
    ch_insert(ch, 'raw.games', rows,
              ['id','title','developer_id','genre_id','price','platform','is_active','updated_at'])

    # users
    rows = pg_fetch(pg, """
        SELECT id::text, username, COALESCE(country,'') AS country, registered_at
        FROM users
    """)
    ch_insert(ch, 'raw.users', rows, ['id','username','country','registered_at'])


def run_postgres_to_raw(since: datetime):
    log.info(f'=== DAG 1: postgres_to_raw (since={since}) ===')
    pg = pg_conn()
    ch = ch_client()
    try:
        load_dictionaries(pg, ch)
        load_purchases(pg, ch, since)
        load_reviews(pg, ch, since)
        load_sessions(pg, ch, since)
        log.info('=== DAG 1 завершён успешно ===')
    finally:
        pg.close()

# ── DAG 2: raw → mart ────────────────────────────────────────────────────────

def run_raw_to_mart():
    log.info('=== DAG 2: raw_to_mart ===')
    ch = ch_client()

    # Витрина продаж
    log.info('Строим mart.fact_purchases...')
    ch.execute("""
        INSERT INTO mart.fact_purchases
        SELECT
            toDate(p.purchased_at)                      AS purchase_date,
            formatDateTime(p.purchased_at, '%Y-%m')     AS year_month,
            p.user_id,
            u.country                                   AS user_country,
            p.game_id,
            g.title                                     AS game_title,
            gen.name                                    AS genre_name,
            dev.name                                    AS developer_name,
            g.platform,
            p.price_paid,
            p.discount_pct,
            p.currency,
            now()                                       AS _updated_at
        FROM raw.purchases p
        LEFT JOIN raw.users       u   ON p.user_id      = u.id
        LEFT JOIN raw.games       g   ON p.game_id      = g.id
        LEFT JOIN raw.genres      gen ON g.genre_id     = gen.id
        LEFT JOIN raw.developers  dev ON g.developer_id = dev.id
    """)
    log.info('  mart.fact_purchases: готово')

    # Витрина отзывов
    log.info('Строим mart.fact_reviews...')
    ch.execute("""
        INSERT INTO mart.fact_reviews
        SELECT
            toDate(r.created_at)    AS review_date,
            r.game_id,
            g.title                 AS game_title,
            gen.name                AS genre_name,
            r.user_id,
            u.country               AS user_country,
            r.rating,
            r.is_positive,
            now()                   AS _updated_at
        FROM raw.reviews r
        LEFT JOIN raw.users   u   ON r.user_id  = u.id
        LEFT JOIN raw.games   g   ON r.game_id  = g.id
        LEFT JOIN raw.genres  gen ON g.genre_id = gen.id
    """)
    log.info('  mart.fact_reviews: готово')

    # Витрина сессий
    log.info('Строим mart.fact_sessions...')
    ch.execute("""
        INSERT INTO mart.fact_sessions
        SELECT
            toDate(s.started_at)                    AS session_date,
            formatDateTime(s.started_at, '%Y-%m')   AS year_month,
            s.user_id,
            u.country                               AS user_country,
            s.game_id,
            g.title                                 AS game_title,
            gen.name                                AS genre_name,
            s.duration_min,
            now()                                   AS _updated_at
        FROM raw.game_sessions s
        LEFT JOIN raw.users   u   ON s.user_id  = u.id
        LEFT JOIN raw.games   g   ON s.game_id  = g.id
        LEFT JOIN raw.genres  gen ON g.genre_id = gen.id
    """)
    log.info('  mart.fact_sessions: готово')
    log.info('=== DAG 2 завершён успешно ===')

# ── Точка входа ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='GameStore ETL')
    parser.add_argument('--step', choices=['raw', 'mart', 'all'], default='all')
    parser.add_argument('--since', default=None,
                        help='Дата начала выборки (ISO format), по умолчанию: 7 дней назад')
    args = parser.parse_args()

    since_dt = datetime.fromisoformat(args.since) if args.since \
               else datetime.now() - timedelta(days=7)

    if args.step in ('raw', 'all'):
        run_postgres_to_raw(since_dt)

    if args.step in ('mart', 'all'):
        run_raw_to_mart()
