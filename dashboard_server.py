# -*- coding: utf-8 -*-
"""
ZEXO LEVEL UP - FreeFire Bot Web Dashboard Backend (Full)
Config control + Auto Stop + Ban Check integrated.
"""

import asyncio
import hashlib
import io
import json
import os
import secrets
import time
import uuid
import zipfile
from typing import Dict, List, Any, Optional
from aiohttp import web


# ==================== CONFIG ====================
ADMIN_PASSWORD_DEFAULT = "zainulhasan"
ADMIN_TOKEN_TTL = 7 * 24 * 3600
USER_TOKEN_TTL = 30 * 24 * 3600
FREE_PLAN_HOURS = 12
FREE_PLAN_SLOTS = 3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "index.html")

USERS_FILE = os.path.join(BASE_DIR, "users.json")
ORDERS_FILE = os.path.join(BASE_DIR, "orders.json")
FREE_PLAN_FILE = os.path.join(BASE_DIR, "free_plan_requests.json")
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
DEVICES_FILE = os.path.join(BASE_DIR, "devices.json")
TOKEN_CACHE_FILE = os.path.join(BASE_DIR, "token_cache.json")
PLANS_FILE = os.path.join(BASE_DIR, "plans.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


DEFAULT_PLANS = [
    {"id": "basic-7",  "name": "Basic",  "duration_text": "7 Days",  "duration_hours": 168,  "price_inr": 99,   "price_usd": 1.29,  "slots": 5,   "badge": None,         "popular": False},
    {"id": "pro-30",   "name": "Pro",    "duration_text": "30 Days", "duration_hours": 720,  "price_inr": 299,  "price_usd": 3.99,  "slots": 15,  "badge": "POPULAR",    "popular": True},
    {"id": "ultra-30", "name": "Ultra",  "duration_text": "30 Days", "duration_hours": 720,  "price_inr": 599,  "price_usd": 7.99,  "slots": 40,  "badge": "BEST VALUE", "popular": False},
    {"id": "mega-90",  "name": "Mega",   "duration_text": "90 Days", "duration_hours": 2160, "price_inr": 1499, "price_usd": 19.99, "slots": 100, "badge": None,         "popular": False},
]

DEFAULT_BOT_CONFIG = {
    "max_level": 31,
    "auto_remove": True,
    "stop_mode": "level",
    "matches_per_account": 50,
    "default_mode": "auto",
    "ban_check_enabled": True,
}

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", ADMIN_PASSWORD_DEFAULT)


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[STORAGE] save failed {path}: {e}")


def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


# ==================== BOT CONFIG ====================
def load_bot_config() -> Dict:
    cfg = _load_json(CONFIG_FILE, None)
    if not isinstance(cfg, dict):
        _save_json(CONFIG_FILE, DEFAULT_BOT_CONFIG)
        return dict(DEFAULT_BOT_CONFIG)
    merged = dict(DEFAULT_BOT_CONFIG)
    merged.update(cfg)
    return merged


def save_bot_config(cfg: Dict):
    _save_json(CONFIG_FILE, cfg)
    try:
        bot_state.bot_config = cfg
    except Exception:
        pass


# ==================== STOP REGISTRY ====================
class _StopRegistry:
    def __init__(self):
        self._stopped: Dict[str, str] = {}
    def mark(self, uid: str, reason: str):
        self._stopped[str(uid)] = reason
    def reason(self, uid: str) -> Optional[str]:
        return self._stopped.get(str(uid))
    def clear(self, uid: str):
        self._stopped.pop(str(uid), None)
    def all(self) -> Dict[str, str]:
        return dict(self._stopped)


# ==================== BOT STATE ====================
class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}
        self._paused: Dict[str, bool] = {}
        self.paused_accounts = set()
        self.stop_registry = _StopRegistry()
        self.bot_config = load_bot_config()

    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {"time": time.strftime("%H:%M:%S"), "level": level, "message": message, "uid": uid}
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def register_account(self, uid: str, nickname: str, region: str, level: int,
                         exp: int, likes: int = 0, mode: str = "auto",
                         owner_user_id: str = "", owner_username: str = ""):
        uid_str = str(uid)
        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": level or 1,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "likes": likes or 0,
                "status": "ONLINE",
                "mode": mode or "auto",
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "last_updated": time.strftime("%H:%M:%S"),
                "owner_user_id": owner_user_id,
                "owner_username": owner_username,
            }
        else:
            acc = self.accounts[uid_str]
            if nickname: acc["nickname"] = nickname
            if region: acc["region"] = region
            if level: acc["level"] = level
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["likes"] = likes
            acc["status"] = "ONLINE"
            if mode: acc["mode"] = mode
            if owner_user_id: acc["owner_user_id"] = owner_user_id
            if owner_username: acc["owner_username"] = owner_username
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            old_exp = acc["current_exp"]
            acc["current_exp"] = current_exp
            if level is not None and level > 0:
                acc["level"] = level
            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            acc["last_updated"] = time.strftime("%H:%M:%S")
            diff = current_exp - old_exp
            if diff > 0:
                self.log(f"{acc['nickname']} gained +{diff} EXP! Total: +{acc['gained_exp']}", "success", uid_str)
            self.recalc_totals()

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = status
            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def increment_match(self, uid: str):
        uid_str = str(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())

    def is_paused(self, uid: str) -> bool:
        return bool(self._paused.get(str(uid), False))

    def set_paused(self, uid: str, paused: bool):
        self._paused[str(uid)] = bool(paused)


bot_state = BotState()


# ==================== USERS ====================
class UserStore:
    def __init__(self):
        self.users: Dict[str, Dict] = _load_json(USERS_FILE, {})
        self.tokens: Dict[str, Dict] = _load_json(USERS_FILE + ".tokens", {})
        self._cleanup_tokens()

    def _persist(self):
        _save_json(USERS_FILE, self.users)
        _save_json(USERS_FILE + ".tokens", self.tokens)

    def _cleanup_tokens(self):
        now = time.time()
        dead = [t for t, v in self.tokens.items() if v.get("expires", 0) < now]
        for t in dead:
            del self.tokens[t]
        if dead:
            self._persist()

    def find(self, username: str) -> Optional[Dict]:
        return self.users.get(username.lower())

    def create(self, username: str, password: str, display_name: str) -> Optional[Dict]:
        u = username.strip().lower()
        if not u or len(u) < 3:
            return None
        if u in self.users:
            return None
        user = {
            "id": str(uuid.uuid4()),
            "username": u,
            "display_name": display_name.strip() or u,
            "password_hash": _hash_pw(password),
            "role": "user",
            "plan": "Free",
            "slots": FREE_PLAN_SLOTS,
            "plan_expires_at": 0,
            "free_plan_claimed": False,
            "created_at": int(time.time()),
            "last_login": 0,
        }
        self.users[u] = user
        self._persist()
        return user

    def verify(self, username: str, password: str) -> Optional[Dict]:
        u = self.find(username)
        if not u:
            return None
        if u.get("password_hash") != _hash_pw(password):
            return None
        return u

    def issue_token(self, username: str) -> str:
        tok = secrets.token_urlsafe(32)
        self.tokens[tok] = {"username": username.lower(), "expires": int(time.time()) + USER_TOKEN_TTL}
        self._persist()
        return tok

    def revoke_token(self, token: str):
        if token in self.tokens:
            del self.tokens[token]
            self._persist()

    def user_from_token(self, token: str) -> Optional[Dict]:
        if not token:
            return None
        entry = self.tokens.get(token)
        if not entry:
            return None
        if entry.get("expires", 0) < time.time():
            del self.tokens[token]
            self._persist()
            return None
        return self.find(entry.get("username", ""))

    def update(self, username: str, **fields):
        u = self.find(username)
        if not u:
            return
        for k, v in fields.items():
            u[k] = v
        self._persist()

    def delete(self, username: str):
        u = self.find(username)
        if not u:
            return
        dead = [t for t, v in self.tokens.items() if v.get("username") == u["username"]]
        for t in dead:
            del self.tokens[t]
        del self.users[u["username"]]
        self._persist()

    def all_users(self) -> List[Dict]:
        return list(self.users.values())


user_store = UserStore()


# ==================== PLANS ====================
def load_plans() -> List[Dict]:
    plans = _load_json(PLANS_FILE, None)
    if not plans or not isinstance(plans, list):
        _save_json(PLANS_FILE, DEFAULT_PLANS)
        return DEFAULT_PLANS
    return plans


# ==================== ADMIN SESSIONS ====================
_admin_sessions: Dict[str, int] = {}


def _admin_new_token() -> str:
    tok = secrets.token_urlsafe(32)
    _admin_sessions[tok] = int(time.time()) + ADMIN_TOKEN_TTL
    return tok


def _admin_valid(token: str) -> bool:
    if not token:
        return False
    exp = _admin_sessions.get(token)
    if not exp:
        return False
    if exp < time.time():
        del _admin_sessions[token]
        return False
    return True


# ==================== HELPERS ====================
def _get_user(request: web.Request) -> Optional[Dict]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return user_store.user_from_token(auth[7:].strip())


def _get_admin(request: web.Request) -> bool:
    return _admin_valid(request.headers.get("X-Admin-Token", ""))


def _json(data, status: int = 200):
    return web.json_response(data, status=status)


def _public_user(u: Dict) -> Dict:
    return {
        "id": u["id"],
        "username": u["username"],
        "display_name": u.get("display_name", u["username"]),
        "role": u.get("role", "user"),
        "plan": u.get("plan", "Free"),
        "slots": u.get("slots", FREE_PLAN_SLOTS),
        "plan_expires_at": u.get("plan_expires_at", 0),
        "free_plan_claimed": u.get("free_plan_claimed", False),
    }


# ==================== USER HANDLERS ====================
async def handle_user_register(request: web.Request):
    try:
        data = await request.json()
        username = str(data.get("username", "")).strip().lower()
        password = str(data.get("password", ""))
        display_name = str(data.get("display_name", "")).strip()
        if len(username) < 3:
            return _json({"status": "error", "error": "Username must be at least 3 chars"})
        if len(password) < 4:
            return _json({"status": "error", "error": "Password must be at least 4 chars"})
        if user_store.find(username):
            return _json({"status": "error", "error": "Username already taken"})
        user = user_store.create(username, password, display_name)
        if not user:
            return _json({"status": "error", "error": "Could not create user"})
        token = user_store.issue_token(username)
        bot_state.log(f"New user registered: {username}", "success")
        return _json({"status": "ok", "token": token, "user": _public_user(user)})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_user_login(request: web.Request):
    try:
        data = await request.json()
        username = str(data.get("username", "")).strip().lower()
        password = str(data.get("password", ""))
        user = user_store.verify(username, password)
        if not user:
            return _json({"status": "error", "error": "Invalid username or password"})
        user_store.update(username, last_login=int(time.time()))
        token = user_store.issue_token(username)
        return _json({"status": "ok", "token": token, "user": _public_user(user)})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_user_logout(request: web.Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        user_store.revoke_token(auth[7:].strip())
    return _json({"status": "ok"})


async def handle_user_me(request: web.Request):
    user = _get_user(request)
    if not user:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    return _json({"status": "ok", "user": _public_user(user)})


# ==================== STATS ====================
async def handle_get_stats(request: web.Request):
    user = _get_user(request)
    is_admin = _get_admin(request)
    if not user and not is_admin:
        return _json({"status": "error", "error": "Not authenticated"}, 401)

    if is_admin:
        visible_accounts = list(bot_state.accounts.values())
    else:
        visible_accounts = [a for a in bot_state.accounts.values() if a.get("owner_user_id") == user["id"]]

    accounts_data = []
    for acc in visible_accounts:
        item = dict(acc)
        item["is_paused"] = bot_state.is_paused(item.get("uid", ""))
        try:
            item["stop_reason"] = bot_state.stop_registry.reason(item.get("uid", ""))
        except Exception:
            item["stop_reason"] = None
        accounts_data.append(item)
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)

    plan_name = "Admin" if is_admin else (user.get("plan", "Free") if user else "Free")
    user_slots = 9999 if is_admin else (user.get("slots", FREE_PLAN_SLOTS) if user else 0)
    used_slots = len(accounts_data)
    available_slots = max(0, user_slots - used_slots)

    plan_expired = False
    remaining_seconds = 0
    if not is_admin and user:
        exp_ts = user.get("plan_expires_at", 0)
        if exp_ts > 0:
            remaining_seconds = max(0, exp_ts - int(time.time()))
            plan_expired = remaining_seconds <= 0
        else:
            plan_expired = plan_name == "Free"

    total_matches = sum(a.get("matches_played", 0) for a in accounts_data)
    total_exp = sum(a.get("gained_exp", 0) for a in accounts_data)

    if is_admin:
        logs = bot_state.logs[-60:]
    else:
        owned_uids = {str(a.get("uid")) for a in accounts_data}
        logs = [l for l in bot_state.logs if not l.get("uid") or str(l.get("uid")) in owned_uids][-60:]

    return _json({
        "status": "ok",
        "total_accounts": len(accounts_data),
        "total_matches": total_matches,
        "total_gained_exp": total_exp,
        "accounts": accounts_data,
        "logs": logs,
        "uptime": int(time.time() - bot_state.start_time),
        "plan_name": plan_name,
        "user_slots": user_slots,
        "available_slots": available_slots,
        "remaining_seconds": remaining_seconds,
        "plan_expired": plan_expired,
    })


# ==================== PLANS HANDLERS ====================
async def handle_plans_list(request: web.Request):
    return _json({"status": "ok", "plans": load_plans()})


async def handle_plan_order(request: web.Request):
    user = _get_user(request)
    if not user:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        data = await request.json()
        plan_id = str(data.get("plan_id", "")).strip()
        txn = str(data.get("transaction_id", "")).strip()
        tg = str(data.get("user_telegram", "")).strip()
        method = str(data.get("payment_method", "UPI")).strip()
        if not plan_id or not txn:
            return _json({"status": "error", "error": "plan_id and transaction_id required"})
        plan = next((p for p in load_plans() if p["id"] == plan_id), None)
        if not plan:
            return _json({"status": "error", "error": "Unknown plan"})
        orders = _load_json(ORDERS_FILE, [])
        order = {
            "id": "ORD-" + secrets.token_hex(4).upper(),
            "user_id": user["id"],
            "username": user["username"],
            "plan_id": plan["id"],
            "plan_name": plan["name"],
            "price_inr": plan["price_inr"],
            "price_usd": plan["price_usd"],
            "slots": plan["slots"],
            "duration_hours": plan["duration_hours"],
            "transaction_id": txn,
            "user_telegram": tg,
            "payment_method": method,
            "status": "pending",
            "created_at": int(time.time()),
            "approved_at": None,
        }
        orders.append(order)
        _save_json(ORDERS_FILE, orders)
        bot_state.log(f"Order {order['id']} from {user['username']} ({plan['name']})", "info")
        return _json({"status": "ok", "order_id": order["id"]})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_my_orders(request: web.Request):
    user = _get_user(request)
    if not user:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    orders = _load_json(ORDERS_FILE, [])
    mine = [o for o in orders if o.get("user_id") == user["id"]]
    mine.sort(key=lambda o: o.get("created_at", 0), reverse=True)
    return _json({"status": "ok", "orders": mine})


# ==================== FREE PLAN HANDLERS ====================
async def handle_free_plan_status(request: web.Request):
    user = _get_user(request)
    if not user:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    reqs = _load_json(FREE_PLAN_FILE, [])
    mine = [r for r in reqs if r.get("user_id") == user["id"]]
    pending = next((r for r in mine if r.get("status") == "pending"), None)
    approved = next((r for r in mine if r.get("status") == "approved"), None)

    is_active = False
    remaining_seconds = 0
    if approved:
        started = approved.get("approved_at", 0)
        expires = started + (FREE_PLAN_HOURS * 3600)
        remaining_seconds = max(0, expires - int(time.time()))
        is_active = remaining_seconds > 0

    return _json({
        "status": "ok",
        "is_active": is_active,
        "remaining_seconds": remaining_seconds,
        "slots": FREE_PLAN_SLOTS,
        "has_pending": bool(pending),
        "already_used": bool(user.get("free_plan_claimed")),
    })


async def handle_free_plan_request(request: web.Request):
    user = _get_user(request)
    if not user:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        if user.get("free_plan_claimed"):
            return _json({"status": "error", "error": "Free plan already used"})
        data = await request.json()
        tg = str(data.get("telegram", "")).strip()
        reason = str(data.get("reason", "")).strip()
        if not tg:
            return _json({"status": "error", "error": "Telegram required"})
        reqs = _load_json(FREE_PLAN_FILE, [])
        if any(r.get("user_id") == user["id"] and r.get("status") == "pending" for r in reqs):
            return _json({"status": "error", "error": "You already have a pending request"})
        req = {
            "id": "FP-" + secrets.token_hex(4).upper(),
            "user_id": user["id"],
            "username": user["username"],
            "display_name": user.get("display_name", user["username"]),
            "telegram": tg,
            "reason": reason,
            "status": "pending",
            "created_at": int(time.time()),
            "approved_at": None,
        }
        reqs.append(req)
        _save_json(FREE_PLAN_FILE, reqs)
        bot_state.log(f"Free plan request from {user['username']}", "info")
        return _json({"status": "ok", "message": "Request sent to admin"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


# ==================== ACCOUNT HANDLERS ====================
def _read_accounts() -> List[Dict]:
    return _load_json(ACCOUNTS_FILE, [])


def _write_accounts(accounts: List[Dict]):
    _save_json(ACCOUNTS_FILE, accounts)


async def handle_account_add(request: web.Request):
    user = _get_user(request)
    is_admin = _get_admin(request)
    if not user and not is_admin:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        data = await request.json()
        mode = str(data.get("mode", "auto")).strip().lower()
        if mode not in ("auto", "br", "lw"):
            mode = "auto"

        if not is_admin:
            visible = [a for a in bot_state.accounts.values() if a.get("owner_user_id") == user["id"]]
            if len(visible) >= user.get("slots", FREE_PLAN_SLOTS):
                return _json({"status": "error", "error": "No slots left. Buy a plan."})

        owner_id = user["id"] if user else "admin"
        owner_name = user["username"] if user else "admin"

        accounts = _read_accounts()
        if "uid" in data and "password" in data:
            uid = str(data["uid"]).strip()
            pwd = str(data["password"]).strip()
            if not uid or not pwd:
                return _json({"status": "error", "error": "UID and Password required"})
            accounts = [a for a in accounts if str(a.get("uid")) != uid]
            accounts.append({"uid": uid, "password": pwd, "mode": mode,
                             "owner_user_id": owner_id, "owner_username": owner_name})
        elif "token" in data:
            tok = str(data["token"]).strip()
            if not tok:
                return _json({"status": "error", "error": "Token required"})
            accounts = [a for a in accounts if a.get("token") != tok]
            accounts.append({"token": tok, "mode": mode,
                             "owner_user_id": owner_id, "owner_username": owner_name})
        else:
            return _json({"status": "error", "error": "Invalid payload"})
        _write_accounts(accounts)
        bot_state.log(f"Node injected by {owner_name} (mode: {mode})", "success")

        if "on_account_added" in bot_state.refresh_callbacks:
            payload = dict(data)
            payload["owner_user_id"] = owner_id
            payload["owner_username"] = owner_name
            asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](payload))
        return _json({"status": "ok"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_account_bulk_add(request: web.Request):
    user = _get_user(request)
    is_admin = _get_admin(request)
    if not user and not is_admin:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        data = await request.json()
        incoming = data.get("accounts", [])
        if not isinstance(incoming, list) or not incoming:
            return _json({"status": "error", "error": "accounts array required"})

        owner_id = user["id"] if user else "admin"
        owner_name = user["username"] if user else "admin"

        accounts = _read_accounts()
        added = 0
        failed = 0

        for item in incoming:
            uid = str(item.get("uid", "")).strip()
            pwd = str(item.get("password", "")).strip()
            mode = str(item.get("mode", "auto")).strip().lower()
            if mode not in ("auto", "br", "lw"):
                mode = "auto"
            if not uid or not pwd:
                failed += 1
                continue
            accounts = [a for a in accounts if str(a.get("uid")) != uid]
            accounts.append({"uid": uid, "password": pwd, "mode": mode,
                             "owner_user_id": owner_id, "owner_username": owner_name})
            added += 1
        _write_accounts(accounts)
        bot_state.log(f"Bulk add: {added} nodes by {owner_name}", "success")

        if "on_account_added" in bot_state.refresh_callbacks:
            for item in incoming:
                if item.get("uid") and item.get("password"):
                    payload = {"uid": item["uid"], "password": item["password"],
                               "mode": item.get("mode", "auto"),
                               "owner_user_id": owner_id, "owner_username": owner_name}
                    asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](payload))
        return _json({"status": "ok", "added": added, "failed": failed})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_account_delete(request: web.Request):
    user = _get_user(request)
    is_admin = _get_admin(request)
    if not user and not is_admin:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        token = str(data.get("token", "")).strip()
        if not uid and not token:
            return _json({"status": "error", "error": "uid or token required"})

        accounts = _read_accounts()

        def owned(a):
            if is_admin:
                return True
            return a.get("owner_user_id") == user["id"]

        def matches(a):
            if uid and str(a.get("uid", "")) == uid and owned(a):
                return True
            if token and a.get("token") == token and owned(a):
                return True
            return False

        if uid and uid not in bot_state.accounts and not token:
            for a in accounts:
                if str(a.get("uid", "")) == uid:
                    token = a.get("token", "")
                    break

        accounts = [a for a in accounts if not matches(a)]
        _write_accounts(accounts)

        if uid and uid in bot_state.accounts:
            if is_admin or bot_state.accounts[uid].get("owner_user_id") == user["id"]:
                del bot_state.accounts[uid]
        if uid and uid in bot_state.account_workers:
            bot_state.account_workers[uid].cancel()
            del bot_state.account_workers[uid]

        try:
            bot_state.stop_registry.clear(uid)
        except Exception:
            pass

        bot_state.log(f"Node {uid or token[:10]} removed", "warning", uid)
        return _json({"status": "ok"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_account_refresh(request: web.Request):
    user = _get_user(request)
    is_admin = _get_admin(request)
    if not user and not is_admin:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return _json({"status": "error", "error": "uid required"})
        if not is_admin:
            acc = bot_state.accounts.get(uid)
            if acc and acc.get("owner_user_id") != user["id"]:
                return _json({"status": "error", "error": "Not your account"}, 403)
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return _json({"status": "ok"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_account_restart(request: web.Request):
    user = _get_user(request)
    is_admin = _get_admin(request)
    if not user and not is_admin:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return _json({"status": "error", "error": "uid required"})
        if not is_admin:
            acc = bot_state.accounts.get(uid)
            if acc and acc.get("owner_user_id") != user["id"]:
                return _json({"status": "error", "error": "Not your account"}, 403)
        try:
            bot_state.stop_registry.clear(uid)
        except Exception:
            pass
        bot_state.set_paused(uid, False)
        if uid in bot_state.account_workers:
            bot_state.account_workers[uid].cancel()
        bot_state.log(f"Node {uid} restart requested", "warning", uid)
        return _json({"status": "ok"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_account_pause(request: web.Request):
    user = _get_user(request)
    is_admin = _get_admin(request)
    if not user and not is_admin:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return _json({"status": "error", "error": "uid required"})
        if not is_admin:
            acc = bot_state.accounts.get(uid)
            if acc and acc.get("owner_user_id") != user["id"]:
                return _json({"status": "error", "error": "Not your account"}, 403)
        new_state = not bot_state.is_paused(uid)
        bot_state.set_paused(uid, new_state)
        bot_state.update_status(uid, "PAUSED" if new_state else "ONLINE", 0)
        bot_state.log(f"Node {uid} {'PAUSED' if new_state else 'RESUMED'}", "warning", uid)
        return _json({"status": "ok", "paused": new_state, "message": "Paused" if new_state else "Resumed"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


# ==================== BAN CHECK HANDLER (FIXED) ====================
def _save_ban_result(uid: str, result: dict):
    """Ban check result ko bot_state mein save karta hai."""
    try:
        if uid in bot_state.accounts:
            is_banned = bool(result.get("banned", False)) if isinstance(result, dict) else False
            bot_state.accounts[uid]["banned"] = is_banned
            bot_state.accounts[uid]["ban_info"] = result
            if is_banned:
                bot_state.accounts[uid]["status"] = "BANNED"
            bot_state.accounts[uid]["ban_checked_at"] = time.time()
    except Exception:
        pass


async def handle_account_ban_check(request: web.Request):
    """
    Dashboard se ban check request handle karta hai.
    UID ya Token se account credentials nikalta hai aur main.py ke wired
    ban check function ko call karta hai.
    """
    user = _get_user(request)
    is_admin = _get_admin(request)
    if not user and not is_admin:
        return _json({"status": "error", "error": "Not authenticated"}, 401)
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return _json({"status": "error", "error": "uid required"})
        if not is_admin:
            acc = bot_state.accounts.get(uid)
            if acc and acc.get("owner_user_id") != user["id"]:
                return _json({"status": "error", "error": "Not your account"}, 403)

        cb_uid_pass = bot_state.refresh_callbacks.get("check_ban_uid_pass")
        cb_access = bot_state.refresh_callbacks.get("check_ban_access_token")

        if not cb_uid_pass and not cb_access:
            return _json({"status": "error", "error": "Ban check not wired"})

        accounts = _read_accounts()
        account_entry = None

        for a in accounts:
            if str(a.get("uid", "")) == uid:
                account_entry = a
                break

        if not account_entry:
            live_acc = bot_state.accounts.get(uid, {})
            live_token = live_acc.get("token") or live_acc.get("auth_token") or ""
            if live_token:
                for a in accounts:
                    if a.get("token") == live_token:
                        account_entry = a
                        break

        if not account_entry:
            creds = bot_state.account_credentials.get(uid)
            if creds:
                pwd = creds.get("auth_password") or creds.get("password") or ""
                token = creds.get("auth_token") or creds.get("token") or ""
                if pwd and cb_uid_pass:
                    result = await cb_uid_pass(uid, pwd)
                    _save_ban_result(uid, result)
                    return _json({"status": "ok", "result": result})
                elif token and cb_access:
                    result = await cb_access(token)
                    _save_ban_result(uid, result)
                    return _json({"status": "ok", "result": result})

        if not account_entry:
            return _json({
                "status": "error",
                "error": f"Account {uid} not found in accounts.json or bot state. "
                         f"Add it with UID+Password to enable ban check."
            })

        pwd = account_entry.get("password", "")
        token = account_entry.get("token", "")

        if pwd and cb_uid_pass:
            result = await cb_uid_pass(uid, pwd)
        elif token and cb_access:
            result = await cb_access(token)
        else:
            return _json({"status": "error", "error": "No credentials (password/token) available for this account"})

        _save_ban_result(uid, result)

        return _json({"status": "ok", "result": result})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return _json({"status": "error", "error": str(e)})


# ==================== CONFIG HANDLERS ====================
async def handle_config_get(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    return _json({"status": "ok", "config": load_bot_config()})


async def handle_config_set(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    try:
        data = await request.json()
        cfg = load_bot_config()
        for k in ("max_level", "auto_remove", "stop_mode", "matches_per_account",
                  "default_mode", "ban_check_enabled"):
            if k in data:
                cfg[k] = data[k]
        try:
            cfg["max_level"] = max(1, min(100, int(cfg["max_level"])))
        except Exception:
            cfg["max_level"] = 31
        try:
            cfg["matches_per_account"] = max(1, int(cfg["matches_per_account"]))
        except Exception:
            cfg["matches_per_account"] = 50
        if cfg["stop_mode"] not in ("infinite", "level", "matches", "both"):
            cfg["stop_mode"] = "level"
        if cfg["default_mode"] not in ("auto", "br", "lw"):
            cfg["default_mode"] = "auto"
        cfg["auto_remove"] = bool(cfg["auto_remove"])
        cfg["ban_check_enabled"] = bool(cfg["ban_check_enabled"])
        save_bot_config(cfg)
        bot_state.log(f"Config updated: max_level={cfg['max_level']} stop_mode={cfg['stop_mode']}", "warning")
        return _json({"status": "ok", "config": cfg})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_stopped_list(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    return _json({"status": "ok", "stopped": bot_state.stop_registry.all()})


async def handle_account_resume(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return _json({"status": "error", "error": "uid required"})
        bot_state.stop_registry.clear(uid)
        bot_state.set_paused(uid, False)
        bot_state.log(f"UID {uid} manual resume", "warning", uid)
        return _json({"status": "ok"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


# ==================== ADMIN HANDLERS ====================
async def handle_admin_login(request: web.Request):
    try:
        data = await request.json()
        pw = str(data.get("password", "")).strip()
        if pw != ADMIN_PASSWORD:
            return _json({"status": "error", "error": "Wrong password"})
        tok = _admin_new_token()
        bot_state.log("Admin logged in", "warning")
        return _json({"status": "ok", "token": tok, "expires_in": ADMIN_TOKEN_TTL})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_admin_orders(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    orders = _load_json(ORDERS_FILE, [])
    orders.sort(key=lambda o: o.get("created_at", 0), reverse=True)
    return _json({"status": "ok", "orders": orders})


async def handle_admin_orders_action(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    try:
        data = await request.json()
        order_id = str(data.get("order_id", "")).strip()
        action = str(data.get("action", "")).strip().lower()
        if action not in ("approve", "reject"):
            return _json({"status": "error", "error": "action must be approve/reject"})
        orders = _load_json(ORDERS_FILE, [])
        order = next((o for o in orders if o["id"] == order_id), None)
        if not order:
            return _json({"status": "error", "error": "Order not found"})
        if order.get("status") != "pending":
            return _json({"status": "error", "error": "Order already " + order.get("status", "?")})

        order["status"] = "approved" if action == "approve" else "rejected"
        order["approved_at"] = int(time.time())
        _save_json(ORDERS_FILE, orders)

        if action == "approve":
            user = user_store.find(order["username"])
            if user:
                now = int(time.time())
                cur = user.get("plan_expires_at", 0)
                base = cur if cur > now else now
                user_store.update(order["username"],
                                  slots=order["slots"],
                                  plan=order["plan_name"],
                                  plan_expires_at=base + order["duration_hours"] * 3600)
                bot_state.log(f"Order {order_id} approved -> {order['username']} gets {order['slots']} slots", "success")
        else:
            bot_state.log(f"Order {order_id} rejected", "warning")
        return _json({"status": "ok", "message": f"Order {action}d"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_admin_overview(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    return _json({
        "status": "ok",
        "overview": {
            "total_users": len(user_store.all_users()),
            "total_active_accounts": len(bot_state.accounts),
            "total_gained_exp": bot_state.total_gained_exp,
            "total_matches": bot_state.total_matches,
            "uptime": int(time.time() - bot_state.start_time),
        }
    })


async def handle_admin_users(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    users = user_store.all_users()
    enriched = []
    for u in users:
        owned = [a for a in bot_state.accounts.values() if a.get("owner_user_id") == u["id"]]
        enriched.append({
            "id": u["id"],
            "username": u["username"],
            "display_name": u.get("display_name", u["username"]),
            "role": u.get("role", "user"),
            "plan": u.get("plan", "Free"),
            "plan_expires_at": u.get("plan_expires_at", 0),
            "slots": u.get("slots", FREE_PLAN_SLOTS),
            "slots_used": len(owned),
            "free_plan_claimed": u.get("free_plan_claimed", False),
            "total_gained_exp": sum(a.get("gained_exp", 0) for a in owned),
            "created_at": u.get("created_at", 0),
        })
    return _json({"status": "ok", "users": enriched})


async def handle_admin_users_update(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    try:
        data = await request.json()
        user_id = str(data.get("user_id", "")).strip()
        slots = data.get("slots")
        add_hours = data.get("add_hours", 0)

        user = next((u for u in user_store.all_users() if u["id"] == user_id), None)
        if not user:
            return _json({"status": "error", "error": "User not found"})

        fields = {}
        if slots is not None:
            try:
                fields["slots"] = int(slots)
            except Exception:
                pass
        if add_hours:
            now = int(time.time())
            cur = user.get("plan_expires_at", 0)
            base = cur if cur > now else now
            fields["plan_expires_at"] = base + int(add_hours) * 3600
        if fields:
            user_store.update(user["username"], **fields)
        return _json({"status": "ok"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_admin_users_delete(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    try:
        data = await request.json()
        user_id = str(data.get("user_id", "")).strip()
        user = next((u for u in user_store.all_users() if u["id"] == user_id), None)
        if not user:
            return _json({"status": "error", "error": "User not found"})
        accounts = _read_accounts()
        before = len(accounts)
        accounts = [a for a in accounts if a.get("owner_user_id") != user_id]
        removed = before - len(accounts)
        _write_accounts(accounts)
        for uid in list(bot_state.accounts.keys()):
            if bot_state.accounts[uid].get("owner_user_id") == user_id:
                del bot_state.accounts[uid]
                if uid in bot_state.account_workers:
                    bot_state.account_workers[uid].cancel()
        user_store.delete(user["username"])
        bot_state.log(f"User {user['username']} deleted ({removed} accounts removed)", "warning")
        return _json({"status": "ok"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_admin_credentials(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    accounts = _read_accounts()
    out = []
    for a in accounts:
        uid = str(a.get("uid", "")).strip()
        token = a.get("token", "")
        password = a.get("password", "")
        owner_username = a.get("owner_username", "")
        live = bot_state.accounts.get(uid, {})
        out.append({
            "uid": uid or (live.get("uid", "") if live else ""),
            "nickname": live.get("nickname", ""),
            "password": password,
            "token": token,
            "has_token": bool(token),
            "owner_username": owner_username,
            "status": live.get("status", "OFFLINE"),
            "level": live.get("level", 0),
            "gained_exp": live.get("gained_exp", 0),
        })
    return _json({"status": "ok", "credentials": out})


async def handle_admin_accounts(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    accounts = []
    for a in bot_state.accounts.values():
        accounts.append({
            "uid": a.get("uid"),
            "nickname": a.get("nickname"),
            "region": a.get("region"),
            "level": a.get("level"),
            "gained_exp": a.get("gained_exp", 0),
            "owner_username": a.get("owner_username", ""),
            "status": a.get("status", "OFFLINE"),
        })
    accounts.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    return _json({"status": "ok", "accounts": accounts})


async def handle_admin_free_plan_requests(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    reqs = _load_json(FREE_PLAN_FILE, [])
    reqs.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return _json({"status": "ok", "requests": reqs})


async def handle_admin_free_plan_action(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    try:
        data = await request.json()
        req_id = str(data.get("request_id", "")).strip()
        action = str(data.get("action", "")).strip().lower()
        if action not in ("approve", "reject"):
            return _json({"status": "error", "error": "action must be approve/reject"})
        reqs = _load_json(FREE_PLAN_FILE, [])
        req = next((r for r in reqs if r["id"] == req_id), None)
        if not req:
            return _json({"status": "error", "error": "Request not found"})
        if req.get("status") != "pending":
            return _json({"status": "error", "error": "Already " + req.get("status", "?")})

        req["status"] = "approved" if action == "approve" else "rejected"
        req["approved_at"] = int(time.time())
        _save_json(FREE_PLAN_FILE, reqs)

        if action == "approve":
            user = user_store.find(req["username"])
            if user:
                now = int(time.time())
                cur = user.get("plan_expires_at", 0)
                base = cur if cur > now else now
                user_store.update(req["username"],
                                  slots=FREE_PLAN_SLOTS,
                                  plan="Free Trial",
                                  plan_expires_at=base + FREE_PLAN_HOURS * 3600,
                                  free_plan_claimed=True)
                bot_state.log(f"Free plan approved for {req['username']}", "success")
        else:
            bot_state.log(f"Free plan rejected for {req['username']}", "warning")
        return _json({"status": "ok", "message": f"Free plan {action}d"})
    except Exception as e:
        return _json({"status": "error", "error": str(e)})


async def handle_admin_download_credentials(request: web.Request):
    if not _get_admin(request):
        return web.Response(text="admin only", status=403)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in (ACCOUNTS_FILE, DEVICES_FILE, TOKEN_CACHE_FILE):
            if os.path.exists(path):
                z.write(path, arcname=os.path.basename(path))
    buf.seek(0)
    return web.Response(
        body=buf.read(),
        content_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=zexo_credentials.zip"},
    )


# ==================== LEVEL LIMIT (patch_limit) ====================
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
_LIMIT_EVERY = 10
_limit_done = set()
_bg_tasks: list = []


def _limit_state() -> Dict[str, Any]:
    c = _load_json(CONFIG_PATH, {})
    if not isinstance(c, dict):
        c = {}
    enabled = bool(c["limit_enabled"]) if "limit_enabled" in c else bool(c.get("auto_remove", True))
    action = c.get("limit_action") or "remove"
    if action not in ("pause", "remove"):
        action = "remove"
    try:
        lvl = int(c.get("max_level", 31))
    except Exception:
        lvl = 31
    return {"enabled": enabled, "max_level": max(1, min(100, lvl)), "action": action}


async def handle_admin_limit_get(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    return _json({"status": "ok", **_limit_state()})


async def handle_admin_limit_set(request: web.Request):
    if not _get_admin(request):
        return _json({"status": "error", "error": "Admin only"}, 403)
    try:
        data = await request.json()
        lvl = int(data.get("max_level"))
        if not 1 <= lvl <= 100:
            return _json({"status": "error", "error": "Level must be between 1 and 100"})
        enabled = bool(data.get("enabled"))
        action = str(data.get("action", "pause"))
        if action not in ("pause", "remove"):
            action = "pause"
        c = _load_json(CONFIG_PATH, {})
        if not isinstance(c, dict):
            c = {}
        c.update({"max_level": lvl, "limit_enabled": enabled, "limit_action": action,
                  "auto_remove": bool(enabled and action == "remove")})
        _save_json(CONFIG_PATH, c)
        _limit_done.clear()
        bot_state.log(f"Level limit {'ON' if enabled else 'OFF'}: Lvl {lvl}, action {action}", "warning")
        return _json({"status": "ok"})
    except Exception as e:
        return _json({"status": "error", "error": "Invalid input: " + str(e)})


async def _limit_watcher():
    while True:
        await asyncio.sleep(_LIMIT_EVERY)
        try:
            st = _limit_state()
            if not st["enabled"] or st["action"] != "pause":
                _limit_done.clear()
                continue
            for uid, acc in list(bot_state.accounts.items()):
                try:
                    lvl = int(acc.get("level", 0) or 0)
                except Exception:
                    lvl = 0
                if lvl >= st["max_level"]:
                    if uid not in _limit_done:
                        _limit_done.add(uid)
                        bot_state.set_paused(uid, True)
                        bot_state.update_status(uid, "PAUSED", 0)
                        bot_state.log(f"Level limit reached: {acc.get('nickname', uid)} Lvl {lvl} >= {st['max_level']} -> paused",
                                      "warning", uid)
                else:
                    _limit_done.discard(uid)
        except Exception as e:
            print("[LIMIT] watcher error:", repr(e))


# ==================== INDEX ====================
async def handle_index(request: web.Request):
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "<h1>templates/index.html not found!</h1>"
    return web.Response(text=content, content_type="text/html", charset="utf-8")


# ==================== START ====================
async def start_web_dashboard(host: str = "0.0.0.0", port: int = None):
    if port is None:
        port = int(os.environ.get("PORT", 20333))

    app = web.Application(client_max_size=4 * 1024 * 1024)
    r = app.router

    r.add_get("/", handle_index)

    r.add_post("/api/user/register", handle_user_register)
    r.add_post("/api/user/login", handle_user_login)
    r.add_post("/api/user/logout", handle_user_logout)
    r.add_get("/api/user/me", handle_user_me)

    r.add_get("/api/stats", handle_get_stats)

    r.add_get("/api/plans", handle_plans_list)
    r.add_post("/api/plans/order", handle_plan_order)
    r.add_get("/api/plans/my-orders", handle_my_orders)

    r.add_get("/api/free-plan/status", handle_free_plan_status)
    r.add_post("/api/free-plan/request", handle_free_plan_request)

    r.add_post("/api/account/add", handle_account_add)
    r.add_post("/api/account/bulk-add", handle_account_bulk_add)
    r.add_post("/api/account/delete", handle_account_delete)
    r.add_post("/api/account/refresh", handle_account_refresh)
    r.add_post("/api/account/restart", handle_account_restart)
    r.add_post("/api/account/pause", handle_account_pause)
    r.add_post("/api/account/ban-check", handle_account_ban_check)

    r.add_get("/api/admin/config", handle_config_get)
    r.add_post("/api/admin/config", handle_config_set)
    r.add_get("/api/admin/stopped", handle_stopped_list)
    r.add_post("/api/admin/resume", handle_account_resume)

    r.add_post("/api/admin/login", handle_admin_login)
    r.add_get("/api/admin/orders", handle_admin_orders)
    r.add_post("/api/admin/orders/action", handle_admin_orders_action)
    r.add_get("/api/admin/overview", handle_admin_overview)
    r.add_get("/api/admin/users", handle_admin_users)
    r.add_post("/api/admin/users/update", handle_admin_users_update)
    r.add_post("/api/admin/users/delete", handle_admin_users_delete)
    r.add_get("/api/admin/credentials", handle_admin_credentials)
    r.add_get("/api/admin/accounts", handle_admin_accounts)
    r.add_get("/api/admin/free-plan-requests", handle_admin_free_plan_requests)
    r.add_get("/api/admin/limit", handle_admin_limit_get)
    r.add_post("/api/admin/limit", handle_admin_limit_set)
    r.add_post("/api/admin/free-plan-action", handle_admin_free_plan_action)
    r.add_get("/api/admin/download-credentials", handle_admin_download_credentials)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    _bg_tasks.append(asyncio.create_task(_limit_watcher()))

    print("\033[92m" + "=" * 60 + "\033[0m")
    print(f"\033[92m[+] ZEXO Web Dashboard running on http://{host}:{port}\033[0m")
    print(f"\033[93m[!] Admin password: {ADMIN_PASSWORD}\033[0m")
    print("\033[92m" + "=" * 60 + "\033[0m")