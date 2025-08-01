# api.py
import os
import hmac
import hashlib
import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

DB_PATH = os.environ.get("SOCIAL_DB_PATH", "social_rating_frame.db")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DAILY_COOLDOWN = 24 * 3600  # сек

# --- утилиты для Telegram WebApp init_data ---
def verify_telegram_webapp_init_data(init_data: str) -> bool:
    # init_data строка вида "key1=value1\nkey2=value2\n...&hash=..."
    # Телеграм передаёт в query-параметре или как объект, нужно извлечь hash отдельно
    parts = dict(pair.split("=", 1) for pair in init_data.split("\n") if "=" in pair)
    received_hash = parts.pop("hash", None)
    if not received_hash:
        return False
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parts.items()))
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    hmac_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(hmac_hash, received_hash)

def parse_init_data(init_data: str):
    # извлечь user_id и username из init_data (если есть)
    parts = dict(pair.split("=", 1) for pair in init_data.split("\n") if "=" in pair)
    user = {}
    if "user" in parts:
        # иногда Telegram отдаёт JSON in init_dataUnsafe; fallback не строгий
        try:
            import json
            user = json.loads(parts["user"])
        except:
            user = {}
    return user

# --- Модели ---
class ProfileRequest(BaseModel):
    init_data: str
    chat_id: int

class DuelRequest(BaseModel):
    init_data: str
    chat_id: int
    target_id: int

class DailyRequest(BaseModel):
    init_data: str
    chat_id: int

# --- DB helpers ---
async def get_conn():
    conn = await aiosqlite.connect(DB_PATH)
    await conn.execute("PRAGMA foreign_keys=ON;")
    return conn

async def init_db():
    async with await get_conn() as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER,
            chat_id INTEGER,
            username TEXT,
            rating INTEGER DEFAULT 100,
            title TEXT DEFAULT 'Новичок',
            daily_streak INTEGER DEFAULT 0,
            last_daily TEXT,
            prestige_multiplier REAL DEFAULT 1.0,
            prestige_level INTEGER DEFAULT 0,
            stars INTEGER DEFAULT 0,
            shield_until TEXT,
            PRIMARY KEY(user_id, chat_id)
        );
        """)
        await db.commit()

async def get_or_create_user(user_id: int, chat_id: int, username: str):
    async with await get_conn() as db:
        row = await db.execute_fetchone(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        )
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, chat_id, username) VALUES (?, ?, ?)",
                (user_id, chat_id, username)
            )
            await db.commit()
            row = await db.execute_fetchone(
                "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id)
            )
        return dict_from_row(row)

def dict_from_row(row):
    if not row:
        return None
    cols = ["user_id","chat_id","username","rating","title","daily_streak","last_daily","prestige_multiplier","prestige_level","stars","shield_until"]
    return {k: row[i] for i, k in enumerate(cols)}

# --- эндпоинты ---
@app.on_event("startup")
async def startup():
    await init_db()

@app.post("/api/profile")
async def profile(req: ProfileRequest):
    if BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(status_code=400, detail="init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(status_code=400, detail="No user info")
    user = await get_or_create_user(user_id, req.chat_id, username)
    return {
        "ok": True,
        "user": {
            "user_id": user["user_id"],
            "username": user["username"],
            "rating": user["rating"],
            "title": user["title"],
            "streak": user["daily_streak"],
            "prestige_multiplier": user["prestige_multiplier"],
            "prestige_level": user["prestige_level"],
            "stars": user["stars"],
            "shield_until": user["shield_until"],
            "next_daily_available_in": compute_next_daily(user["last_daily"]),
        }
    }

def compute_next_daily(last_daily_iso):
    if not last_daily_iso:
        return 0
    try:
        last = datetime.fromisoformat(last_daily_iso)
    except:
        return 0
    delta = timedelta(seconds=DAILY_COOLDOWN) - (datetime.now(timezone.utc) - last)
    if delta.total_seconds() < 0:
        return 0
    return int(delta.total_seconds())

@app.post("/api/daily")
async def daily(req: DailyRequest):
    if BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(status_code=400, detail="init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(status_code=400, detail="No user info")
    async with await get_conn() as db:
        row = await db.execute_fetchone(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
        )
        if row:
            user = dict_from_row(row)
        else:
            await db.execute(
                "INSERT INTO users (user_id, chat_id, username) VALUES (?, ?, ?)",
                (user_id, req.chat_id, username)
            )
            await db.commit()
            row = await db.execute_fetchone(
                "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
            )
            user = dict_from_row(row)

        # cooldown
        next_avail = compute_next_daily(user["last_daily"])
        if next_avail > 0:
            raise HTTPException(status_code=400, detail=f"already_done, next_in={next_avail}")

        # изменение рейтинга: -10..+10 умноженное на prestige_multiplier
        import random
        base = random.randint(-10, 10)
        delta = int(round(base * user["prestige_multiplier"]))
        new_rating = max(0, user["rating"] + delta)

        # стрик
        streak = user["daily_streak"]
        last_daily = user["last_daily"]
        now = datetime.now(timezone.utc)
        if last_daily:
            try:
                prev = datetime.fromisoformat(last_daily)
                diff = now - prev
                if 20*3600 <= diff.total_seconds() <= 48*3600:
                    streak += 1
                else:
                    streak = 1
            except:
                streak = 1
        else:
            streak = 1

        # сохранить
        await db.execute("""
            UPDATE users SET rating=?, daily_streak=?, last_daily=?
            WHERE user_id=? AND chat_id=?
        """, (new_rating, streak, now.isoformat(), user_id, req.chat_id))
        await db.commit()

        updated_row = await db.execute_fetchone(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
        )
        updated = dict_from_row(updated_row)

    return {
        "ok": True,
        "daily": {
            "delta": delta,
            "new_rating": updated["rating"],
            "streak": updated["daily_streak"],
        },
        "user": {
            "rating": updated["rating"],
            "prestige_multiplier": updated["prestige_multiplier"],
            "prestige_level": updated["prestige_level"],
            "stars": updated["stars"],
            "next_daily_available_in": compute_next_daily(updated["last_daily"]),
        }
    }

@app.post("/api/wheel")
async def wheel(req: ProfileRequest):
    if BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(status_code=400, detail="init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(status_code=400, detail="No user info")
    async with await get_conn() as db:
        row = await db.execute_fetchone(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
        )
        if not row:
            await get_or_create_user(user_id, req.chat_id, username)
            row = await db.execute_fetchone(
                "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
            )
        user = dict_from_row(row)

        # простая логика: случайный приз
        import random
        prize_pool = [
            ("+50 рейтинга", {"rating": 50}),
            ("+1 звёздочка", {"stars": 1}),
            ("x1.1 престиж", {"prestige_multiplier": 1.1}),
            ("ничего", {}),
            ("+100 рейтинга", {"rating": 100}),
            ("+2 звёздочки", {"stars": 2}),
        ]
        label, effect = random.choice(prize_pool)
        new_rating = user["rating"]
        new_stars = user["stars"]
        new_prestige_mul = user["prestige_multiplier"]

        if "rating" in effect:
            new_rating += effect["rating"]
        if "stars" in effect:
            new_stars += effect["stars"]
        if "prestige_multiplier" in effect:
            new_prestige_mul *= effect["prestige_multiplier"]

        await db.execute("""
            UPDATE users SET rating=?, stars=?, prestige_multiplier=?
            WHERE user_id=? AND chat_id=?
        """, (new_rating, new_stars, new_prestige_mul, user_id, req.chat_id))
        await db.commit()

        updated_row = await db.execute_fetchone(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
        )
        updated = dict_from_row(updated_row)

    return {
        "ok": True,
        "result": label,
        "user": {
            "rating": updated["rating"],
            "stars": updated["stars"],
            "prestige_multiplier": updated["prestige_multiplier"],
        }
    }

@app.post("/api/slot")
async def slot(req: ProfileRequest):
    if BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(status_code=400, detail="init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(status_code=400, detail="No user info")
    async with await get_conn() as db:
        row = await db.execute_fetchone(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
        )
        if not row:
            await get_or_create_user(user_id, req.chat_id, username)
            row = await db.execute_fetchone(
                "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
            )
        user = dict_from_row(row)

        import random
        symbols = ["🍒", "🍋", "🔔", "⭐", "7️⃣"]
        a = random.choice(symbols)
        b = random.choice(symbols)
        c = random.choice(symbols)
        payout = 0
        if a == b == c:
            payout = 200
        elif a == b or b == c or a == c:
            payout = 50

        new_rating = user["rating"] + payout
        await db.execute("UPDATE users SET rating=? WHERE user_id=? AND chat_id=?", (new_rating, user_id, req.chat_id))
        await db.commit()

        updated_row = await db.execute_fetchone(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
        )
        updated = dict_from_row(updated_row)

    return {
        "ok": True,
        "reels": [a, b, c],
        "payout": payout,
        "user": {
            "rating": updated["rating"]
        }
    }
