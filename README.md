# GameStore — Цикл схемы БД: PostgreSQL → ClickHouse

Проект реализует полный цикл хранения и переноса данных для магазина цифровых игр (аналог Steam, малый масштаб).

## Архитектура

```
PostgreSQL (OLTP)
├── genres, developers          ← справочники
├── users, games                ← каталог
├── purchases                   ← факты покупок
├── reviews                     ← отзывы
└── game_sessions               ← игровые сессии
          │
          │  ETL (etl/etl.py)
          ▼
ClickHouse — слои DWH:
  tmp.*   временный буфер
  raw.*   сырые копии таблиц
  mart.*  витрины для аналитики
```

## Структура репозитория

```
gamestore/
├── postgres/
│   ├── 01_schema.sql     # DDL: создание таблиц в PostgreSQL
│   └── 02_seed.sql       # Тестовые данные
├── clickhouse/
│   └── 01_schema.sql     # DDL: слои TMP, RAW, MART в ClickHouse
├── etl/
│   └── etl.py            # ETL-скрипт: PostgreSQL → ClickHouse
├── .env.example          # Шаблон переменных окружения
└── README.md
```

## Запуск

### 1. Установка зависимостей

```bash
pip install psycopg2-binary clickhouse-driver python-dotenv
```

### 2. Настройка окружения

```bash
cp .env.example .env
# Заполните .env своими данными подключения
```

### 3. Создание схемы PostgreSQL

```bash
psql -U postgres -d gamestore -f postgres/01_schema.sql
psql -U postgres -d gamestore -f postgres/02_seed.sql
```

### 4. Создание схемы ClickHouse

```bash
clickhouse-client --multiquery < clickhouse/01_schema.sql
```

### 5. Запуск ETL

```bash
# Полный цикл
python etl/etl.py --step all

# Только перенос в RAW (за последние 7 дней)
python etl/etl.py --step raw

# Только построение витрин
python etl/etl.py --step mart

# Указать дату начала выборки
python etl/etl.py --step all --since 2024-01-01
```

## Схема PostgreSQL

### Таблицы-справочники

| Таблица | Описание |
|---|---|
| `genres` | Жанры игр (Action, RPG, Strategy…) |
| `developers` | Разработчики игр |

### Основные таблицы

| Таблица | Описание |
|---|---|
| `users` | Пользователи магазина |
| `games` | Каталог игр |
| `purchases` | Факты покупок |
| `reviews` | Отзывы и рейтинги (1–10) |
| `game_sessions` | Игровые сессии с длительностью |

## Схема ClickHouse

### Слой TMP
Временные таблицы для буферизации данных при загрузке. После успешного переноса в RAW выполняется `TRUNCATE`.

### Слой RAW
Точные копии таблиц из PostgreSQL. Движок `ReplacingMergeTree` — корректно обрабатывает обновления из источника. Партиционирование по месяцам.

### Слой MART (витрины)

| Витрина | Описание |
|---|---|
| `mart.fact_purchases` | Продажи с денормализованными данными об игре и пользователе |
| `mart.fact_reviews` | Отзывы с данными об игре, жанре, пользователе |
| `mart.fact_sessions` | Сессии с длительностью, готовые к агрегации |

## Ключевые решения

**ReplacingMergeTree** — выбран для RAW и витрин вместо обычного MergeTree, так как данные в PostgreSQL могут обновляться. Оставляет только последнюю версию записи по ключу `ORDER BY`.

**LowCardinality** — применён для полей с малым числом уникальных значений (`genre_name`, `currency`, `country`). Даёт сжатие до 10x и ускорение GROUP BY.

**PARTITION BY toYYYYMM()** — партиционирование по месяцам позволяет читать только нужный диапазон дат вместо всей таблицы.

**Decimal(8,2) для цен** — никакого Float64. Гарантирует точность финансовых расчётов на всём протяжении цикла.

**Array(LowCardinality(String)) для platform** — игра поддерживает несколько платформ. Позволяет фильтровать: `WHERE has(platform, 'windows')`.
