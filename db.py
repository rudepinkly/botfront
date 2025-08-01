# db.py
import aiosqlite
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")
DB_PATH = "social_rating.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        with open("schema.sql", encoding="utf-8") as f:
            await db.executescript(f.read())
        await db.commit()


def now_iso():
    return datetime.now(KYIV_TZ).isoformat()


# Пользователи

async def register_or_update_user(user_id, chat_id, username):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, chat_id, username)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET username=excluded.username
        """, (user_id, chat_id, username))
        await db.commit()


async def get_user(user_id, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        row = await cur.fetchone()
        return dict(row) if row else None


async def leaderboard(chat_id, limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT username, rating, user_id FROM users WHERE chat_id=? ORDER BY rating DESC LIMIT ?",
            (chat_id, limit)
        )
        rows = await cur.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]


# Перевод рейтинга

async def transfer_rating(sender_id, target_id, chat_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT rating FROM users WHERE user_id=? AND chat_id=?", (sender_id, chat_id))
        row = await cur.fetchone()
        if not row:
            return False, "no_sender"
        sender_rating = row[0]
        if sender_rating < amount:
            return False, "not_enough"
        cur2 = await db.execute("SELECT rating FROM users WHERE user_id=? AND chat_id=?", (target_id, chat_id))
        row2 = await cur2.fetchone()
        if not row2:
            return False, "no_target"
        target_rating = row2[0]
        # списать и добавить
        await db.execute("UPDATE users SET rating=? WHERE user_id=? AND chat_id=?", (sender_rating - amount, sender_id, chat_id))
        await db.execute("UPDATE users SET rating=? WHERE user_id=? AND chat_id=?", (target_rating + amount, target_id, chat_id))
        ts = now_iso()
        await db.execute(
            "INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
            (sender_id, chat_id, "gift_sent", -amount, ts, f"to {target_id}")
        )
        await db.execute(
            "INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
            (target_id, chat_id, "gift_received", amount, ts, f"from {sender_id}")
        )
        await db.commit()
        return True, None


# Daily change

async def apply_daily_change(user_id, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))).fetchone()
        if not user:
            return None, "no_user"
        last_daily = user["last_daily"]
        today = datetime.now(KYIV_TZ).date()
        if last_daily:
            try:
                prev = datetime.fromisoformat(last_daily).astimezone(KYIV_TZ).date()
                if prev == today:
                    return None, "already_done"
            except:
                pass
        base = random.randint(-10, 10)
        prestige_mul = user["prestige_multiplier"] or 1.0
        event_mult = 1.0
        ev = await (await db.execute("SELECT * FROM events WHERE active=1")).fetchone()
        if ev:
            event_mult = ev["daily_multiplier"]
        delta = int(base * prestige_mul * event_mult)
        new_rating = user["rating"] + delta
        if new_rating < 0:
            new_rating = 0
        streak = user["daily_streak"]
        if last_daily:
            try:
                prev = datetime.fromisoformat(last_daily).astimezone(KYIV_TZ).date()
                if (today - prev).days == 1:
                    streak += 1
                else:
                    streak = 1
            except:
                streak = 1
        else:
            streak = 1
        await db.execute("""
            UPDATE users SET rating=?, last_daily=?, daily_streak=?, next_daily_multiplier=1.0 WHERE user_id=? AND chat_id=?
        """, (new_rating, datetime.now(KYIV_TZ).isoformat(), streak, user_id, chat_id))
        ts = now_iso()
        await db.execute(
            "INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
            (user_id, chat_id, "daily", delta, ts, f"streak={streak}")
        )
        await db.commit()
        updated = await get_user(user_id, chat_id)
        return {
            "delta": delta,
            "new_rating": updated["rating"],
            "streak": streak,
            "prestige_multiplier": prestige_mul
        }, None


# Админы

async def is_admin(user_id, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM admins WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        row = await cur.fetchone()
        return bool(row)


async def add_admin(chat_id, user_id, granted_by):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO admins (chat_id, user_id, granted_by, granted_at)
            VALUES (?, ?, ?, ?)
        """, (chat_id, user_id, granted_by, now_iso()))
        await db.commit()


async def remove_admin(chat_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        await db.commit()


async def list_admins(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, granted_by, granted_at FROM admins WHERE chat_id=?", (chat_id,))
        return await cur.fetchall()


# Кланы

async def create_crew(owner_id, chat_id, name):
    async with aiosqlite.connect(DB_PATH) as db:
        now = now_iso()
        try:
            await db.execute("""
                INSERT INTO crews (chat_id, name, owner_user_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (chat_id, name, owner_id, now))
            cur = await db.execute("SELECT id FROM crews WHERE name=?", (name,))
            row = await cur.fetchone()
            crew_id = row[0]
            await db.execute("INSERT INTO crew_members (crew_id, user_id) VALUES (?,?)", (crew_id, owner_id))
            await db.commit()
            return True, None
        except aiosqlite.IntegrityError:
            return False, "exists"


async def join_crew(user_id, chat_id, name):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM crews WHERE name=? AND chat_id=?", (name, chat_id))
        crew = await cur.fetchone()
        if not crew:
            return False, "no_crew"
        crew_id = crew[0]
        await db.execute("INSERT OR IGNORE INTO crew_members (crew_id, user_id) VALUES (?,?)", (crew_id, user_id))
        await db.commit()
        return True, None


async def leave_crew(user_id, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT c.id FROM crews c
            JOIN crew_members m ON m.crew_id=c.id
            WHERE m.user_id=? AND c.chat_id=?
        """, (user_id, chat_id))
        row = await cur.fetchone()
        if row:
            crew_id = row[0]
            await db.execute("DELETE FROM crew_members WHERE crew_id=? AND user_id=?", (crew_id, user_id))
            await db.commit()


async def get_user_crew(user_id, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT c.* FROM crews c
            JOIN crew_members m ON m.crew_id=c.id
            WHERE m.user_id=? AND c.chat_id=?
        """, (user_id, chat_id))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_crew_info(name):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM crews WHERE name=?", (name,))
        row = await cur.fetchone()
        return dict(row) if row else None


# Достижения и история

async def add_achievement(user_id, chat_id, name):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM achievements WHERE user_id=? AND chat_id=? AND name=?",
            (user_id, chat_id, name)
        )
        if await cur.fetchone():
            return False
        await db.execute(
            "INSERT INTO achievements (user_id, chat_id, name, achieved_at) VALUES (?,?,?,?)",
            (user_id, chat_id, name, now_iso())
        )
        await db.commit()
        return True


async def get_achievements(user_id, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT name, achieved_at FROM achievements WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        return await cur.fetchall()


async def get_history(user_id, chat_id, limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT type, delta, ts, details FROM history WHERE user_id=? AND chat_id=? ORDER BY ts DESC LIMIT ?", (user_id, chat_id, limit))
        return await cur.fetchall()


# События

async def get_active_event():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM events WHERE active=1 ORDER BY started_at DESC LIMIT 1")
        row = await cur.fetchone()
        return dict(row) if row else None


# Колесо удачи

async def spin_wheel(user_id, chat_id):
    prizes = [
        ("+50", 50),
        ("+100", 100),
        ("x2 следующий /sr_daily", "boost2"),
        ("Щит 12ч", "shield12"),
        ("Ничего", 0),
        ("+10", 10),
    ]
    prize = random.choice(prizes)
    if isinstance(prize[1], int):
        delta = prize[1]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT rating FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
            row = await cur.fetchone()
            if not row:
                return None, "no_user"
            new_rating = row[0] + delta
            await db.execute("UPDATE users SET rating=? WHERE user_id=? AND chat_id=?", (new_rating, user_id, chat_id))
            await db.execute("INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
                             (user_id, chat_id, "wheel", delta, now_iso(), f"Prize {prize[0]}"))
            await db.commit()
        return {"description": prize[0], "delta": delta, "new_rating": new_rating}, None
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            if prize[1] == "boost2":
                await db.execute("UPDATE users SET next_daily_multiplier=? WHERE user_id=? AND chat_id=?", (2.0, user_id, chat_id))
                await db.execute("INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
                                 (user_id, chat_id, "wheel", 0, now_iso(), "x2 следующий /sr_daily"))
            elif prize[1] == "shield12":
                until = (datetime.now(KYIV_TZ) + timedelta(hours=12)).isoformat()
                await db.execute("UPDATE users SET shield_until=? WHERE user_id=? AND chat_id=?", (until, user_id, chat_id))
                await db.execute("INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
                                 (user_id, chat_id, "wheel", 0, now_iso(), "Щит 12ч"))
            await db.commit()
        return {"description": prize[0]}, None


# Слот

async def spin_slot(user_id, chat_id):
    symbols = ["🍒", "🍋", "🔔", "⭐", "7️⃣"]
    result = [random.choice(symbols) for _ in range(3)]
    payout = 0
    if result[0] == result[1] == result[2]:
        if result[0] == "7️⃣":
            payout = 500
        else:
            payout = 100
    elif len(set(result)) == 2:
        payout = 20
    if payout > 0:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT rating FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
            row = await cur.fetchone()
            if not row:
                return None, "no_user"
            new_rating = row[0] + payout
            await db.execute("UPDATE users SET rating=? WHERE user_id=? AND chat_id=?", (new_rating, user_id, chat_id))
            await db.execute("INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
                             (user_id, chat_id, "slot_win", payout, now_iso(), f"{''.join(result)}"))
            await db.commit()
        return {"result": result, "payout": payout, "new_rating": new_rating}, None
    return {"result": result, "payout": 0}, None


# Дуэли

async def create_pending_battle(challenger_id, target_id, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        now = now_iso()
        cursor = await db.execute("""
            INSERT INTO battles (chat_id, challenger_id, target_id, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (chat_id, challenger_id, target_id, now))
        await db.commit()
        return cursor.lastrowid


async def get_battle_by_id(battle_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM battles WHERE battle_id=?", (battle_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def resolve_pending_battle(battle_id):
    battle = await get_battle_by_id(battle_id)
    if not battle or battle["status"] != "pending":
        return None, "invalid"
    chat_id = battle["chat_id"]
    challenger_id = battle["challenger_id"]
    target_id = battle["target_id"]

    # Получаем рейтинги
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        c = await (await db.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (challenger_id, chat_id))).fetchone()
        t = await (await db.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (target_id, chat_id))).fetchone()
        if not c or not t:
            return None, "no_user"

        # Проверяем щит цели
        shield_blocked = 0
        now = datetime.now(KYIV_TZ)
        if t["shield_until"]:
            try:
                until = datetime.fromisoformat(t["shield_until"])
                if until > now:
                    shield_blocked = 1
            except:
                pass

        # Простая механика: сильнее по (rating + random[0,20])
        score_c = c["rating"] + random.randint(0, 20)
        score_t = t["rating"] + random.randint(0, 20)
        if score_c >= score_t:
            winner_id = challenger_id
            loser_id = target_id
        else:
            winner_id = target_id
            loser_id = challenger_id

        stolen = 0
        if shield_blocked:
            stolen = 0
        else:
            # забираем 10% рейтинга проигравшего, минимум 1
            loser_rating = c["rating"] if loser_id == challenger_id else t["rating"]
            stolen = max(1, int(loser_rating * 0.1))
            # обновление рейтингов
            if winner_id == challenger_id:
                new_winner = c["rating"] + stolen
                new_loser = t["rating"] - stolen
            else:
                new_winner = t["rating"] + stolen
                new_loser = c["rating"] - stolen
            await db.execute("UPDATE users SET rating=? WHERE user_id=? AND chat_id=?", (new_winner, winner_id, chat_id))
            await db.execute("UPDATE users SET rating=? WHERE user_id=? AND chat_id=?", (new_loser, loser_id, chat_id))

        # фиксируем итог
        await db.execute("""
            UPDATE battles SET status='resolved', winner_id=?, loser_id=?, stolen=?, shield_blocked=?
            WHERE battle_id=?
        """, (winner_id, loser_id, stolen, shield_blocked, battle_id))
        ts = now_iso()
        # история
        if not shield_blocked:
            await db.execute("INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
                             (winner_id, chat_id, "duel_win", stolen, ts, f"beat {loser_id}"))
            await db.execute("INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
                             (loser_id, chat_id, "duel_loss", -stolen, ts, f"lost to {winner_id}"))
        else:
            await db.execute("INSERT INTO history (user_id, chat_id, type, delta, ts, details) VALUES (?,?,?,?,?,?)",
                             (loser_id, chat_id, "duel_shielded", 0, ts, "shield blocked"))
        await db.commit()
        winner = await get_user(winner_id, chat_id)
        loser = await get_user(loser_id, chat_id)
        return {
            "winner_id": winner_id,
            "loser_id": loser_id,
            "stolen": stolen,
            "shield_blocked": bool(shield_blocked),
            "winner_rating": winner["rating"] if winner else None,
            "loser_rating": loser["rating"] if loser else None,
        }, None
