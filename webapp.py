# webapp.py
import os
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from db import (
    init_db,
    get_user,
    get_user_crew,
    register_or_update_user,
    apply_daily_change,
    spin_wheel,
    spin_slot,
    leaderboard,
    transfer_rating,
    create_pending_battle,
    resolve_pending_battle,
    is_admin,
    add_admin,
    list_admins,
)

from datetime import datetime
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


def verify_init_data(init_data: str):
    try:
        data_parts = dict(kv.split("=", 1) for kv in init_data.split("&"))
    except Exception:
        return False, {}
    hash_received = data_parts.pop("hash", "")
    check_list = [f"{k}={data_parts[k]}" for k in sorted(data_parts.keys())]
    check_string = "\n".join(check_list)
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    hmac_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    valid = hmac.compare_digest(hmac_hash, hash_received)
    return valid, data_parts if valid else ({}, {})


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/")
async def index():
    return FileResponse("static/index.html")


def simplify_user(user):
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "rating": user["rating"],
        "title": user["title"],
        "streak": user["daily_streak"],
        "prestige_multiplier": user["prestige_multiplier"],
    }


@app.post("/api/profile")
async def api_profile(payload: dict):
    init_data = payload.get("init_data", "")
    chat_id = payload.get("chat_id")
    valid, data = verify_init_data(init_data)
    if not valid:
        raise HTTPException(400, "Invalid init_data")
    user_id = int(data.get("user_id") or data.get("id", 0))
    if not chat_id:
        raise HTTPException(400, "chat_id missing")
    await register_or_update_user(user_id, int(chat_id), data.get("username", "Anon"))
    user = await get_user(user_id, int(chat_id))
    crew = await get_user_crew(user_id, int(chat_id))
    top = await leaderboard(int(chat_id), limit=5)
    return {
        "ok": True,
        "user": simplify_user(user),
        "crew": crew["name"] if crew else None,
        "top": [{"username": u or str(uid), "rating": r} for u, r, uid in top],
    }


@app.post("/api/daily")
async def api_daily(payload: dict):
    init_data = payload.get("init_data", "")
    chat_id = payload.get("chat_id")
    valid, data = verify_init_data(init_data)
    if not valid:
        raise HTTPException(400, "Invalid init_data")
    user_id = int(data.get("user_id") or data.get("id", 0))
    res, err = await apply_daily_change(user_id, int(chat_id))
    if err:
        return {"ok": False, "error": err}
    user = await get_user(user_id, int(chat_id))
    return {"ok": True, "daily": res, "user": simplify_user(user)}


@app.post("/api/wheel")
async def api_wheel(payload: dict):
    init_data = payload.get("init_data", "")
    chat_id = payload.get("chat_id")
    valid, data = verify_init_data(init_data)
    if not valid:
        raise HTTPException(400, "Invalid init_data")
    user_id = int(data.get("user_id") or data.get("id", 0))
    result, err = await spin_wheel(user_id, int(chat_id))
    if err:
        return {"ok": False, "error": err}
    user = await get_user(user_id, int(chat_id))
    return {"ok": True, "wheel": result, "user": simplify_user(user)}


@app.post("/api/slot")
async def api_slot(payload: dict):
    init_data = payload.get("init_data", "")
    chat_id = payload.get("chat_id")
    valid, data = verify_init_data(init_data)
    if not valid:
        raise HTTPException(400, "Invalid init_data")
    user_id = int(data.get("user_id") or data.get("id", 0))
    result, err = await spin_slot(user_id, int(chat_id))
    if err:
        return {"ok": False, "error": err}
    user = await get_user(user_id, int(chat_id))
    return {"ok": True, "slot": result, "user": simplify_user(user)}


@app.post("/api/duel/request")
async def api_duel_request(payload: dict):
    init_data = payload.get("init_data", "")
    chat_id = payload.get("chat_id")
    target_id = payload.get("target_id")
    valid, data = verify_init_data(init_data)
    if not valid:
        raise HTTPException(400, "Invalid init_data")
    user_id = int(data.get("user_id") or data.get("id", 0))
    battle_id = await create_pending_battle(user_id, target_id, int(chat_id))
    return {"ok": True, "battle_id": battle_id}


@app.post("/api/duel/respond")
async def api_duel_respond(payload: dict):
    init_data = payload.get("init_data", "")
    chat_id = payload.get("chat_id")
    battle_id = payload.get("battle_id")
    action = payload.get("action")  # "accept" or "decline"
    valid, data = verify_init_data(init_data)
    if not valid:
        raise HTTPException(400, "Invalid init_data")
    user_id = int(data.get("user_id") or data.get("id", 0))
    battle = await resolve_pending_battle(battle_id) if action == "accept" else (None, "declined")
    if action == "decline":
        # просто помечаем отклонённым
        # (можно расширить)
        return {"ok": True, "status": "declined"}
    if not battle[0]:
        return {"ok": False, "error": battle[1]}
    return {"ok": True, "result": battle[0]}
