-- schema.sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER,
    chat_id INTEGER,
    username TEXT,
    rating INTEGER DEFAULT 100,
    title TEXT DEFAULT 'Новичок',
    daily_streak INTEGER DEFAULT 0,
    last_daily TEXT,
    prestige_multiplier REAL DEFAULT 1.0,
    shield_until TEXT,
    next_daily_multiplier REAL DEFAULT 1.0,
    PRIMARY KEY (user_id, chat_id)
);

CREATE TABLE IF NOT EXISTS crews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    name TEXT UNIQUE,
    owner_user_id INTEGER,
    rating_bonus REAL DEFAULT 0.0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS crew_members (
    crew_id INTEGER,
    user_id INTEGER,
    FOREIGN KEY (crew_id) REFERENCES crews(id)
);

CREATE TABLE IF NOT EXISTS admins (
    chat_id INTEGER,
    user_id INTEGER,
    granted_by INTEGER,
    granted_at TEXT,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS battles (
    battle_id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    challenger_id INTEGER,
    target_id INTEGER,
    status TEXT DEFAULT 'pending', -- pending, accepted, declined, resolved
    created_at TEXT,
    winner_id INTEGER,
    loser_id INTEGER,
    stolen INTEGER,
    shield_blocked INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS achievements (
    user_id INTEGER,
    chat_id INTEGER,
    name TEXT,
    achieved_at TEXT
);

CREATE TABLE IF NOT EXISTS history (
    user_id INTEGER,
    chat_id INTEGER,
    type TEXT,
    delta INTEGER,
    ts TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS events (
    name TEXT,
    active INTEGER DEFAULT 0,
    daily_multiplier REAL,
    started_at TEXT,
    ends_at TEXT
);
