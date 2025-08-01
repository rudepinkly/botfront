# server.py
import os
import asyncio
import logging
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
import random
import json
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import MenuButtonWebApp, WebAppInfo

# ------- CONFIG -------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://yourfrontend.github.io/your-repo")
DB_PATH = os.environ.get("SOCIAL_DB_PATH", "social_combined.db")
DAILY_COOLDOWN = 24 * 3600  # seconds
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "*")  # tighten in prod

if not TELEGRAM_BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN не задан")
    exit(1)

# ------- Logging -------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("server")

# ------- Telegram bot setup -------
try:
    from aiogram.types import DefaultBotProperties
    from aiogram.enums.parse_mode import ParseMode

    def make_bot(token):
        return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
except ImportError:
    def make_bot(token):
        return Bot(token=token)

bot = make_bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ------ Lifespan handling ------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    polling_task = asyncio.create_task(start_bot_polling())
    app.state.polling_task = polling_task
    try:
        yield
    finally:
        if not polling_task.done():
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        try:
            await bot.session.close()
        except Exception:
            pass

# ------ FastAPI app ------
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------ DB helpers ------

@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON;")
        yield db

async def init_db():
    if getattr(app.state, "db_inited", False):
        return
    async with get_db() as db:
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
            click_power INTEGER DEFAULT 1,
            total_clicks INTEGER DEFAULT 0,
            auto_click_level INTEGER DEFAULT 0,
            last_auto_tick TEXT,
            PRIMARY KEY(user_id, chat_id)
        );

        CREATE TABLE IF NOT EXISTS admins (
            chat_id INTEGER,
            user_id INTEGER,
            granted_by INTEGER,
            granted_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(chat_id, user_id)
        );
        """)
        await db.commit()
    app.state.db_inited = True

def dict_from_row(row):
    if not row:
        return None
    return {k: row[k] for k in row.keys()}

# --- Telegram WebApp init_data verification ---
def verify_telegram_webapp_init_data(init_data: str) -> bool:
    try:
        parts = dict(pair.split("=", 1) for pair in init_data.split("\n") if "=" in pair)
        received_hash = parts.pop("hash", None)
        if not received_hash:
            return False
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parts.items()))
        secret_key = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
        hmac_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(hmac_hash, received_hash)
    except Exception:
        return False

def parse_init_data(init_data: str):
    obj = {}
    try:
        parts = dict(pair.split("=", 1) for pair in init_data.split("\n") if "=" in pair)
        if "user" in parts:
            obj = json.loads(parts["user"])
        else:
            obj = {}
    except Exception:
        obj = {}
    return obj

def compute_next_daily(last_daily_iso):
    if not last_daily_iso:
        return 0
    try:
        last = datetime.fromisoformat(last_daily_iso)
    except Exception:
        return 0
    delta = timedelta(seconds=DAILY_COOLDOWN) - (datetime.now(timezone.utc) - last)
    if delta.total_seconds() < 0:
        return 0
    return int(delta.total_seconds())

# --- Pydantic models ---
class BaseRequest(BaseModel):
    init_data: str
    chat_id: int

class UpgradeRequest(BaseRequest):
    type: str  # "click_power" / "auto_click"

# ------ API endpoints ------

@app.get("/")
async def root():
    return {"status": "ok", "msg": "Соц-рейтинг арена backend работает"}

@app.post("/api/profile")
async def profile(req: BaseRequest):
    if TELEGRAM_BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(400, "init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(400, "No user info")
    async with get_db() as db:
        row = await db.execute_fetchone(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
        )
        if not row:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, chat_id, username) VALUES (?,?,?)",
                (user_id, req.chat_id, username),
            )
            await db.commit()
            row = await db.execute_fetchone(
                "SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)
            )
        user = dict_from_row(row)
    return {"ok": True, "user": {
        "user_id": user["user_id"],
        "username": user["username"],
        "rating": user["rating"],
        "title": user["title"],
        "streak": user["daily_streak"],
        "prestige_multiplier": user["prestige_multiplier"],
        "prestige_level": user["prestige_level"],
        "stars": user["stars"],
        "click_power": user["click_power"],
        "total_clicks": user["total_clicks"],
        "auto_click_level": user["auto_click_level"],
        "next_daily_available_in": compute_next_daily(user["last_daily"]),
    }}

@app.post("/api/chat_friends")
async def chat_friends(req: BaseRequest):
    if TELEGRAM_BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(400, "init_data verification failed")
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT user_id, username, rating, title, stars FROM users WHERE chat_id=? ORDER BY rating DESC LIMIT 50",
            (req.chat_id,)
        )
        friends = []
        for r in rows:
            friends.append({
                "user_id": r["user_id"],
                "username": r["username"],
                "rating": r["rating"],
                "title": r["title"],
                "stars": r["stars"],
                "avatar_url": f"/avatar/{req.chat_id}/{r['user_id']}?init_data={req.init_data}"
            })
    return {"ok": True, "friends": friends}

@app.post("/api/daily")
async def daily(req: BaseRequest):
    if TELEGRAM_BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(400, "init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(400, "No user info")

    async with get_db() as db:
        row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, chat_id, username) VALUES (?,?,?)",
                (user_id, req.chat_id, username),
            )
            await db.commit()
            row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        user = dict_from_row(row)

        next_avail = compute_next_daily(user["last_daily"])
        if next_avail > 0:
            raise HTTPException(400, f"already_done,next_in={next_avail}")

        base = random.randint(-10, 10)
        delta = int(round(base * user["prestige_multiplier"]))
        new_rating = max(0, user["rating"] + delta)

        streak = user["daily_streak"]
        last_daily = user["last_daily"]
        now = datetime.now(timezone.utc)
        if last_daily:
            try:
                prev = datetime.fromisoformat(last_daily)
                diff = now - prev
                if 20 * 3600 <= diff.total_seconds() <= 48 * 3600:
                    streak += 1
                else:
                    streak = 1
            except Exception:
                streak = 1
        else:
            streak = 1

        await db.execute("""
            UPDATE users SET rating=?, daily_streak=?, last_daily=?
            WHERE user_id=? AND chat_id=?
        """, (new_rating, streak, now.isoformat(), user_id, req.chat_id))
        await db.commit()

        updated_row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
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
        },
    }

@app.post("/api/wheel")
async def wheel(req: BaseRequest):
    if TELEGRAM_BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(400, "init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(400, "No user info")
    async with get_db() as db:
        row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        if not row:
            await db.execute("INSERT OR IGNORE INTO users (user_id, chat_id, username) VALUES (?,?,?)",
                             (user_id, req.chat_id, username))
            await db.commit()
            row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        user = dict_from_row(row)

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

        updated = dict_from_row(await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)))

    return {
        "ok": True,
        "result": label,
        "user": {
            "rating": updated["rating"],
            "stars": updated["stars"],
            "prestige_multiplier": updated["prestige_multiplier"],
        },
    }

@app.post("/api/slot")
async def slot(req: BaseRequest):
    if TELEGRAM_BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(400, "init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(400, "No user info")
    async with get_db() as db:
        row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        if not row:
            await db.execute("INSERT OR IGNORE INTO users (user_id, chat_id, username) VALUES (?,?,?)",
                             (user_id, req.chat_id, username))
            await db.commit()
            row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        user = dict_from_row(row)

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
        updated = dict_from_row(await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)))

    return {
        "ok": True,
        "reels": [a, b, c],
        "payout": payout,
        "user": {"rating": updated["rating"]},
    }

@app.post("/api/click")
async def click(req: BaseRequest):
    if TELEGRAM_BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(400, "init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(400, "No user info")
    async with get_db() as db:
        row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        if not row:
            await db.execute("INSERT OR IGNORE INTO users (user_id, chat_id, username) VALUES (?,?,?)",
                             (user_id, req.chat_id, username))
            await db.commit()
            row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        user = dict_from_row(row)

        click_power = user.get("click_power", 1)
        new_rating = user["rating"] + click_power
        total_clicks = user.get("total_clicks", 0) + 1

        await db.execute("""
            UPDATE users SET rating=?, total_clicks=?
            WHERE user_id=? AND chat_id=?
        """, (new_rating, total_clicks, user_id, req.chat_id))
        await db.commit()

        updated = dict_from_row(await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)))

    return {
        "ok": True,
        "gain": click_power,
        "new_rating": updated["rating"],
        "total_clicks": updated["total_clicks"],
        "click_power": updated["click_power"],
        "stars": updated["stars"],
    }

@app.post("/api/upgrade")
async def upgrade(req: UpgradeRequest):
    if TELEGRAM_BOT_TOKEN and not verify_telegram_webapp_init_data(req.init_data):
        raise HTTPException(400, "init_data verification failed")
    user_obj = parse_init_data(req.init_data)
    user_id = int(user_obj.get("id") or 0)
    username = user_obj.get("username") or user_obj.get("first_name") or f"{user_id}"
    if not user_id:
        raise HTTPException(400, "No user info")

    async with get_db() as db:
        row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        if not row:
            await db.execute("INSERT OR IGNORE INTO users (user_id, chat_id, username) VALUES (?,?,?)",
                             (user_id, req.chat_id, username))
            await db.commit()
            row = await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id))
        user = dict_from_row(row)

        stars = user.get("stars", 0)
        if req.type == "click_power":
            current = user.get("click_power", 1)
            cost = (current + 1) ** 2
            if stars < cost:
                raise HTTPException(400, f"Недостаточно звёзд: нужно {cost}, есть {stars}")
            new_click_power = current + 1
            new_stars = stars - cost
            await db.execute("UPDATE users SET click_power=?, stars=? WHERE user_id=? AND chat_id=?",
                             (new_click_power, new_stars, user_id, req.chat_id))
        elif req.type == "auto_click":
            level = user.get("auto_click_level", 0)
            cost = (level + 1) * 5
            if stars < cost:
                raise HTTPException(400, f"Недостаточно звёзд: нужно {cost}, есть {stars}")
            new_level = level + 1
            new_stars = stars - cost
            await db.execute("UPDATE users SET auto_click_level=?, stars=? WHERE user_id=? AND chat_id=?",
                             (new_level, new_stars, user_id, req.chat_id))
        else:
            raise HTTPException(400, "Unknown upgrade type")
        await db.commit()
        updated = dict_from_row(await db.execute_fetchone("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, req.chat_id)))

    return {"ok": True, "user": {
        "rating": updated["rating"],
        "click_power": updated["click_power"],
        "auto_click_level": updated["auto_click_level"],
        "stars": updated["stars"],
    }}

@app.get("/api/leaderboard")
async def leaderboard(scope: str = Query("chat"), chat_id: int = Query(0), limit: int = Query(10)):
    async with get_db() as db:
        if scope == "global":
            rows = await db.execute_fetchall("SELECT username, rating, chat_id FROM users ORDER BY rating DESC LIMIT ?", (limit,))
        else:
            rows = await db.execute_fetchall("SELECT username, rating FROM users WHERE chat_id=? ORDER BY rating DESC LIMIT ?", (chat_id, limit))
        result = []
        for r in rows:
            if scope == "global":
                result.append({"username": r["username"], "rating": r["rating"], "chat_id": r["chat_id"]})
            else:
                result.append({"username": r["username"], "rating": r["rating"]})
    return {"ok": True, "leaderboard": result}

# ------ Avatar proxy with simple caching ------
_avatar_cache: dict = {}  # key: user_id, value: (redirect_url, fetched_at)

AVATAR_TTL = 300  # seconds

@app.get("/avatar/{chat_id}/{user_id}")
async def avatar_proxy(chat_id: int, user_id: int, init_data: str):
    if TELEGRAM_BOT_TOKEN and not verify_telegram_webapp_init_data(init_data):
        raise HTTPException(400, "init_data verification failed")
    now = datetime.utcnow().timestamp()
    cache_entry = _avatar_cache.get(user_id)
    if cache_entry:
        url, fetched = cache_entry
        if now - fetched < AVATAR_TTL:
            return RedirectResponse(url)

    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count == 0 or not photos.photos:
            raise ValueError("no photo")
        sizes = photos.photos[0]
        file_id = sizes[-1].file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        _avatar_cache[user_id] = (url, now)
        return RedirectResponse(url)
    except Exception:
        return RedirectResponse("https://via.placeholder.com/128?text=No+Avatar")

# ------ Telegram bot handlers (WebApp share etc.) ------

def mention(user: types.User):
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)

@dp.message(Command(commands=["start", "sr_start"]))
async def cmd_start(message: types.Message):
    try:
        await bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(text="🎮 Играть", web_app=WebAppInfo(url=f"{WEBAPP_URL}/?chat_id={message.chat.id}"))
        )
    except Exception as e:
        logger.warning("WebApp button fail: %s", e)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🚀 Открыть арену", web_app=WebAppInfo(url=f"{WEBAPP_URL}/?chat_id={message.chat.id}"))]
    ])
    await message.reply(
        f"🔥 Хай, {mention(message.from_user)}! Жми на кнопку и качай рейтинг.", 
        reply_markup=kb
    )

@dp.message(Command(commands=["help", "sr_help"]))
async def cmd_help(message: types.Message):
    await message.reply(
        "🧠 /sr_start — открыть WebApp\n"
        "Внутри: кликер, ежедневный /daily, колесо, слот, апгрейды, престиж, шар в чат.\n"
        "🔁 Нажми 'Поделиться' в игре — бот вывалит в чат твою инфу."
    )

@dp.message()
async def handle_webapp_data(message: types.Message):
    if getattr(message, "web_app_data", None):
        try:
            payload = message.web_app_data.data
            obj = json.loads(payload)
            if obj.get("action") == "share":
                user = message.from_user
                text = obj.get("text", "")
                await message.reply(f"🎯 {mention(user)} поделился: {text}")
                return
        except Exception:
            pass
    if message.text and message.text.startswith("/"):
        await message.reply("Команда неизвестна. /sr_start — открыть игру.")
    else:
        await message.reply("Жми /sr_start чтобы играть в соц-ростилку.")

# запускаем polling
async def start_bot_polling():
    logger.info("Старт polling бота")
    await dp.start_polling(bot)
