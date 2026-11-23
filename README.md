# 🎮 Gacha Reminder Bot

A Discord bot that reminds you about events that are ending, free premium currency and redeem codes for
**Genshin Impact, Honkai: Star Rail, Zenless Zone Zero, Wuthering Waves and Honkai Impact 3rd**.

## What it does

| | Genshin | Star Rail | ZZZ | Wuthering Waves | Honkai 3rd |
|---|:-:|:-:|:-:|:-:|:-:|
| Events ending soon, with pictures (and how many primos/jades/polychromes they give) | auto | auto | auto | auto | you add them |
| Endgame resets (Abyss, Theater, MoC, Pure Fiction, Shiyu, Deadly Assault…) | auto | auto | auto | `/recurring` | `/recurring` |
| Banners ending (with character art) | auto | auto | auto | auto | – |
| New redeem codes (with redeem link) | auto | auto | auto | auto | auto |
| Daily check-in / dailies reminder | ✓ | ✓ | ✓ | ✓ | ✓ |
| **With `/link`:** automatic HoYoLAB check-in | ✓ | ✓ | ✓ | – | ✓ |
| **With `/link`:** Resin / energy almost-full alerts + real dailies progress | ✓ | ✓ | ✓ | – | – |

Each game has **its own channel, and only that game's posts appear there**: new events and banners with
their official artwork, reminders **3 days, 24 hours and 3 hours** before something ends, new redeem codes
and the evening daily reminder (with a weekly-reset warning on Sundays).

End times for Genshin, Star Rail and ZZZ come from HoYoverse's **official in-game announcements for your
server**, so they're exact.

---

## Setup (about 10 minutes, one time only)

### 1. Create the Discord server
In Discord, click the **+** at the bottom of the server list → **Create My Own** → **For me and my friends**.
Give it a name, then invite your girlfriend (right-click the server → **Invite People**).

### 2. Create the bot
1. Go to <https://discord.com/developers/applications> and click **New Application**. Name it, e.g. *Paimon Reminders*.
2. Open the **Bot** tab on the left. You can set a profile picture here.
3. Click **Reset Token** → **Copy**. ⚠️ Keep this token secret: anyone who has it controls your bot.

### 3. Start the bot
1. Double-click **`start.bat`** in this folder. The first start installs everything and opens `.env` in Notepad.
2. Replace `paste-your-bot-token-here` with your token, then save and close Notepad.
3. Double-click **`start.bat`** again. It prints an **Invite link**. Open it in your browser,
   pick your server and click **Authorize**.

### 4. In Discord
Type **`/setup`** in any channel. The bot arranges the whole server:

```
👋 Welcome      #👋┃welcome (pinned guide)   #💬┃chat (your old #general)
🎮 Games        #✨┃genshin-impact  #💎┃honkai-star-rail  #📀┃zenless-zone-zero
                #🌊┃wuthering-waves #🔮┃honkai-impact-3rd
🔊 Voice        🎧 Gaming together (your old General voice channel)
```
Your channels are renamed and moved, never deleted. If an older version created `#daily-reminders` or
`#redeem-codes`, they're removed, but only when nothing but the bot ever posted in them. You can run
`/setup` again at any time; it only fixes what's missing.

That's it! 🎉

---

## Commands

| Command | What it does |
|---|---|
| `/upcoming` | Everything ending in the next 2 weeks (you can pick one game) |
| `/codes` | Active redeem codes for a game |
| `/daily` | The daily checklist (in a game channel: just that game) |
| `/link` | Connect your HoYoLAB account (see below) |
| `/status` | Your live Resin / Trailblaze Power / Battery and dailies (only you see it) |
| `/unlink` | Disconnect and delete your saved HoYoLAB login |
| `/event add` | Add your own reminder, e.g. a Honkai 3rd event. For `ends`, copy the in-game timer, like `5d 3h` |
| `/event list` / `/event remove` | See or delete your reminders |
| `/recurring add` | A reset that repeats, e.g. WuWa **Tower of Adversity**. Enter the time left in-game (`ends`) and how often it resets (`every_days`) |
| `/recurring list` / `/recurring remove` | See or delete repeating resets |

**Example (Wuthering Waves):** if the in-game timer for Tower of Adversity says 12 days 5 hours and it resets every 28 days:
`/recurring add game:Wuthering Waves name:Tower of Adversity ends:12d 5h every_days:28 currency:800`

---

## 🔗 Linking a HoYoLAB account (optional)

Each person runs `/link` themselves. The bot shows a private guide and a button that opens a private form:

1. On a computer, open <https://www.hoyolab.com> and log in.
2. Press **F12** → **Application** tab (Chrome/Edge) or **Storage** (Firefox) → **Cookies** → `https://www.hoyolab.com`.
3. Copy the values of **`ltuid_v2`**, **`ltoken_v2`** and **`ltmid_v2`** into the form.
4. For energy alerts, turn on **Real-Time Notes**: HoYoLAB → your profile → Battle Chronicle → ⚙️ → *Enable Real-Time Notes*.

Once linked, the bot:
- **claims the HoYoLAB daily check-in automatically** every day (Genshin, Star Rail, ZZZ, Honkai 3rd) and posts what you got in the game's channel;
- **pings you when Resin / Trailblaze Power / Battery is about 1 hour from full** (change it with `STAMINA_ALERT_MINUTES`);
- shows **your real dailies progress** in the evening reminder, and only pings you if they're not done.

### 🔒 Security
- **No password is ever asked for.** These cookies can't change your password or email, can't log into the game and can't spend anything.
- Everything happens privately: only you see the guide, the form and `/status`.
- Logins are stored **encrypted** in `data/accounts.enc`. The key is kept in **Windows Credential Manager**, not in the bot folder, so a copy of the folder is useless on its own.
- Logins are never printed in the bot window or posted in Discord.
- `/unlink` deletes your login immediately. Logging out of HoYoLAB on all devices (or changing your password) also makes the saved login stop working. If that happens, the bot DMs you to `/link` again.
- Wuthering Waves isn't supported for account features because Kuro has no equivalent of HoYoLAB.

---

## Settings (`.env` file)

| Setting | Default | Meaning |
|---|---|---|
| `GAME_SERVER` | `europe` | `europe`, `america`, `asia` or `tw`. End times are adjusted to this server |
| `TIMEZONE` | `Africa/Casablanca` | Used for the daily reminder time and for dates you type |
| `DAILY_REMINDER_TIME` | `20:00` | When to post the daily reminder (`off` to disable) |
| `REMINDER_HOURS` | `72,24,3` | How many hours before the end to remind |
| `REMIND_BANNERS` | `true` | Also remind about banners ending |
| `MENTION` | *(empty)* | Set to `@everyone` to get pinged for 24h/3h reminders and new codes |
| `STAMINA_ALERT_MINUTES` | `60` | How long before Resin / energy is full to ping a linked account |

Restart the bot after changing settings (close the window, then double-click `start.bat`).

## Keeping it running
The bot only works while `start.bat` is running on your PC. To start it automatically when Windows starts:
press `Win + R`, type `shell:startup`, press Enter, and put a **shortcut** to `start.bat` in that folder.
For 24/7 uptime you can run it on a small always-on machine (a Raspberry Pi, an old laptop or a cheap VPS).

## Where the data comes from
- Genshin / Star Rail / ZZZ: HoYoverse's official in-game announcements (exact times + official artwork), plus
  [api.ennead.cc](https://api.ennead.cc) for the list of events, endgame modes and how much currency each gives
- Game icons and current key art: the games' App Store pages (refreshed daily)
- Redeem codes: [hoyo-codes.seria.moe](https://hoyo-codes.seria.moe)
- Wuthering Waves events, banners and codes: the [game8.co](https://game8.co/games/Wuthering-Waves) guide pages, read every 3 hours
- Honkai 3rd has no public event calendar, so add its events with `/event` and `/recurring`.

These are free community services. If one goes down, the bot keeps running and catches up when it comes back.

---

## ☁️ Running 24/7 on a server

The `deploy/` folder runs the bot on a small Linux server (Ubuntu 24.04), so your PC can be off.

- **`server-setup.sh`** (once, as root): installs updates and turns on automatic security updates. It sets a firewall that blocks all incoming connections except SSH, allows SSH login with a key only (no passwords), and adds fail2ban against password guessing. It also creates a `gachabot` user with no login and no admin rights, and a **read-only** GitHub deploy key.
- **`install.sh`** (as root, any time): downloads or updates the bot from GitHub, installs the libraries and restarts it. This is also how you **update** the bot.
- **`gacha-bot.service`**: runs the bot in the background and restarts it if it crashes or the server reboots. It's sandboxed, so the bot can only write to its own `data/` folder.

Useful commands on the server:
```
sudo systemctl status gacha-bot        # is it running?
sudo journalctl -u gacha-bot -f        # live log
sudo bash /home/gachabot/gacha-reminder-bot/deploy/install.sh   # update to the latest version
```
⚠️ Run the bot in **one place only**. If it runs on the PC and the server at the same time, every message gets posted twice.

---

## 💻 Gacha Tracker: the Windows app

A desktop companion that uses the same data as the bot, but runs on your own PC, no Discord needed:

- **Events & banners gallery**: one page per game with the official artwork, how many primos/jades/polychromes are left to collect, and live countdowns.
- **Redeem codes**: a **Copy** button, plus a **Redeem** button that opens the official redeem page with the code already filled in. Honkai 3rd and Wuthering Waves codes are redeemed in-game.
- **Windows notifications** for new events, new codes, things ending soon, energy almost full and an evening dailies reminder. It lives in the tray (near the clock) and can start with Windows.
- **Your energy & dailies**: connect HoYoLAB in **Settings** to see live Resin / Trailblaze Power / Battery, dailies and check-in status. The login is encrypted on that PC only (key in Windows Credential Manager).

**Get it:** run `app\build.bat`. It creates `dist\Gacha Tracker.exe`, a single file you can copy to any Windows 10/11 PC.

- The first time it runs, Windows SmartScreen may say *"Windows protected your PC"*, because the app isn't signed by a company. Click **More info → Run anyway**.
- Closing the window keeps it running in the tray. To fully close it, right-click the ✦ tray icon → **Quit**.
- Its settings and log live in `%APPDATA%\GachaTracker`.
