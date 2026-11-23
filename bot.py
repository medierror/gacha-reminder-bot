"""Gacha Reminder Bot - reminds you about ending events, banners, endgame resets,
redeem codes and dailies for Genshin, Star Rail, ZZZ, Wuthering Waves and Honkai 3rd."""
import asyncio
import datetime as dt
import json
import logging
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
import genshin
from discord import app_commands
from dotenv import load_dotenv

import accounts
import sources
from sources import GAMES

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

# ---------------------------------------------------------------- settings (.env)
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
TZ = ZoneInfo(os.getenv("TIMEZONE", "Africa/Casablanca").strip())
SERVER = os.getenv("GAME_SERVER", "europe").strip().lower()
REMINDER_HOURS = sorted({float(x) for x in os.getenv("REMINDER_HOURS", "72,24,3").split(",") if x.strip()},
                        reverse=True)
DAILY_TIME = os.getenv("DAILY_REMINDER_TIME", "20:00").strip()  # "off" to disable
REMIND_BANNERS = os.getenv("REMIND_BANNERS", "true").strip().lower() in ("1", "true", "yes")
MENTION = os.getenv("MENTION", "").strip()  # e.g. @everyone, or <@&ROLE_ID>
FETCH_EVERY = 30 * 60  # seconds between API refreshes
GAME8_EVERY = 3 * 3600  # game8.co pages (Wuthering Waves) are read every 3 hours to be polite
CHECK_EVERY_MIN = 5

KIND_ICON = {"event": "🎉", "challenge": "⚔️", "banner": "🎲", "manual": "📌", "recurring": "🔁"}
PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, embed_links=True,
                                  read_message_history=True, manage_channels=True, manage_messages=True,
                                  connect=True)

# The server layout /setup builds: (category, [(type, state key, channel name, topic)])
SERVER_LAYOUT = [
    ("👋 Welcome", [
        ("text", "welcome", "👋┃welcome", "Start here: what this server and the bot do"),
        ("text", "chat", "💬┃chat", "Talk about anything 💕"),
    ]),
    ("🎮 Games", [
        ("text", game, f"{g['emoji']}┃{g['channel']}",
         f"Everything {g['name']}: new events & banners, ending-soon reminders, redeem codes, daily check-in")
        for game, g in GAMES.items()
    ]),
    ("🔊 Voice", [
        ("voice", "voice", "🎧 Gaming together", None),
    ]),
]
# Discord's default channels/categories for a new server; /setup reuses or removes them
DEFAULT_TEXT, DEFAULT_VOICE = "general", "General"
DEFAULT_CATEGORIES = ("Text Channels", "Voice Channels")
# Shared channels from older versions; everything now goes into each game's own channel
OLD_SHARED_CHANNELS = ("general", "codes")
OLD_CATEGORIES = ("🔔 Reminders",)
STAMINA_ALERT_MINUTES = int(os.getenv("STAMINA_ALERT_MINUTES", "60"))  # alert this long before resin is full
NOTES_EVERY = 20 * 60

# ---------------------------------------------------------------- state (data/state.json)
DATA_DIR = Path(__file__).parent / "data"
STATE_FILE = DATA_DIR / "state.json"
DEFAULT_STATE = {
    "channels": {}, "sent": {}, "seen": {}, "seeded_items": [], "codes_seen": {},
    "seeded_codes": [], "manual": [], "recurring": [], "next_id": 1,
    "last_daily": None, "art": {}, "checkin_day": {}, "stamina_alerted": {},
}


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    return {**json.loads(json.dumps(DEFAULT_STATE)), **data}


def save_state():
    DATA_DIR.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


state = load_state()


# ---------------------------------------------------------------- helpers
def game_icon(game: str):
    return state["art"].get(game, {}).get("icon")


def game_art(game: str):
    return state["art"].get(game, {}).get("art")


def currency_icon(game: str):
    for item in bot.calendar.get(game, []):
        if item.get("icon"):
            return item["icon"]  # e.g. the Primogem / Stellar Jade icon from the event rewards
    return GAMES[game].get("currency_icon") or game_icon(game)


def brand(e: discord.Embed, game: str, label: str = "") -> discord.Embed:
    """Game name + app icon at the top of an embed."""
    name = GAMES[game]["name"] + (f" · {label}" if label else "")
    e.set_author(name=name, icon_url=game_icon(game))
    return e


def currency_text(game: str, amount: int) -> str:
    g = GAMES[game]
    return f"{g['emoji']} **{amount:,}** {g['currency']}" if amount else ""


def item_line(item: dict) -> str:
    parts = [f"{KIND_ICON.get(item['kind'], '•')} **{item['name']}**"]
    if item.get("currency"):
        parts.append(currency_text(item["game"], item["currency"]))
    if item["start"] > time.time():
        parts.append(f"starts <t:{item['start']}:R>")
    parts.append(f"ends <t:{item['end']}:R>")
    return " — ".join(parts)


def hours_label(h: float) -> str:
    if h >= 48 and h % 24 == 0:
        return f"{int(h // 24)} days"
    if h >= 1:
        return f"{h:g} hour" + ("" if h == 1 else "s")
    return f"{int(h * 60)} minutes"


def build_embeds(game: str, title: str, items: list, footer_total=True) -> list:
    """One or more embeds (Discord caps descriptions at 4096 chars)."""
    g = GAMES[game]
    lines = [item_line(i) for i in sorted(items, key=lambda i: i["end"])]
    chunks, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    chunks.append(cur)
    embeds = []
    for n, chunk in enumerate(chunks):
        e = discord.Embed(title=title if n == 0 else f"{title} (cont.)", description=chunk, color=g["color"])
        embeds.append(brand(e, game))
    if game_art(game):
        embeds[-1].set_image(url=game_art(game))
    total = sum(i.get("currency") or 0 for i in items)
    if footer_total and total:
        embeds[-1].set_footer(text=f"Total: {total:,} {g['currency']} up for grabs")
    pic = next((i["image"] for i in sorted(items, key=lambda i: i["end"]) if i.get("image")), None)
    if pic:
        embeds[0].set_thumbnail(url=pic)
    return embeds


KIND_LABEL = {"event": "Event", "challenge": "Endgame", "banner": "Banner", "manual": "Your reminder",
              "recurring": "Repeating reset"}


def card_embed(item: dict, note: str) -> discord.Embed:
    """A picture card for one event / banner / reset."""
    g = GAMES[item["game"]]
    lines = []
    if item.get("currency"):
        lines.append(f"{currency_text(item['game'], item['currency'])} to collect")
    if item["start"] > time.time():
        lines.append(f"🟢 Starts <t:{item['start']}:f> (<t:{item['start']}:R>)")
    lines.append(f"⏳ Ends <t:{item['end']}:f> (<t:{item['end']}:R>)")
    e = discord.Embed(title=f"{KIND_ICON.get(item['kind'], '•')} {item['name']}", description="\n".join(lines),
                      color=g["color"])
    brand(e, item["game"], KIND_LABEL.get(item["kind"], ""))
    e.set_footer(text=note)
    if item.get("image"):
        if item["kind"] == "banner" and not item.get("official_image"):
            e.set_thumbnail(url=item["image"])  # character icon
        else:
            e.set_image(url=item["image"])  # big event artwork
    else:
        e.set_thumbnail(url=item.get("icon") or (currency_icon(item["game"]) if item.get("currency")
                                                 else game_icon(item["game"])))
        if item["kind"] in ("manual", "recurring") and game_art(item["game"]):
            e.set_image(url=game_art(item["game"]))  # your own reminders get the game's key art
    return e


def card_message(game: str, headline: str, items: list, note: str):
    """Headline text + one picture card per item."""
    items = sorted(items, key=lambda i: (-(i.get("currency") or 0), i["end"]))
    total = sum(i.get("currency") or 0 for i in items)
    text = headline
    if total:
        text += f" — {currency_text(game, total)} up for grabs!"
    return text, [card_embed(i, note) for i in items]


DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(d|h|m)", re.I)
DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y")


def parse_when(text: str) -> int:
    """'3d 4h', '12h', '45m', '2026-12-01 04:00' or '01/12/2026 04:00' -> unix timestamp."""
    s = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return int(dt.datetime.strptime(s, fmt).replace(tzinfo=TZ).timestamp())
        except ValueError:
            pass
    parts = DUR_RE.findall(s)
    if parts and not DUR_RE.sub("", s).strip(" ,"):
        mult = {"d": 86400, "h": 3600, "m": 60}
        return int(time.time() + sum(float(n) * mult[u.lower()] for n, u in parts))
    raise ValueError(text)


# ---------------------------------------------------------------- discord client
class GachaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.http_session: aiohttp.ClientSession | None = None
        self.calendar: dict[str, list] = {}
        self.codes: dict[str, list] = {}
        self.last_fetch = 0.0
        self.last_game8 = 0.0
        self.last_art = 0.0
        self.last_notes = 0.0
        self.accounts = accounts.AccountStore()
        self.lock = asyncio.Lock()
        self.loop_task: asyncio.Task | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def on_ready(self):
        log.info("Logged in as %s", self.user)
        for guild in self.guilds:
            await self.sync_guild(guild)
        if not self.guilds:
            log.warning("The bot is not in any server yet. Invite it with this link:")
        print("\n  Invite link:", discord.utils.oauth_url(self.user.id, permissions=PERMISSIONS,
                                                          scopes=("bot", "applications.commands")), "\n")
        if not self.loop_task:
            self.loop_task = asyncio.create_task(self.main_loop())

    async def on_guild_join(self, guild):
        await self.sync_guild(guild)

    async def sync_guild(self, guild):
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands ready in %s", guild.name)

    # ------------------------------------------------------------ data refresh
    async def refresh_art(self):
        if state["art"] and time.time() - self.last_art < 86400:
            return
        self.last_art = time.time()
        try:
            state["art"] = await sources.fetch_game_art(self.http_session) or state["art"]
            save_state()
        except Exception as e:
            log.warning("Could not load game pictures: %s", e)

    async def refresh(self, force=False):
        await self.refresh_art()
        now = time.time()
        if force or now - self.last_fetch >= FETCH_EVERY:
            self.last_fetch = now
            for game, g in GAMES.items():
                if g["calendar"] and g["calendar"] != "game8":
                    try:
                        items = await sources.fetch_calendar(self.http_session, game, SERVER)
                    except Exception as e:
                        log.warning("Calendar fetch failed for %s: %s", game, e)
                        items = None
                    if items is not None and game in sources.ANNOUNCEMENTS:
                        try:  # official in-game announcements: exact end times + official artwork
                            anns = await sources.fetch_announcements(self.http_session, game, SERVER)
                            sources.enrich_with_announcements(items, anns)
                        except Exception as e:
                            log.warning("Announcements fetch failed for %s: %s", game, e)
                    if items is not None:
                        self.calendar[game] = items
                if g["codes"] and g["codes"] != "game8":
                    try:
                        self.codes[game] = await sources.fetch_codes(self.http_session, game)
                    except Exception as e:
                        log.warning("Code fetch failed for %s: %s", game, e)
        if force or now - self.last_game8 >= GAME8_EVERY:
            self.last_game8 = now
            items, codes = await sources.fetch_wuwa(self.http_session, SERVER)
            if items is not None:
                self.calendar["wuwa"] = items
            if codes is not None:
                self.codes["wuwa"] = codes

    def user_items(self) -> list:
        now = time.time()
        items = []
        for m in state["manual"]:
            items.append({"key": f"manual:{m['id']}", "game": m["game"], "kind": "manual", "name": m["name"],
                          "start": 0, "end": m["end"], "currency": m.get("currency") or 0, "image": None})
        for r in state["recurring"]:
            period = r["every_days"] * 86400
            while r["next_end"] <= now:
                r["next_end"] += period
            items.append({"key": f"recurring:{r['id']}", "game": r["game"], "kind": "recurring",
                          "name": r["name"], "start": 0, "end": r["next_end"],
                          "currency": r.get("currency") or 0, "image": None})
        return items

    def all_items(self) -> list:
        items = [i for lst in self.calendar.values() for i in lst]
        if not REMIND_BANNERS:
            items = [i for i in items if i["kind"] != "banner"]
        return items + self.user_items()

    # ------------------------------------------------------------ sending
    async def channel_for(self, key: str):
        cid = state["channels"].get(key)
        if not cid:
            return None
        ch = self.get_channel(cid)
        if ch is None:
            try:
                ch = await self.fetch_channel(cid)
            except discord.HTTPException:
                return None
        return ch

    async def post(self, key: str, embeds: list, ping=False, text=None):
        ch = await self.channel_for(key)
        if ch is None:
            log.warning("No channel for %s - run /setup", key)
            return
        content = " ".join(x for x in (MENTION if ping else "", text or "") if x) or None
        for i in range(0, len(embeds), 10):
            await ch.send(content=content if i == 0 else None, embeds=embeds[i:i + 10])

    # ------------------------------------------------------------ the main loop
    async def main_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.tick()
            except Exception:
                log.exception("Error during check")
            await asyncio.sleep(CHECK_EVERY_MIN * 60)

    async def tick(self, force_fetch=False):
        async with self.lock:
            if not state["channels"]:
                return  # /setup has not been run yet
            await self.refresh(force=force_fetch)
            now = time.time()
            items = self.all_items()
            await self.announce_new(now)
            await self.send_reminders(items, now)
            await self.announce_codes()
            await self.maybe_daily()
            await self.account_jobs(now)
            self.prune(now)
            save_state()

    async def announce_new(self, now):
        for game, lst in self.calendar.items():
            if not REMIND_BANNERS:
                lst = [i for i in lst if i["kind"] != "banner"]
            first_time = game not in state["seeded_items"]
            new = [i for i in lst if i["key"] not in state["seen"] and i["end"] > now]
            for i in lst:
                state["seen"][i["key"]] = i["end"]
            if first_time:
                state["seeded_items"].append(game)
                continue
            if not new:
                continue
            # don't also send a "less than X hours" reminder for something we just announced
            for i in new:
                for h in REMINDER_HOURS:
                    if (i["end"] - now) / 3600 <= h:
                        state["sent"][f"{i['key']}:{i['end']}|{h:g}"] = i["end"]
            text, cards = card_message(game, f"🆕 **New in {GAMES[game]['name']}!**", new, "New")
            await self.post(game, cards, text=text)

    async def send_reminders(self, items, now):
        groups = defaultdict(list)
        for item in items:
            remaining_h = (item["end"] - now) / 3600
            if remaining_h <= 0:
                continue
            due = [h for h in REMINDER_HOURS if remaining_h <= h]
            if not due:
                continue
            threshold = min(due)
            base = f"{item['key']}:{item['end']}"
            if f"{base}|{threshold:g}" in state["sent"]:
                continue
            for h in REMINDER_HOURS:
                if h >= threshold:
                    state["sent"][f"{base}|{h:g}"] = item["end"]
            groups[(item["game"], threshold)].append(item)

        for (game, threshold), group in sorted(groups.items(), key=lambda kv: -kv[0][1]):
            label = hours_label(threshold)
            text, cards = card_message(game, f"⏰ **{GAMES[game]['name']}: ending in less than {label}!**",
                                       group, f"⏰ Less than {label} left")
            await self.post(game, cards, ping=threshold <= 24, text=text)

    async def announce_codes(self):
        for game, codes in self.codes.items():
            seen = set(state["codes_seen"].get(game, []))
            new = [c for c in codes if c["code"] not in seen]
            state["codes_seen"][game] = sorted(seen | {c["code"] for c in codes})
            if game not in state["seeded_codes"]:
                state["seeded_codes"].append(game)
                continue
            if new:
                title = "🎁 New redeem code!" if len(new) == 1 else "🎁 New redeem codes!"
                await self.post(game, [code_embed(game, new, title)], ping=True)

    async def maybe_daily(self):
        if DAILY_TIME.lower() in ("off", "", "none"):
            return
        hh, mm = (int(x) for x in DAILY_TIME.split(":"))
        now_local = dt.datetime.now(TZ)
        today = now_local.date().isoformat()
        if now_local.time() >= dt.time(hh, mm) and state["last_daily"] != today:
            state["last_daily"] = today
            personal = await self.personal_dailies()
            for game in GAMES:
                embed, pings = daily_card(game, now_local, personal.get(game, []))
                await self.post(game, [embed], text=" ".join(pings) or None)

    # ------------------------------------------------------------ linked HoYoLAB accounts
    async def personal_dailies(self) -> dict:
        """{game: [(discord_user_id, notes)]} for everyone who linked their account."""
        out = defaultdict(list)
        for user_id, entry in self.accounts.items():
            for game in accounts.NOTES_GAMES:
                acc = entry["games"].get(game)
                if not acc:
                    continue
                try:
                    out[game].append((user_id, await accounts.get_notes(entry["cookies"], game, acc["uid"])))
                except Exception as e:
                    log.info("Notes unavailable for %s (%s)", game, type(e).__name__)
        return out

    async def cookies_expired(self, user_id: int, entry: dict):
        entry["expired"] = True
        self.accounts.put(user_id, entry)
        try:
            user = await self.fetch_user(user_id)
            await user.send("🔐 Your HoYoLAB login for the gacha reminder bot has expired (this happens after "
                            "changing your password or logging out everywhere). Use `/link` in the server to "
                            "connect again.")
        except discord.HTTPException:
            pass

    async def account_jobs(self, now):
        linked = self.accounts.items()
        if not linked:
            return
        # 1) automatic HoYoLAB daily check-in, once per HoYoLAB day (it resets at 00:00 UTC+8)
        hoyo_day = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        for user_id, entry in linked:
            for game in entry["games"]:
                key = f"{user_id}:{game}"
                if game not in accounts.CHECKIN_GAME or state["checkin_day"].get(key) == hoyo_day:
                    continue
                status, reward = await accounts.check_in(entry["cookies"], game)
                if status == "error":
                    continue  # try again on the next check
                state["checkin_day"][key] = hoyo_day
                if status == "expired":
                    await self.cookies_expired(user_id, entry)
                    break
                if status == "claimed":
                    await self.post(game, [checkin_embed(game, user_id, reward)])
                elif status == "captcha":
                    await self.post(game, [captcha_embed(game, user_id)], text=f"<@{user_id}>")
        # 2) resin / trailblaze power / battery alerts
        if now - self.last_notes < NOTES_EVERY:
            return
        self.last_notes = now
        window = STAMINA_ALERT_MINUTES * 60
        for user_id, entry in self.accounts.items():
            for game in accounts.NOTES_GAMES:
                acc = entry["games"].get(game)
                if not acc:
                    continue
                try:
                    notes = await accounts.get_notes(entry["cookies"], game, acc["uid"])
                except genshin.InvalidCookies:
                    await self.cookies_expired(user_id, entry)
                    break
                except Exception as e:
                    log.info("Notes unavailable for %s (%s)", game, type(e).__name__)
                    continue
                key = f"{user_id}:{game}"
                almost_full = notes["stamina"] >= notes["max"] or notes["full_at"] - now <= window
                if almost_full and not state["stamina_alerted"].get(key):
                    state["stamina_alerted"][key] = True
                    await self.post(game, [stamina_embed(game, user_id, notes)], text=f"<@{user_id}>")
                elif notes["full_at"] - now > window + 2 * 3600:
                    state["stamina_alerted"].pop(key, None)  # she spent it: alert again next time

    def prune(self, now):
        cutoff = now - 7 * 86400
        state["sent"] = {k: v for k, v in state["sent"].items() if v > cutoff}
        state["seen"] = {k: v for k, v in state["seen"].items() if v > cutoff}
        state["manual"] = [m for m in state["manual"] if m["end"] > now - 3600]


def code_embed(game: str, codes: list, title: str) -> discord.Embed:
    g = GAMES[game]
    lines = []
    for c in codes[:25]:
        link = f" — [redeem]({g['redeem'].format(code=c['code'])})" if g["redeem"] else ""
        rewards = f"\n  ↳ {c['rewards']}" if c["rewards"] else ""
        lines.append(f"`{c['code']}`{link}{rewards}")
    e = discord.Embed(title=title, description="\n".join(lines) or "No active codes.", color=g["color"])
    brand(e, game, "Redeem codes")
    if currency_icon(game):
        e.set_thumbnail(url=currency_icon(game))
    if game_art(game):
        e.set_image(url=game_art(game))
    if not g["redeem"]:
        e.set_footer(text="Redeem these in-game.")
    return e


def daily_card(game: str, now_local: dt.datetime, personal: list):
    """Daily reminder for one game channel. With a linked account it shows her real progress.
    Returns (embed, list of mentions to ping)."""
    g = GAMES[game]
    lines = [f"✅ {g['daily']}"]
    if g["checkin"]:
        lines.append(f"🗓️ [HoYoLAB daily check-in]({g['checkin']})")
    pings = []
    for user_id, notes in personal:
        if notes["daily_done"]:
            lines.append(f"🌟 <@{user_id}> — {notes['daily_text']}: all done, good job!")
        else:
            lines.append(f"⚠️ <@{user_id}> — {notes['daily_text']}: **not finished yet!**")
            pings.append(f"<@{user_id}>")
        lines.append(f"{accounts.STAMINA_NAME[game]}: **{notes['stamina']}/{notes['max']}**")
    if now_local.weekday() == 6:  # Sunday
        lines.append("\n📅 **Weekly reset tonight** (Monday 04:00 server time): finish your weekly bosses "
                     "and weekly rewards!")
    e = brand(discord.Embed(title="🌙 Daily reminder", description="\n".join(lines), color=g["color"]),
              game, "Dailies")
    if currency_icon(game):
        e.set_thumbnail(url=currency_icon(game))
    if game_art(game):
        e.set_image(url=game_art(game))
    return e, pings


def checkin_embed(game: str, user_id: int, reward: dict) -> discord.Embed:
    e = discord.Embed(title="✅ Daily check-in claimed automatically",
                      description=f"<@{user_id}> got **{reward['name']} ×{reward['amount']}** 🎁",
                      color=GAMES[game]["color"])
    if reward.get("icon"):
        e.set_thumbnail(url=reward["icon"])
    return brand(e, game, "HoYoLAB check-in")


def captcha_embed(game: str, user_id: int) -> discord.Embed:
    g = GAMES[game]
    e = discord.Embed(title="⚠️ Check-in needs you today",
                      description=f"HoYoLAB asked <@{user_id}> for a captcha, so the bot couldn't claim it.\n"
                                  f"👉 [Check in manually here]({g['checkin']}) (takes 5 seconds)",
                      color=g["color"])
    e.set_thumbnail(url=game_icon(game))
    return brand(e, game, "HoYoLAB check-in")


def stamina_embed(game: str, user_id: int, notes: dict) -> discord.Embed:
    name = accounts.STAMINA_NAME[game]
    full = notes["stamina"] >= notes["max"]
    desc = f"<@{user_id}> **{notes['stamina']}/{notes['max']}** {name}"
    desc += "\nIt's capped, so new energy is being wasted!" if full else f"\nFull <t:{notes['full_at']}:R>"
    e = discord.Embed(title=f"🔋 {name} is full!" if full else f"⏳ {name} almost full!", description=desc,
                      color=GAMES[game]["color"])
    e.set_thumbnail(url=game_icon(game))
    return brand(e, game, "Real-time notes")


bot = GachaBot()
GAME_CHOICES = [app_commands.Choice(name=g["name"], value=k) for k, g in GAMES.items()]


# ---------------------------------------------------------------- slash commands
def welcome_embeds() -> list:
    """Welcome guide + a picture card for every game channel."""
    ch = state["channels"]
    cards = []
    for k, g in GAMES.items():
        if k not in ch:
            continue
        e = brand(discord.Embed(description=f"{g['emoji']} <#{ch[k]}>\n✅ {g['daily']}", color=g["color"]), k)
        if game_art(k):
            e.set_image(url=game_art(k))
        cards.append(e)
    return [welcome_embed()] + cards


def welcome_embed() -> discord.Embed:
    ch = state["channels"]
    games = "\n".join(f"{g['emoji']} <#{ch[k]}> — {g['name']}" for k, g in GAMES.items() if k in ch)
    e = discord.Embed(
        title="👋 Welcome!",
        description="This is our little gacha corner. The bot watches the games for us so we never miss "
                    "free Primogems, Stellar Jade, Polychrome, Astrite or Crystals again. ✨",
        color=0xF47FFF)
    e.add_field(name="🎮 One channel per game", inline=False,
                value=games + "\nEach channel only gets its own game: new events & banners with pictures, "
                              "reminders **3 days, 24 hours and 3 hours** before something ends, new redeem "
                              "codes, and the daily check-in reminder every evening.")
    e.add_field(name="🔗 Link your HoYoLAB (optional)", inline=False,
                value="`/link` — the bot claims your HoYoLAB daily check-in automatically, warns you when "
                      "Resin / Trailblaze Power / Battery is almost full, and tells you if your dailies "
                      "aren't done. Private and encrypted, `/unlink` anytime.")
    e.add_field(name="⌨️ Commands", inline=False,
                value="`/upcoming` — what ends in the next 2 weeks\n"
                      "`/codes` — active redeem codes\n"
                      "`/event add` — add your own reminder (handy for Honkai 3rd events)\n"
                      "`/recurring add` — repeating resets, e.g. Tower of Adversity every 28 days\n"
                      "`/daily` — the daily checklist\n"
                      "`/status` — your live Resin / energy and dailies (after `/link`)")
    return e


async def build_server(guild: discord.Guild) -> list:
    """Create/arrange the categories and channels from SERVER_LAYOUT. Safe to run again."""
    s = state["channels"]
    log_lines = []
    # Reuse Discord's default #general and General voice instead of leaving them behind
    if not guild.get_channel(s.get("chat") or 0):
        if (c := discord.utils.get(guild.text_channels, name=DEFAULT_TEXT)) is not None:
            s["chat"] = c.id
    if not guild.get_channel(s.get("voice") or 0):
        if (c := discord.utils.get(guild.voice_channels, name=DEFAULT_VOICE)) is not None:
            s["voice"] = c.id

    for cat_name, channels in SERVER_LAYOUT:
        category = discord.utils.get(guild.categories, name=cat_name)
        if category is None:
            category = await guild.create_category(cat_name)
            log_lines.append(f"📁 created **{cat_name}**")
        for kind, key, name, topic in channels:
            ch = guild.get_channel(s.get(key) or 0) or discord.utils.get(guild.channels, name=name)
            if ch is None:
                if kind == "text":
                    ch = await guild.create_text_channel(name, category=category, topic=topic)
                else:
                    ch = await guild.create_voice_channel(name, category=category)
                log_lines.append(f"➕ created {ch.mention}")
            else:
                changes = {}
                if ch.name in (DEFAULT_TEXT, DEFAULT_VOICE):
                    changes["name"] = name
                if ch.category_id != category.id:
                    changes["category"] = category
                if kind == "text" and topic and ch.topic != topic:
                    changes["topic"] = topic
                if changes:
                    await ch.edit(**changes)
                    log_lines.append(f"✏️ updated {ch.mention}")
            s[key] = ch.id

    # Older versions had shared #daily-reminders / #redeem-codes channels. Remove them, but only if
    # nobody but the bot ever wrote there (otherwise keep them, so nothing of yours is lost).
    for key in OLD_SHARED_CHANNELS:
        old = guild.get_channel(s.pop(key, None) or 0)
        if old is None:
            continue
        messages = [m async for m in old.history(limit=500)]
        if all(m.author.id == guild.me.id for m in messages):
            await old.delete(reason="Posts now go to each game's own channel")
            log_lines.append(f"🧹 removed **#{old.name}** (now inside each game channel)")
        else:
            log_lines.append(f"ℹ️ kept {old.mention} because it has your messages; delete it if you like")

    # Remove Discord's default "Text Channels" / "Voice Channels" categories once they are empty
    all_channels = await guild.fetch_channels()
    for cat in [c for c in all_channels if isinstance(c, discord.CategoryChannel)
                and c.name in DEFAULT_CATEGORIES + OLD_CATEGORIES]:
        if not any(getattr(c, "category_id", None) == cat.id for c in all_channels):
            await cat.delete(reason="Replaced by the gacha reminder layout")
            log_lines.append(f"🧹 removed empty category **{cat.name}**")
    save_state()

    # Welcome message: posted once and pinned, refreshed on later /setup runs
    welcome = guild.get_channel(s["welcome"])
    if state.get("welcome_msg") and welcome is not None:
        try:
            msg = await welcome.fetch_message(state["welcome_msg"])
            await msg.edit(embeds=welcome_embeds())
            log_lines.append(f"✏️ refreshed the welcome message in {welcome.mention}")
        except discord.NotFound:
            state["welcome_msg"] = None
    if not state.get("welcome_msg") and welcome is not None:
        msg = await welcome.send(embeds=welcome_embeds())
        try:
            await msg.pin()
        except discord.HTTPException:
            pass
        state["welcome_msg"] = msg.id
        save_state()
        log_lines.append(f"📌 posted the welcome message in {welcome.mention}")
    return log_lines


@bot.tree.command(name="setup", description="Set up the server: categories, channels and welcome message")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def setup_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True, thinking=True)
    await bot.refresh(force=True)  # loads the game pictures for the welcome message
    try:
        changes = await build_server(inter.guild)
    except discord.Forbidden:
        await inter.followup.send("❌ I'm missing permissions. Re-invite me with the invite link from the "
                                  "bot window (it includes **Manage Channels** and **Manage Messages**).",
                                  ephemeral=True)
        return
    summary = "\n".join(changes) or "Everything was already set up."
    await inter.followup.send(f"✅ **Server is ready!**\n{summary}\n\nReminders will start appearing in a moment.",
                              ephemeral=True)
    asyncio.create_task(bot.tick())


@bot.tree.command(name="upcoming", description="Show everything ending in the next 2 weeks")
@app_commands.describe(game="Only show one game")
@app_commands.choices(game=GAME_CHOICES)
async def upcoming_cmd(inter: discord.Interaction, game: app_commands.Choice[str] | None = None):
    await inter.response.defer(thinking=True)
    await bot.refresh()
    now = time.time()
    items = [i for i in bot.all_items() if now < i["end"] <= now + 14 * 86400]
    games = [game.value] if game else list(GAMES)
    embeds = []
    for gk in games:
        lst = [i for i in items if i["game"] == gk]
        if lst:
            embeds += build_embeds(gk, f"{GAMES[gk]['emoji']} {GAMES[gk]['name']} — ending soon", lst)
    if not embeds:
        await inter.followup.send("Nothing ending in the next 2 weeks. 🎉")
        return
    for i in range(0, len(embeds), 10):
        await inter.followup.send(embeds=embeds[i:i + 10])


@bot.tree.command(name="codes", description="Show active redeem codes")
@app_commands.choices(game=[c for c in GAME_CHOICES if GAMES[c.value]["codes"]])
async def codes_cmd(inter: discord.Interaction, game: app_commands.Choice[str]):
    await inter.response.defer(thinking=True)
    await bot.refresh()
    await inter.followup.send(embed=code_embed(game.value, bot.codes.get(game.value, []), "🎁 Active codes"))


def game_of_channel(channel_id) -> str | None:
    return next((g for g in GAMES if state["channels"].get(g) == channel_id), None)


@bot.tree.command(name="daily", description="Show the daily checklist (this channel's game, or all games)")
async def daily_cmd(inter: discord.Interaction):
    await inter.response.defer(thinking=True)
    here = game_of_channel(inter.channel_id)
    personal = await bot.personal_dailies()
    now_local = dt.datetime.now(TZ)
    embeds = [daily_card(g, now_local, personal.get(g, []))[0] for g in ([here] if here else GAMES)]
    await inter.followup.send(embeds=embeds[:10])


# ---------------------------------------------------------------- linking a HoYoLAB account
LINK_GUIDE = (
    "**How to connect (2 minutes, on a computer):**\n"
    "1. Open **https://www.hoyolab.com** and log in.\n"
    "2. Press **F12** → open the **Application** tab (Chrome/Edge) or **Storage** (Firefox) → "
    "**Cookies** → `https://www.hoyolab.com`.\n"
    "3. Find **ltuid_v2**, **ltoken_v2** and **ltmid_v2** and copy their values.\n"
    "4. Click **🔐 Enter my cookies** below and paste them.\n\n"
    "For Resin / energy alerts, also turn on **Real-Time Notes**: HoYoLAB → your profile → Battle Chronicle → "
    "⚙️ settings → *Enable Real-Time Notes* (for each game).\n\n"
    "🔒 **Your safety:**\n"
    "• Only you can see this message and the form.\n"
    "• You never give your password. These cookies **can't** change your password or email, log into your "
    "game or spend anything. They can only read game info and claim check-in rewards.\n"
    "• They're stored **encrypted** on the bot's PC (key kept in Windows Credential Manager), never shown "
    "anywhere.\n"
    "• `/unlink` deletes them instantly. Logging out of HoYoLAB on all devices also makes them useless."
)


class LinkModal(discord.ui.Modal, title="Connect your HoYoLAB account"):
    ltuid = discord.ui.TextInput(label="ltuid_v2", placeholder="numbers, e.g. 123456789", max_length=200)
    ltoken = discord.ui.TextInput(label="ltoken_v2", placeholder="starts with v2_", max_length=2000,
                                  style=discord.TextStyle.paragraph)
    ltmid = discord.ui.TextInput(label="ltmid_v2", placeholder="e.g. 0abc12de34_hy", max_length=200)

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        cookies = accounts.clean_cookies(self.ltuid.value, self.ltoken.value, self.ltmid.value)
        try:
            games = await accounts.find_game_accounts(cookies, SERVER)
        except genshin.InvalidCookies:
            await inter.followup.send("❌ HoYoLAB didn't accept these. Make sure you copied the three **_v2** "
                                      "values completely, then try `/link` again.", ephemeral=True)
            return
        except Exception as e:
            log.warning("Linking failed: %s", type(e).__name__)  # never log the cookies themselves
            await inter.followup.send("❌ Couldn't reach HoYoLAB right now. Try again in a minute.", ephemeral=True)
            return
        if not games:
            await inter.followup.send("❌ This HoYoLAB account has no Genshin / Star Rail / ZZZ / Honkai 3rd "
                                      "game linked to it.", ephemeral=True)
            return
        bot.accounts.put(inter.user.id, {"cookies": cookies, "games": games, "linked_at": int(time.time()),
                                         "expired": False})
        e = discord.Embed(title="✅ Connected!", color=0x57F287,
                          description="From now on the bot will:\n"
                                      "🗓️ claim your HoYoLAB daily check-in automatically every day\n"
                                      "🔋 ping you when Resin / Trailblaze Power / Battery is almost full\n"
                                      "🌙 show your real dailies progress in the evening reminder")
        for game, acc in games.items():
            e.add_field(name=f"{GAMES[game]['emoji']} {GAMES[game]['name']}",
                        value=f"{acc['nickname']} · AR/Lv {acc['level']} · {acc['server']}", inline=False)
        e.set_footer(text="Encrypted on the bot's PC · /unlink removes it anytime")
        await inter.followup.send(embed=e, ephemeral=True)
        asyncio.create_task(bot.tick())  # today's check-in right away


class LinkView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id

    @discord.ui.button(label="Enter my cookies", emoji="🔐", style=discord.ButtonStyle.primary)
    async def open_form(self, inter: discord.Interaction, _button: discord.ui.Button):
        if inter.user.id != self.owner_id:
            await inter.response.send_message("This button isn't for you. Use `/link` yourself.", ephemeral=True)
            return
        await inter.response.send_modal(LinkModal())


@bot.tree.command(name="link", description="Connect your HoYoLAB account (auto check-in + resin alerts)")
async def link_cmd(inter: discord.Interaction):
    e = discord.Embed(title="🔗 Connect your HoYoLAB account", description=LINK_GUIDE, color=0x5865F2)
    await inter.response.send_message(embed=e, view=LinkView(inter.user.id), ephemeral=True)


@bot.tree.command(name="unlink", description="Disconnect your HoYoLAB account and delete your saved login")
async def unlink_cmd(inter: discord.Interaction):
    removed = bot.accounts.remove(inter.user.id)
    await inter.response.send_message("🗑️ Done. Your HoYoLAB login was deleted from the bot." if removed
                                      else "You don't have a linked account.", ephemeral=True)


@bot.tree.command(name="status", description="Your live Resin / energy and dailies (needs /link)")
async def status_cmd(inter: discord.Interaction):
    entry = bot.accounts.get(inter.user.id)
    if not entry or entry.get("expired"):
        await inter.response.send_message("Connect your account first with `/link`.", ephemeral=True)
        return
    await inter.response.defer(ephemeral=True, thinking=True)
    embeds = []
    for game in accounts.NOTES_GAMES:
        acc = entry["games"].get(game)
        if not acc:
            continue
        g = GAMES[game]
        try:
            n = await accounts.get_notes(entry["cookies"], game, acc["uid"])
            full = "**full!**" if n["stamina"] >= n["max"] else f"full <t:{n['full_at']}:R>"
            desc = (f"🔋 {accounts.STAMINA_NAME[game]}: **{n['stamina']}/{n['max']}** — {full}\n"
                    f"{'✅' if n['daily_done'] else '⚠️'} {n['daily_text']}")
        except (genshin.DataNotPublic, genshin.UserNotesAccessDenied):
            desc = "Turn on **Real-Time Notes** in HoYoLAB → Battle Chronicle settings to see this."
        except Exception as e:
            desc = f"Couldn't load right now ({type(e).__name__})."
        e = brand(discord.Embed(description=desc, color=g["color"]), game, f"{acc['nickname']} · {acc['uid']}")
        e.set_thumbnail(url=game_icon(game))
        embeds.append(e)
    await inter.followup.send(embeds=embeds or [discord.Embed(description="No Genshin / HSR / ZZZ account found.")],
                              ephemeral=True)


event_group = app_commands.Group(name="event", description="Your own reminders (great for Honkai 3rd)")
recurring_group = app_commands.Group(name="recurring", description="Repeating resets like Tower of Adversity or Abyss")
def entry_card(entry: dict, note: str) -> discord.Embed:
    """Picture card for something you added with /event or /recurring."""
    kind = "recurring" if "next_end" in entry else "manual"
    item = {"game": entry["game"], "kind": kind, "name": entry["name"], "start": 0,
            "end": entry.get("next_end", entry.get("end")), "currency": entry.get("currency") or 0, "image": None}
    return card_embed(item, note)


async def send_cards(inter: discord.Interaction, cards: list, empty: str):
    if not cards:
        await inter.response.send_message(empty)
        return
    await inter.response.send_message(embeds=cards[:10])
    for i in range(10, len(cards), 10):
        await inter.followup.send(embeds=cards[i:i + 10])


WHEN_HELP = "When it ends, e.g. '3d 5h', '12h' or '2026-12-01 04:00' (copy the timer from the game)"


@event_group.command(name="add", description="Add an event to be reminded about")
@app_commands.describe(name="Event name", ends=WHEN_HELP, currency="How much premium currency it gives (optional)")
@app_commands.choices(game=GAME_CHOICES)
async def event_add(inter: discord.Interaction, game: app_commands.Choice[str], name: str, ends: str,
                    currency: int = 0):
    try:
        end = parse_when(ends)
    except ValueError:
        await inter.response.send_message(f"❌ I couldn't read `{ends}`. {WHEN_HELP}.", ephemeral=True)
        return
    entry = {"id": state["next_id"], "game": game.value, "name": name, "end": end, "currency": currency}
    state["next_id"] += 1
    state["manual"].append(entry)
    save_state()
    await inter.response.send_message("📌 **Reminder added!**", embed=entry_card(entry, f"ID {entry['id']}"))


@event_group.command(name="list", description="List the events you added")
async def event_list(inter: discord.Interaction):
    cards = [entry_card(m, f"ID {m['id']} · remove with /event remove")
             for m in sorted(state["manual"], key=lambda m: m["end"])]
    await send_cards(inter, cards, "No events added yet. Use `/event add`.")


@event_group.command(name="remove", description="Remove an event you added")
@app_commands.describe(id="The ID shown in /event list")
async def event_remove(inter: discord.Interaction, id: int):
    before = len(state["manual"])
    state["manual"] = [m for m in state["manual"] if m["id"] != id]
    save_state()
    await inter.response.send_message("🗑️ Removed." if len(state["manual"]) < before else "No event with that ID.")


@recurring_group.command(name="add", description="Add a reset that repeats (e.g. every 14 or 28 days)")
@app_commands.describe(name="e.g. Tower of Adversity", ends="When the CURRENT cycle ends. " + WHEN_HELP,
                       every_days="How many days between resets", currency="Currency it gives (optional)")
@app_commands.choices(game=GAME_CHOICES)
async def recurring_add(inter: discord.Interaction, game: app_commands.Choice[str], name: str, ends: str,
                        every_days: app_commands.Range[int, 1, 365], currency: int = 0):
    try:
        end = parse_when(ends)
    except ValueError:
        await inter.response.send_message(f"❌ I couldn't read `{ends}`. {WHEN_HELP}.", ephemeral=True)
        return
    entry = {"id": state["next_id"], "game": game.value, "name": name, "next_end": end,
             "every_days": every_days, "currency": currency}
    state["next_id"] += 1
    state["recurring"].append(entry)
    save_state()
    await inter.response.send_message(f"🔁 **Added!** It repeats every {every_days} days.",
                                      embed=entry_card(entry, f"ID {entry['id']} · every {every_days} days"))


@recurring_group.command(name="list", description="List repeating resets")
async def recurring_list(inter: discord.Interaction):
    bot.user_items()  # advances finished cycles
    cards = [entry_card(r, f"ID {r['id']} · every {r['every_days']} days · remove with /recurring remove")
             for r in sorted(state["recurring"], key=lambda r: r["next_end"])]
    await send_cards(inter, cards, "Nothing yet. Use `/recurring add`.")


@recurring_group.command(name="remove", description="Remove a repeating reset")
@app_commands.describe(id="The ID shown in /recurring list")
async def recurring_remove(inter: discord.Interaction, id: int):
    before = len(state["recurring"])
    state["recurring"] = [r for r in state["recurring"] if r["id"] != id]
    save_state()
    await inter.response.send_message("🗑️ Removed." if len(state["recurring"]) < before else "No reset with that ID.")


bot.tree.add_command(event_group)
bot.tree.add_command(recurring_group)

if __name__ == "__main__":
    if not TOKEN or TOKEN == "paste-your-bot-token-here":
        raise SystemExit("Put your bot token in the .env file first (see README.md).")
    bot.run(TOKEN, log_handler=None)
