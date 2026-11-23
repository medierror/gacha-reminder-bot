"""HoYoLAB account features: secure storage, daily check-in and real-time notes (resin / energy / dailies).

Security:
- Only HoYoLAB *ltoken* cookies are used. They can read game info and claim check-in rewards, but cannot
  change the password/email, log into the game or spend anything. No passwords are ever asked for.
- Cookies are encrypted (Fernet / AES) in data/accounts.enc. The encryption key lives in Windows Credential
  Manager, not next to the file, so copying the bot folder doesn't leak the accounts.
- Cookies are never printed to the console or posted in Discord. /unlink deletes them immediately.
"""
import json
import logging
import time
from pathlib import Path

import genshin
import keyring
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("accounts")

DATA_FILE = Path(__file__).parent / "data" / "accounts.enc"
KEYRING_SERVICE = "gacha-reminder-bot"
KEYRING_USER = "accounts-encryption-key"

# HoYoLAB game_biz -> our game keys
GAME_BIZ = {"hk4e_global": "genshin", "hkrpg_global": "hsr", "nap_global": "zzz", "bh3_global": "hi3"}
CHECKIN_GAME = {"genshin": genshin.Game.GENSHIN, "hsr": genshin.Game.STARRAIL, "zzz": genshin.Game.ZZZ,
                "hi3": genshin.Game.HONKAI}
NOTES_GAMES = ("genshin", "hsr", "zzz")  # games with real-time notes
STAMINA_NAME = {"genshin": "Original Resin", "hsr": "Trailblaze Power", "zzz": "Battery Charge"}
SERVER_HINTS = {"europe": ("euro", "eur", "_eu"), "america": ("usa", "_us", "america"),
                "asia": ("asia", "_jp", "_kr"), "tw": ("cht", "_sg")}
COOKIE_NAMES = ("ltuid_v2", "ltoken_v2", "ltmid_v2")


def _fernet() -> Fernet:
    key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    if not key:
        key = Fernet.generate_key().decode()
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
    return Fernet(key.encode())


class AccountStore:
    """{discord_user_id: {"cookies": {...}, "games": {game: {"uid", "nickname", "level", "server"}},
                          "linked_at": ts, "expired": bool}}"""

    def __init__(self):
        self.data: dict = {}
        if DATA_FILE.exists():
            try:
                self.data = json.loads(_fernet().decrypt(DATA_FILE.read_bytes()))
            except (InvalidToken, ValueError):
                log.error("Could not decrypt linked accounts (encryption key missing?). Please /link again.")

    def save(self):
        DATA_FILE.parent.mkdir(exist_ok=True)
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_bytes(_fernet().encrypt(json.dumps(self.data).encode()))
        tmp.replace(DATA_FILE)

    def get(self, user_id) -> dict | None:
        return self.data.get(str(user_id))

    def put(self, user_id, entry: dict):
        self.data[str(user_id)] = entry
        self.save()

    def remove(self, user_id) -> bool:
        existed = self.data.pop(str(user_id), None) is not None
        self.save()
        return existed

    def items(self):
        return [(int(uid), e) for uid, e in self.data.items() if not e.get("expired")]


def clean_cookies(ltuid: str, ltoken: str, ltmid: str) -> dict:
    def strip(v: str, name: str) -> str:
        v = v.strip().strip(";").strip()
        return v.split("=", 1)[1].strip() if v.startswith(name + "=") else v
    return {"ltuid_v2": strip(ltuid, "ltuid_v2"), "ltoken_v2": strip(ltoken, "ltoken_v2"),
            "ltmid_v2": strip(ltmid, "ltmid_v2")}


def _client(cookies: dict) -> genshin.Client:
    return genshin.Client(cookies, region=genshin.Region.OVERSEAS, lang="en-us")


def _server_score(server_code: str, server: str) -> int:
    code = (server_code or "").lower()
    return 1 if any(h in code for h in SERVER_HINTS.get(server, ())) else 0


async def find_game_accounts(cookies: dict, server: str) -> dict:
    """Validates the cookies and returns her game accounts (the one on your server, else highest level)."""
    accounts = await _client(cookies).get_game_accounts()
    games = {}
    for acc in accounts:
        game = GAME_BIZ.get(acc.game_biz)
        if not game:
            continue
        score = (_server_score(acc.server, server), acc.level)
        if game not in games or score > games[game]["_score"]:
            games[game] = {"uid": acc.uid, "nickname": acc.nickname, "level": acc.level,
                           "server": acc.server_name, "_score": score}
    for g in games.values():
        g.pop("_score")
    return games


async def check_in(cookies: dict, game: str):
    """Returns (status, reward). status: claimed / already / captcha / expired / error."""
    try:
        reward = await _client(cookies).claim_daily_reward(game=CHECKIN_GAME[game])
        return "claimed", {"name": reward.name, "amount": reward.amount, "icon": reward.icon}
    except genshin.AlreadyClaimed:
        return "already", None
    except (genshin.DailyGeetestTriggered, genshin.GeetestError):
        return "captcha", None
    except genshin.InvalidCookies:
        return "expired", None
    except genshin.GenshinException as e:
        log.warning("Check-in failed (%s): %s", game, e.retcode)
        return "error", None


async def get_notes(cookies: dict, game: str, uid: int) -> dict:
    """Normalized real-time notes. Raises genshin errors (DataNotPublic, InvalidCookies...)."""
    c = _client(cookies)
    if game == "genshin":
        n = await c.get_genshin_notes(uid)
        return {"stamina": n.current_resin, "max": n.max_resin,
                "full_at": int(time.time() + n.remaining_resin_recovery_time.total_seconds()),
                "daily_done": bool(n.claimed_commission_reward),
                "daily_text": f"Commissions {n.completed_commissions}/{n.max_commissions}"}
    if game == "hsr":
        n = await c.get_starrail_notes(uid)
        return {"stamina": n.current_stamina, "max": n.max_stamina,
                "full_at": int(time.time() + n.stamina_recover_time.total_seconds()),
                "daily_done": n.current_train_score >= n.max_train_score,
                "daily_text": f"Daily Training {n.current_train_score}/{n.max_train_score}"}
    if game == "zzz":
        n = await c.get_zzz_notes(uid)
        return {"stamina": n.battery_charge.current, "max": n.battery_charge.max,
                "full_at": int(time.time() + n.battery_charge.seconds_till_full),
                "daily_done": n.engagement.current >= n.engagement.max,
                "daily_text": f"Engagement {n.engagement.current}/{n.engagement.max}"}
    raise ValueError(game)
