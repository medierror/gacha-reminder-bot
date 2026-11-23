"""Gacha Tracker - Windows desktop companion for Genshin, Star Rail, ZZZ, Wuthering Waves and Honkai 3rd.

Shows events & banners with their official pictures and countdowns, redeem codes with 1-click redeem,
the player's live Resin / energy and dailies (optional HoYoLAB link), and sends Windows notifications
from the system tray. Shares its data code (sources.py, accounts.py) with the Discord bot.
"""
import asyncio
import ctypes
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
import winreg
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp
import pystray
import webview
from PIL import Image

FROZEN = hasattr(sys, "_MEIPASS")
HERE = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent
if not FROZEN:
    sys.path.insert(0, str(HERE.parent))  # sources.py / accounts.py live in the repo root
import accounts  # noqa: E402
import keyring  # noqa: E402
import sources  # noqa: E402
from keyring.backends.Windows import WinVaultKeyring  # noqa: E402
from sources import GAMES  # noqa: E402

# Inside the .exe keyring can't auto-discover its backends: always use Windows Credential Manager
keyring.set_keyring(WinVaultKeyring())

APP_NAME = "Gacha Tracker"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "GachaTracker"
APP_DIR.mkdir(parents=True, exist_ok=True)
UI_DIR = HERE / "ui"
ICON_PNG = HERE / "icon.png"
SETTINGS_FILE = APP_DIR / "settings.json"
STATE_FILE = APP_DIR / "state.json"
ACCOUNTS_FILE = APP_DIR / "account.enc"
KEYRING_SERVICE = "gacha-tracker-app"
ME = "me"  # single local user

logging.basicConfig(filename=APP_DIR / "tracker.log", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tracker")

DEFAULT_SETTINGS = {
    "server": "europe",
    "timezone": "Africa/Casablanca",
    "notify_new": True,
    "notify_codes": True,
    "notify_hours": [24, 3],
    "notify_resin": True,
    "resin_minutes": 60,
    "notify_daily": True,
    "daily_time": "20:00",
    "start_with_windows": False,
}
FETCH_EVERY, GAME8_EVERY, ART_EVERY, NOTES_EVERY = 30 * 60, 3 * 3600, 24 * 3600, 10 * 60
# Only these sites can be opened from the app (defense in depth against odd data)
SAFE_DOMAINS = ("hoyoverse.com", "hoyolab.com", "mihoyo.com", "kurogames.com", "game8.co", "github.com")


def load_json(path: Path, default: dict) -> dict:
    try:
        return {**default, **json.loads(path.read_text(encoding="utf-8"))}
    except (FileNotFoundError, ValueError):
        return json.loads(json.dumps(default))


def save_json(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- background engine (own asyncio thread)
class Engine:
    def __init__(self):
        self.settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
        self.state = load_json(STATE_FILE, {"seen": {}, "sent": {}, "codes_seen": {}, "seeded": [],
                                            "seeded_codes": [], "stamina_alerted": {}, "last_daily": None,
                                            "art": {}})
        self.account = accounts.AccountStore(ACCOUNTS_FILE, KEYRING_SERVICE)
        self.calendar, self.codes, self.notes, self.checkin = {}, {}, {}, {}
        self.last = defaultdict(float)
        self.updated = 0
        self.notifier = None  # set by the tray
        self.loop = asyncio.new_event_loop()
        self.wake = None
        self.session = None
        threading.Thread(target=self._run, daemon=True, name="engine").start()

    # -- thread plumbing
    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    def call(self, coro, timeout=60):
        """Run a coroutine on the engine loop from another thread and wait for the result."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _main(self):
        self.wake = asyncio.Event()
        self.session = aiohttp.ClientSession()
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("tick failed")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    def poke(self, force=False):
        if force:
            self.last.clear()
        if self.wake:
            self.loop.call_soon_threadsafe(self.wake.set)

    def save(self):
        save_json(STATE_FILE, self.state)

    @property
    def tz(self):
        try:
            return ZoneInfo(self.settings.get("timezone") or "UTC")
        except Exception:
            return ZoneInfo("UTC")

    # -- data
    def _due(self, name, every):
        if time.time() - self.last[name] >= every:
            self.last[name] = time.time()
            return True
        return False

    async def refresh(self):
        server = self.settings["server"]
        if self._due("art", ART_EVERY) or not self.state["art"]:
            try:
                self.state["art"] = await sources.fetch_game_art(self.session) or self.state["art"]
            except Exception as e:
                log.warning("art: %s", e)
        async def hoyo_calendar(game):
            try:
                items = await sources.fetch_calendar(self.session, game, server)
                try:
                    sources.enrich_with_announcements(
                        items, await sources.fetch_announcements(self.session, game, server))
                except Exception as e:
                    log.warning("announcements %s: %s", game, e)
                self.calendar[game] = items
            except Exception as e:
                log.warning("calendar %s: %s", game, e)

        async def hoyo_codes(game):
            try:
                self.codes[game] = await sources.fetch_codes(self.session, game)
            except Exception as e:
                log.warning("codes %s: %s", game, e)

        async def wuwa():
            items, codes = await sources.fetch_wuwa(self.session, server)
            if items is not None:
                self.calendar["wuwa"] = items
            if codes is not None:
                self.codes["wuwa"] = codes

        jobs = []  # everything downloads at the same time
        if self._due("hoyo", FETCH_EVERY):
            jobs += [hoyo_calendar(g) for g, v in GAMES.items() if v["calendar"] and v["calendar"] != "game8"]
            jobs += [hoyo_codes(g) for g, v in GAMES.items() if v["codes"] and v["codes"] != "game8"]
        if self._due("game8", GAME8_EVERY):
            jobs.append(wuwa())
        if jobs:
            await asyncio.gather(*jobs)
        self.updated = time.time()

    async def refresh_account(self):
        entry = self.account.get(ME)
        if not entry or entry.get("expired"):
            self.notes, self.checkin = {}, {}
            return
        for game in accounts.NOTES_GAMES:
            acc = entry["games"].get(game)
            if not acc:
                continue
            try:
                self.notes[game] = await accounts.get_notes(entry["cookies"], game, acc["uid"])
            except accounts.genshin.InvalidCookies:
                entry["expired"] = True
                self.account.put(ME, entry)
                self.notify("🔐 HoYoLAB login expired", "Open Gacha Tracker → Settings and connect again.")
                return
            except (accounts.genshin.DataNotPublic, accounts.genshin.UserNotesAccessDenied):
                self.notes[game] = {"error": "notes_off"}
            except Exception as e:
                log.info("notes %s: %s", game, type(e).__name__)
        for game in entry["games"]:
            if game in accounts.CHECKIN_GAME:
                self.checkin[game] = await accounts.checked_in_today(entry["cookies"], game)

    # -- notifications
    def notify(self, title: str, message: str):
        log.info("notify: %s | %s", title, message)
        if self.notifier:
            try:
                self.notifier(title[:63], message[:250])
            except Exception:
                log.exception("notify failed")

    async def tick(self):
        await self.refresh()
        if self._due("notes", NOTES_EVERY):
            await self.refresh_account()
        now = time.time()
        s, st = self.settings, self.state

        # new events / banners
        for game, lst in self.calendar.items():
            new = [i for i in lst if i["key"] not in st["seen"] and i["end"] > now]
            for i in lst:
                st["seen"][i["key"]] = i["end"]
            if game not in st["seeded"]:
                st["seeded"].append(game)
                continue
            if new and s["notify_new"]:
                total = sum(i.get("currency") or 0 for i in new)
                extra = f" (+{total:,} {GAMES[game]['currency']})" if total else ""
                self.notify(f"🆕 New in {GAMES[game]['name']}", ", ".join(i["name"] for i in new[:4]) + extra)

        # ending soon
        hours = sorted({float(h) for h in s["notify_hours"]}, reverse=True)
        groups = defaultdict(list)
        for i in (x for lst in self.calendar.values() for x in lst):
            left = (i["end"] - now) / 3600
            due = [h for h in hours if 0 < left <= h]
            if not due:
                continue
            h = min(due)
            base = f"{i['key']}:{i['end']}"
            if f"{base}|{h:g}" in st["sent"]:
                continue
            for hh in hours:
                if hh >= h:
                    st["sent"][f"{base}|{hh:g}"] = i["end"]
            groups[(i["game"], h)].append(i)
        for (game, h), items in groups.items():
            total = sum(i.get("currency") or 0 for i in items)
            extra = f" — {total:,} {GAMES[game]['currency']} left!" if total else ""
            label = f"{int(h // 24)} days" if h >= 48 else f"{h:g} hours"
            self.notify(f"⏰ {GAMES[game]['name']}: ends in < {label}",
                        ", ".join(i["name"] for i in items[:4]) + extra)

        # new codes
        for game, codes in self.codes.items():
            seen = set(st["codes_seen"].get(game, []))
            new = [c for c in codes if c["code"] not in seen]
            st["codes_seen"][game] = sorted(seen | {c["code"] for c in codes})
            if game not in st["seeded_codes"]:
                st["seeded_codes"].append(game)
                continue
            if new and s["notify_codes"]:
                self.notify(f"🎁 New {GAMES[game]['name']} code!",
                            "  ".join(c["code"] for c in new[:3]) + " — open Gacha Tracker to redeem")

        # resin / energy almost full
        window = int(s["resin_minutes"]) * 60
        for game, n in self.notes.items():
            if "stamina" not in n:
                continue
            key = game
            almost = n["stamina"] >= n["max"] or n["full_at"] - now <= window
            if almost and not st["stamina_alerted"].get(key):
                st["stamina_alerted"][key] = True
                if s["notify_resin"]:
                    name = accounts.STAMINA_NAME[game]
                    self.notify(f"🔋 {name} almost full!", f"{GAMES[game]['name']}: {n['stamina']}/{n['max']}")
            elif n["full_at"] - now > window + 2 * 3600:
                st["stamina_alerted"].pop(key, None)

        # evening dailies reminder
        local = dt.datetime.now(self.tz)
        try:
            hh, mm = (int(x) for x in s["daily_time"].split(":"))
        except ValueError:
            hh, mm = 20, 0
        if s["notify_daily"] and local.time() >= dt.time(hh, mm) and st["last_daily"] != local.date().isoformat():
            st["last_daily"] = local.date().isoformat()
            if self.notes:
                todo = [f"{GAMES[g]['name']}: {n['daily_text']}" for g, n in self.notes.items()
                        if "daily_done" in n and not n["daily_done"]]
                if todo:
                    self.notify("🌙 Dailies not done yet", " · ".join(todo))
            else:
                self.notify("🌙 Daily reminder", "Don't forget your dailies and check-ins today 💕")

        cutoff = now - 7 * 86400
        st["sent"] = {k: v for k, v in st["sent"].items() if v > cutoff}
        st["seen"] = {k: v for k, v in st["seen"].items() if v > cutoff}
        self.save()

    # -- what the window shows
    def snapshot(self) -> dict:
        entry = self.account.get(ME)
        linked = bool(entry and not entry.get("expired"))
        games = {}
        for k, g in GAMES.items():
            art = self.state["art"].get(k, {})
            games[k] = {"name": g["name"], "emoji": g["emoji"], "color": f"#{g['color']:06x}",
                        "currency": g["currency"], "icon": art.get("icon"), "art": art.get("art"),
                        "checkin": g["checkin"], "daily": g["daily"], "inGameRedeem": not g["redeem"],
                        "currencyIcon": next((i["icon"] for i in self.calendar.get(k, []) if i.get("icon")),
                                             g.get("currency_icon"))}
        codes = {k: [{**c, "url": GAMES[k]["redeem"].format(code=c["code"]) if GAMES[k]["redeem"] else None}
                     for c in v] for k, v in self.codes.items()}
        items = [{k: i.get(k) for k in ("key", "game", "kind", "name", "start", "end", "currency", "image",
                                        "icon", "official_image")}
                 for lst in self.calendar.values() for i in lst if i["end"] > time.time()]
        account = None
        if linked:
            account = {"games": entry["games"], "notes": self.notes, "checkin": self.checkin,
                       "staminaName": accounts.STAMINA_NAME}
        elif entry and entry.get("expired"):
            account = {"expired": True}
        return {"updated": self.updated, "games": games, "items": items, "codes": codes, "account": account,
                "settings": self.settings, "now": time.time()}


# ---------------------------------------------------------------- the API the window's JavaScript can call
class Api:
    def __init__(self, engine: Engine):
        self._engine = engine  # underscore: not exposed to JavaScript

    def get_data(self):
        return self._engine.snapshot()

    def refresh(self):
        self._engine.poke(force=True)
        return True

    def open_url(self, url: str):
        host = (urlparse(str(url)).hostname or "").lower()
        if str(url).startswith("https://") and any(host == d or host.endswith("." + d) for d in SAFE_DOMAINS):
            webbrowser.open(url)
            return True
        return False

    def copy(self, text: str):
        text = str(text)[:200]
        subprocess.run(["clip"], input=text.encode("utf-16-le"), creationflags=subprocess.CREATE_NO_WINDOW)
        return True

    def save_settings(self, new: dict):
        s = self._engine.settings
        if new.get("server") in sources.SERVER_UTC_OFFSET:
            server_changed = new["server"] != s["server"]
            s["server"] = new["server"]
        else:
            server_changed = False
        for key in ("notify_new", "notify_codes", "notify_resin", "notify_daily"):
            if key in new:
                s[key] = bool(new[key])
        if isinstance(new.get("notify_hours"), list):
            s["notify_hours"] = [h for h in (int(x) for x in new["notify_hours"]) if h in (72, 24, 3, 1)]
        if str(new.get("resin_minutes", "")).isdigit():
            s["resin_minutes"] = max(5, min(600, int(new["resin_minutes"])))
        t = str(new.get("daily_time", ""))
        if len(t) == 5 and t[2] == ":" and t.replace(":", "").isdigit():
            s["daily_time"] = t
        if "start_with_windows" in new:
            s["start_with_windows"] = bool(new["start_with_windows"])
            set_autostart(s["start_with_windows"])
        save_json(SETTINGS_FILE, s)
        self._engine.poke(force=server_changed)
        return s

    def link(self, ltuid: str, ltoken: str, ltmid: str):
        cookies = accounts.clean_cookies(str(ltuid), str(ltoken), str(ltmid))
        try:
            games = self._engine.call(accounts.find_game_accounts(cookies, self._engine.settings["server"]))
        except accounts.genshin.InvalidCookies:
            return {"ok": False, "error": "HoYoLAB didn't accept these. Copy the three _v2 values completely."}
        except Exception as e:
            log.warning("link failed: %s", type(e).__name__)  # never log the cookies
            return {"ok": False, "error": "Couldn't reach HoYoLAB right now. Try again in a minute."}
        if not games:
            return {"ok": False, "error": "No Genshin / Star Rail / ZZZ / Honkai 3rd account on this HoYoLAB."}
        self._engine.account.put(ME, {"cookies": cookies, "games": games, "linked_at": int(time.time()),
                                      "expired": False})
        self._engine.last.pop("notes", None)
        self._engine.poke()
        return {"ok": True, "games": games}

    def unlink(self):
        self._engine.account.remove(ME)
        self._engine.notes, self._engine.checkin = {}, {}
        return True


# ---------------------------------------------------------------- Windows helpers
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(enabled: bool):
    if not FROZEN:
        return  # only the built .exe registers itself
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "GachaTracker", 0, winreg.REG_SZ, f'"{sys.executable}" --minimized')
        else:
            try:
                winreg.DeleteValue(k, "GachaTracker")
            except FileNotFoundError:
                pass


def already_running() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "GachaTracker_single_instance")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


# ---------------------------------------------------------------- main: window + tray
def selftest():
    """`Gacha Tracker.exe --selftest`: checks the encrypted login storage works inside the .exe."""
    test_file = APP_DIR / "selftest.enc"
    store = accounts.AccountStore(test_file, KEYRING_SERVICE + "-selftest")
    store.put(ME, {"cookies": {"ltoken_v2": "v2_SELFTEST"}, "games": {}})
    ok = accounts.AccountStore(test_file, KEYRING_SERVICE + "-selftest").get(ME)["cookies"]["ltoken_v2"] == "v2_SELFTEST"
    ok = ok and b"SELFTEST" not in test_file.read_bytes()
    test_file.unlink()
    keyring.delete_password(KEYRING_SERVICE + "-selftest", accounts.KEYRING_USER)
    log.info("SELFTEST %s (keyring backend: %s)", "PASSED" if ok else "FAILED", type(keyring.get_keyring()).__name__)


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if already_running():
        ctypes.windll.user32.MessageBoxW(None, "Gacha Tracker is already running.\nLook for the ✦ icon in the "
                                               "tray, next to the clock.", APP_NAME, 0x40)
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GachaTracker.App")
    except Exception:
        pass
    engine = Engine()
    start_hidden = "--minimized" in sys.argv
    window = webview.create_window(APP_NAME, url=str(UI_DIR / "index.html"), js_api=Api(engine),
                                   width=1280, height=840, min_size=(940, 620), background_color="#0e0f16",
                                   hidden=start_hidden, text_select=True)
    quitting = {"now": False, "told": False}

    def show(*_):
        window.show()
        window.restore()

    def quit_app(icon, _item=None):
        quitting["now"] = True
        icon.stop()
        window.destroy()

    tray = pystray.Icon("GachaTracker", Image.open(ICON_PNG), APP_NAME, menu=pystray.Menu(
        pystray.MenuItem("Open Gacha Tracker", show, default=True),
        pystray.MenuItem("Refresh now", lambda *_: engine.poke(force=True)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app)))
    engine.notifier = lambda title, msg: tray.notify(msg, title)

    def on_closing():
        if quitting["now"]:
            return True
        window.hide()  # closing the window keeps the app running in the tray
        if not quitting["told"]:
            quitting["told"] = True
            engine.notify("✦ Still here!", "Gacha Tracker keeps running in the tray to send you reminders. "
                                           "Right-click the icon → Quit to close it completely.")
        return False

    window.events.closing += on_closing
    tray.run_detached()
    webview.start(http_server=True, private_mode=False, storage_path=str(APP_DIR / "webview"))
    quitting["now"] = True
    tray.stop()


if __name__ == "__main__":
    main()
