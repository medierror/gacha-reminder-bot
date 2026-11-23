"""Game data: fetches event calendars and redeem codes.

- Calendars (Genshin / Star Rail / ZZZ): https://api.ennead.cc/mihoyo/<game>/calendar
- Redeem codes (Genshin / Star Rail / ZZZ / Honkai 3rd): https://hoyo-codes.seria.moe/codes?game=<game>
- Wuthering Waves events, banners and codes: game8.co guide pages (read a few times a day)
Honkai 3rd has no public calendar -> use /event and /recurring in Discord.
"""
import datetime as dt
import html
import logging
import re

import aiohttp

log = logging.getLogger("sources")

HEADERS = {"User-Agent": "gacha-reminder-discord-bot/1.0"}
CALENDAR_URL = "https://api.ennead.cc/mihoyo/{slug}/calendar"
CODES_URL = "https://hoyo-codes.seria.moe/codes?game={slug}"

GAMES = {
    "genshin": {
        "name": "Genshin Impact",
        "currency": "Primogems",
        "currency_item": "Primogem",
        "emoji": "✨",
        "color": 0xF2C94C,
        "channel": "genshin-impact",
        "calendar": "genshin",
        "codes": "genshin",
        "redeem": "https://genshin.hoyoverse.com/en/gift?code={code}",
        "checkin": "https://act.hoyolab.com/ys/event/signin-sea-v3/index.html?act_id=e202102251931481",
        "daily": "Daily commissions (+60 Primogems)",
    },
    "hsr": {
        "name": "Honkai: Star Rail",
        "currency": "Stellar Jade",
        "currency_item": "Stellar Jade",
        "emoji": "💎",
        "color": 0x8E7CC3,
        "channel": "honkai-star-rail",
        "calendar": "starrail",
        "codes": "hkrpg",
        "redeem": "https://hsr.hoyoverse.com/gift?code={code}",
        "checkin": "https://act.hoyolab.com/bbs/event/signin/hkrpg/index.html?act_id=e202303301540311",
        "daily": "Daily training (+60 Stellar Jade)",
    },
    "zzz": {
        "name": "Zenless Zone Zero",
        "currency": "Polychrome",
        "currency_item": None,  # the API gives a "polychrome" number directly
        "emoji": "📀",
        "color": 0xC7F000,
        "channel": "zenless-zone-zero",
        "calendar": "zenless",
        "codes": "nap",
        "redeem": "https://zenless.hoyoverse.com/redemption?code={code}",
        "checkin": "https://act.hoyolab.com/bbs/event/signin/zzz/e202406031448091.html?act_id=e202406031448091",
        "daily": "Daily engagement (+60 Polychrome)",
    },
    "wuwa": {
        "name": "Wuthering Waves",
        "currency": "Astrite",
        "currency_item": None,
        "emoji": "🌊",
        "color": 0x5AC8D8,
        "channel": "wuthering-waves",
        "calendar": "game8",  # scraped from game8.co (see fetch_wuwa)
        "codes": "game8",
        "redeem": None,
        "checkin": None,
        "daily": "Daily activity (+60 Astrite) · check in on the Kuro Games app",
        "currency_icon": "https://img.game8.co/4312299/350fce7d8386fed17d06e47cd22a467d.png/show",
    },
    "hi3": {
        "name": "Honkai Impact 3rd",
        "currency": "Crystals",
        "currency_item": None,
        "emoji": "🔮",
        "color": 0xE23D6B,
        "channel": "honkai-impact-3rd",
        "calendar": None,
        "codes": "honkai3rd",
        "redeem": None,  # HI3 codes are redeemed in-game
        "checkin": "https://act.hoyolab.com/bbs/event/signin-bh3/index.html?act_id=e202110291205111",
        "daily": "Daily tasks",
    },
}

# HoYoverse servers use fixed UTC offsets. The calendar API gives Asia-server times (UTC+8).
SERVER_UTC_OFFSET = {"asia": 8, "europe": 1, "america": -5, "tw": 8}


def _is_server_local(ts: int) -> bool:
    """Most events end at 03:59 / 04:00 *server time*, so they end later on Europe/America."""
    secs = (ts + 8 * 3600) % 86400
    return 3 * 3600 + 59 * 60 <= secs <= 4 * 3600 + 59


def adjust_for_server(ts, server: str):
    if not ts:
        return ts
    offset = SERVER_UTC_OFFSET.get(server, 8)
    return ts + (8 - offset) * 3600 if _is_server_local(ts) else ts


def _currency(game: str, it: dict) -> int:
    if game == "zzz":
        return int(it.get("polychrome") or 0)
    name = GAMES[game]["currency_item"]
    best = 0
    for r in it.get("rewards") or []:
        if r.get("name") == name:
            best = max(best, int(r.get("amount") or 0))
    sr = it.get("special_reward") or {}
    if sr.get("name") == name:
        best = max(best, int(sr.get("amount") or 0))
    return best


def _currency_icon(game: str, it: dict):
    name = GAMES[game]["currency_item"]
    for r in [it.get("special_reward") or {}] + list(it.get("rewards") or []):
        if r.get("name") == name and r.get("icon"):
            return r["icon"]
    return None


def _is_top_rarity(x: dict) -> bool:
    return str(x.get("rarity")) in ("5", "S")


def _banner_name(game: str, b: dict) -> str:
    chars = b.get("characters") or b.get("agents") or []
    weapons = b.get("weapons") or b.get("light_cones") or b.get("w_engines") or []
    top_chars = [c["name"] for c in chars if _is_top_rarity(c) and c.get("name")]
    top_weapons = [w["name"] for w in weapons if _is_top_rarity(w) and w.get("name")]
    is_weapon = "WEAPON" in str(b.get("banner_type", "")) or "Weapon" in str(b.get("name", ""))
    if is_weapon or not top_chars:
        label = {"genshin": "Weapon banner", "hsr": "Light Cone banner", "zzz": "W-Engine banner"}[game]
        names = top_weapons or top_chars
    else:
        label = "Banner"
        names = top_chars
    return f"{label}: {', '.join(names)}" if names else (b.get("name") or label)


async def fetch_calendar(session: aiohttp.ClientSession, game: str, server: str) -> list:
    slug = GAMES[game]["calendar"]
    async with session.get(CALENDAR_URL.format(slug=slug), headers=HEADERS,
                           timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)

    items = []
    for kind, section in (("event", "events"), ("challenge", "challenges"), ("banner", "banners")):
        for it in data.get(section) or []:
            end = it.get("end_time")
            if not end:
                continue
            start = it.get("start_time") or 0
            image = it.get("image_url")
            featured = []
            if kind == "banner":
                name = _banner_name(game, it)
                ident = it.get("id") or f"{it.get('banner_type')}-{start}"
                pool = [x for x in (it.get("characters") or it.get("agents") or [])
                        + (it.get("weapons") or it.get("light_cones") or it.get("w_engines") or [])
                        if _is_top_rarity(x) and x.get("name")]
                featured = [x["name"] for x in pool]
                icons = [x["icon"] for x in pool if x.get("icon")]
                image = image or (icons[0] if icons else None)
            else:
                name = it.get("name") or "Unknown"
                ident = it.get("id") or name
            items.append({
                "key": f"{game}:{kind}:{ident}:{name}",
                "game": game,
                "kind": kind,
                "name": name,
                "start": int(start),
                "end": int(adjust_for_server(end, server)),
                "currency": _currency(game, it) if kind != "banner" else 0,
                "image": image,
                "icon": _currency_icon(game, it) if kind != "banner" else None,
                "featured": featured,
                "official_image": False,
            })
    return items


# ---------------------------------------------------------------- official in-game announcements (HoYoverse)
# The same feed the in-game "Announcements" window uses. Times are exact for YOUR server, and every
# event / banner has its official artwork. Used to correct end times and add pictures.
ANNOUNCEMENTS = {
    "genshin": ("https://sg-hk4e-api.hoyoverse.com/common/hk4e_global/announcement/api/getAnnList",
                {"game": "hk4e", "game_biz": "hk4e_global", "bundle_id": "hk4e_global", "level": "55",
                 "uid": "100000000"},
                {"europe": "os_euro", "america": "os_usa", "asia": "os_asia", "tw": "os_cht"}),
    "hsr": ("https://sg-hkrpg-api.hoyoverse.com/common/hkrpg_global/announcement/api/getAnnList",
            {"game": "hkrpg", "game_biz": "hkrpg_global", "bundle_id": "hkrpg_global", "level": "70",
             "uid": "100000000", "channel_id": "1"},
            {"europe": "prod_official_eur", "america": "prod_official_usa", "asia": "prod_official_asia",
             "tw": "prod_official_cht"}),
    "zzz": ("https://sg-announcement-static.hoyoverse.com/common/nap_global/announcement/api/getAnnList",
            {"game": "nap", "game_biz": "nap_global", "bundle_id": "nap_global", "level": "60",
             "uid": "1300000000", "channel_id": "1"},
            {"europe": "prod_gf_eu", "america": "prod_gf_us", "asia": "prod_gf_jp", "tw": "prod_gf_sg"}),
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


async def fetch_announcements(session: aiohttp.ClientSession, game: str, server: str) -> list:
    url, params, regions = ANNOUNCEMENTS[game]
    # Same parameter order as the game client: the CDN caches each order separately and other orders can be stale
    params = {"game": params["game"], "game_biz": params["game_biz"], "lang": "en", "bundle_id": params["bundle_id"],
              "platform": "pc", "region": regions.get(server, regions["europe"]), "level": params["level"],
              "uid": params["uid"], **({"channel_id": params["channel_id"]} if "channel_id" in params else {})}
    async with session.get(url, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        data = (await r.json(content_type=None)).get("data") or {}
    tz = dt.timezone(dt.timedelta(hours=float(data.get("timezone") or 0)))
    found = {}

    def walk(o):
        if isinstance(o, dict):
            if "ann_id" in o and "title" in o:
                found[o["ann_id"]] = o
            else:
                for v in o.values():
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk([data.get("list"), data.get("pic_list")])
    anns = []
    for a in found.values():
        try:
            end = dt.datetime.strptime(a["end_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        except (KeyError, ValueError):
            continue
        anns.append({"title": _norm(_text(a.get("title", "")) + " " + _text(a.get("subtitle", ""))),
                     "end": int(end.timestamp()), "image": a.get("banner") or a.get("img") or None})
    return anns


def enrich_with_announcements(items: list, anns: list) -> None:
    """Match calendar items to official announcements -> exact end time + official artwork."""
    for it in items:
        if it["kind"] == "challenge":
            continue
        if it["kind"] == "banner":
            names = [_norm(n) for n in it.get("featured") or [] if len(_norm(n)) >= 3]
            cands = [a for a in anns if any(n in a["title"] for n in names)]
            if not cands:
                cands = [a for a in anns if re.search(r"\b(warp|channels?|wish)\b", a["title"])]
        else:
            name = _norm(it["name"])
            if len(name) < 4:
                continue
            cands = [a for a in anns if name in a["title"]]
        cands = [a for a in cands if abs(a["end"] - it["end"]) <= 2 * 86400]
        if not cands:
            continue
        best = min(cands, key=lambda a: abs(a["end"] - it["end"]))
        it["end"] = best["end"]
        if best["image"]:
            it["image"] = best["image"]
            it["official_image"] = True


async def fetch_codes(session: aiohttp.ClientSession, game: str) -> list:
    slug = GAMES[game]["codes"]
    async with session.get(CODES_URL.format(slug=slug), headers=HEADERS,
                           timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return [
        {"code": c["code"], "rewards": (c.get("rewards") or "").replace(";", ", ").replace("*", " ×")}
        for c in data.get("codes") or []
        if c.get("status", "OK") == "OK" and c.get("code")
    ]


# ---------------------------------------------------------------- Wuthering Waves (game8.co)
GAME8_WUWA = "https://game8.co/games/Wuthering-Waves/archives/{page}"
GAME8_EVENTS, GAME8_CODES, GAME8_BANNERS = 453473, 453149, 453303
DATE_RE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"
RANGE_RE = re.compile(DATE_RE + r"\s*(?:-|to)\s*" + DATE_RE)
IMG_RE = re.compile(r"<img[^>]*?alt=[\"']([^\"']*)[\"'][^>]*?data-src=[\"']([^\"']+)[\"']", re.S)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _wuwa_time(date_text: str, server: str, hour: int, minute: int) -> int:
    """game8 only gives dates; WuWa resets/ends at 04:00 server time like HoYo games."""
    d = dt.datetime.strptime(date_text, "%B %d, %Y")
    tz = dt.timezone(dt.timedelta(hours=SERVER_UTC_OFFSET.get(server, 1)))
    return int(d.replace(hour=hour, minute=minute, tzinfo=tz).timestamp())


def _article(page_html: str) -> str:
    i = page_html.find("archive-style-wrapper")
    return page_html[i:] if i >= 0 else page_html


async def _get(session, page) -> str:
    async with session.get(GAME8_WUWA.format(page=page), headers=HEADERS,
                           timeout=aiohttp.ClientTimeout(total=40)) as r:
        r.raise_for_status()
        return await r.text()


def parse_wuwa_events(page_html: str, server: str) -> list:
    body = _article(page_html)
    items = []
    for table in re.findall(r"<table.*?</table>", body, re.S):
        header = _text(table[:400])
        if not ("All Current Events" in header or "Upcoming Events" in header):
            continue
        for row in re.findall(r"<tr.*?</tr>", table, re.S):
            cells = re.findall(r"<td.*?</td>", row, re.S)
            dates = RANGE_RE.search(_text(row))
            if len(cells) < 2 or not dates:
                continue
            name = _text(cells[0])
            img = IMG_RE.search(cells[0])
            if not name and img:
                name = img.group(1).replace("Wuthering Waves - ", "")
            if not name:
                continue
            start = _wuwa_time(dates.group(1), server, 4, 0)
            items.append({
                "key": f"wuwa:event:{name}:{start}", "game": "wuwa", "kind": "event", "name": name,
                "start": start, "end": _wuwa_time(dates.group(2), server, 3, 59),
                "currency": 0, "image": img.group(2) if img else None,
            })
    return items


def parse_wuwa_banners(page_html: str, server: str) -> list:
    body = _article(page_html)
    stop = re.search(r"<h2[^>]*>[^<]*Permanent Convene", body)
    body = body[:stop.start()] if stop else body
    items, title, dates = [], None, None
    for m in re.finditer(r"<h([23])[^>]*>(.*?)</h[23]>|<table.*?</table>", body, re.S):
        block = m.group(0)
        if m.group(1) == "2":  # new section ("Current Banners", "Next Banners"): forget old dates
            title, dates = None, None
            continue
        if m.group(1) == "3":
            title = _text(m.group(2))
            continue
        text = _text(block)
        if "Banner Schedule Duration" in text:
            dates = RANGE_RE.search(text)
            continue
        if not (dates and title and "Rate-Up" in text):
            continue
        # featured 5-star = the images right after "5★ Limited Rate-Up"
        after = block[block.find("Rate-Up"):]
        five = after.split("4★")[0]
        featured = [(alt.replace("Wuthering Waves - ", ""), src) for alt, src in IMG_RE.findall(five)
                    if "Rank Icon" not in alt and "Icon" not in alt]
        kind = "Weapon banner" if "Weapon Banners" in text[:60] else "Banner"
        names = ", ".join(n for n, _ in featured) or title
        start = _wuwa_time(dates.group(1), server, 4, 0)
        items.append({
            "key": f"wuwa:banner:{kind}:{names}:{start}", "game": "wuwa", "kind": "banner",
            "name": f"{kind}: {names}", "start": start, "end": _wuwa_time(dates.group(2), server, 3, 59),
            "currency": 0, "image": featured[0][1] if featured else None,
        })
    return items


def parse_wuwa_codes(page_html: str) -> list:
    body = _article(page_html)
    stop = body.find("Expired Codes")
    body = body[:stop] if stop > 0 else body
    codes = []
    for table in re.findall(r"<table.*?</table>", body, re.S):
        for cell in re.findall(r"<td.*?</td>", table, re.S):
            code = re.search(r"a-clipboard__textInput[^>]*value=[\"']([^\"']+)", cell)
            if not code:
                continue
            rewards = re.findall(r"</a>\s*<b[^>]*>([^<]+)</b>", cell)
            names = [_text(n) for n in re.findall(r"<a [^>]*>(.*?)</a>", cell, re.S)]
            text = ", ".join(f"{n} {q.strip()}" for n, q in zip(names, rewards))
            codes.append({"code": code.group(1).strip(), "rewards": text})
    seen, unique = set(), []
    for c in codes:
        if c["code"] not in seen:
            seen.add(c["code"])
            unique.append(c)
    return unique


async def fetch_wuwa(session: aiohttp.ClientSession, server: str):
    """Returns (items, codes). Either can be None if that page failed."""
    items, codes = [], None
    ok = False
    for page, parser in ((GAME8_EVENTS, parse_wuwa_events), (GAME8_BANNERS, parse_wuwa_banners)):
        try:
            items += parser(await _get(session, page), server)
            ok = True
        except Exception as e:
            log.warning("game8 page %s failed: %s", page, e)
    try:
        codes = parse_wuwa_codes(await _get(session, GAME8_CODES))
    except Exception as e:
        log.warning("game8 codes failed: %s", e)
    return (items if ok else None), codes


# ---------------------------------------------------------------- game icons & key art (App Store)
# The App Store listing has the official app icon, and its first screenshot is the current version's key art,
# so the pictures stay up to date every patch.
APP_STORE_IDS = {"genshin": 1517783697, "hsr": 1599719154, "zzz": 1606356401, "wuwa": 6475033368,
                 "hi3": 1336342304}


async def fetch_game_art(session: aiohttp.ClientSession) -> dict:
    ids = ",".join(str(i) for i in APP_STORE_IDS.values())
    async with session.get("https://itunes.apple.com/lookup", params={"id": ids, "country": "us"},
                           headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    by_id = {res.get("trackId"): res for res in data.get("results") or []}
    art = {}
    for game, app_id in APP_STORE_IDS.items():
        res = by_id.get(app_id)
        if not res:
            continue
        shots = res.get("screenshotUrls") or res.get("ipadScreenshotUrls") or []
        big = re.sub(r"/\d+x\d+bb\.(jpg|png|webp)$", r"/1280x1280bb.\1", shots[0]) if shots else None
        art[game] = {"icon": res.get("artworkUrl512"), "art": big}
    return art
