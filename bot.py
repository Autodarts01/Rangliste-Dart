import discord
from discord import app_commands
import re
import os
import json
import random
import io
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import asyncio
from PIL import Image, ImageDraw, ImageFont

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("TOKEN")
LOG_CHANNEL_ID = 1492394175906320605
STATS_CHANNEL_ID = 1513493210910167170
TABELLE_CHANNEL_ID = 1492394072369922118
SPIELER_INFO_CHANNEL_ID = 1514183688445759509
ABWESENHEIT_CHANNEL_ID = 1465638993415770286
GEBURTSTAGE_CHANNEL_ID = 1514226619231899708
CHANNEL_NAME = "bullseye-rangliste-ergebnisse"
MAX_MATCHES_PER_DAY = 10
LANZI_NAME = "lanzi_90"

# 🔐 ADMIN ROLE ID
ADMIN_ROLE_IDS = [1463106884595880031]

lanzi_insult_index = 0

# 🤝 UNENTSCHIEDEN-SPRÜCHE
UNENTSCHIEDEN_SPRUECHE = [
    "Unentschieden?! Selbst die KI schüttelt den Kopf 🤖",
    "Ein Unentschieden beim Dart... das schafft auch nur ihr 😂",
    "🤝 Keiner gewinnt, keiner verliert — beide Verlierer 😂",
    "Unentschieden? Habt ihr überhaupt getroffen? 🎯",
    "Die KI weint gerade... Unentschieden beim Dart 😭",
]

# 🏆 DOMINANZ-SPRÜCHE
def get_dominanz_spruch(winner, score_diff):
    if score_diff >= 3:
        sprueche = [
            f"Jemand hat heute zu viel geübt 😏",
            f"Die Dartscheibe hat Angst bekommen 🎯😂",
            f"Selbst der Schiedsrichter hat gelacht 😂",
            f"War das Dart oder ein Kunstwerk? 🎨",
            f"Die Pfeile wussten wo sie hingehören 🏹",
            f"Jemand hat heute seinen Spinat gegessen 🥬💪",
            f"Die Schwerkraft hat heute mitgespielt 🌍",
            f"Statistisch unmöglich — aber hier sind wir 📊",
            f"Der Gegner braucht jetzt erstmal Urlaub 🏖️",
            f"So trifft man, wenn man die Augen zumacht 😎",
        ]
        return random.choice(sprueche)
    return None

# =========================
# TABELLE MESSAGE ID
# =========================
last_tabelle_message_id = None

# =========================
# MEILENSTEINE
# =========================
SPIELE_MEILENSTEINE = {
    10: "10 Spiele — der Anfang einer Legende! Weiter so! 🚀",
    25: "25 Spiele — du meinst es ernst! Die Scheibe hat Respekt! 🎯",
    50: "50 Spiele — halbe Hundert! Du bist nicht mehr aufzuhalten! 💪",
    100: "100 Spiele — absolute Legende! Die Halle gehört dir! 👑",
}

SIEGE_MEILENSTEINE = {
    10: "10 Siege — Bronzerang verdient! Die Gegner zittern! 🥉",
    25: "25 Siege — Silber! Du weißt wie man gewinnt! 🥈",
    50: "50 Siege — Gold! Eine Klasse für sich! 🥇",
    100: "100 Siege — unsterblich! Niemand kommt dir gleich! 👑",
}


async def check_meilensteine(spieler_name, spielabsprachen_channel):
    """Prüft ob ein Spieler einen Meilenstein erreicht hat."""
    try:
        stats = get_stats_from_sheet()
        s = None
        for k, v in stats.items():
            if normalize(k) == normalize(spieler_name):
                s = v
                break
        if not s:
            return

        for milestone, spruch in SPIELE_MEILENSTEINE.items():
            if s["spiele"] == milestone:
                await spielabsprachen_channel.send(
                    f"🎉 **Meilenstein fuer {spieler_name}!**\n{spruch}"
                )

        for milestone, spruch in SIEGE_MEILENSTEINE.items():
            if s["siege"] == milestone:
                await spielabsprachen_channel.send(
                    f"🎉 **Meilenstein fuer {spieler_name}!**\n{spruch}"
                )
    except Exception as e:
        print("❌ MEILENSTEIN ERROR:", e)


# =========================
# DISCORD SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# =========================
# GOOGLE SHEETS
# =========================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_json = os.getenv("GOOGLE_CREDENTIALS")
if not creds_json:
    raise Exception("❌ GOOGLE_CREDENTIALS fehlt!")

creds_dict = json.loads(creds_json)

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    creds_dict,
    scope
)

gs_client = gspread.authorize(creds)

sheet = gs_client.open_by_key(
    "19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc"
).worksheet("Ergebnisse")

# =========================
# MEMORY
# =========================
match_count = defaultdict(int)
last_reset = date.today()
today_matches = []  # für Tagesauswertung: liste von dicts

# =========================
# REGEX
# =========================
pattern = re.compile(
    r"(.+?)\s*(?:vs\.?|gegen)\s*(.+?)\s*[\(\[]?\s*(\d+)\s*[:\-]\s*(\d+)",
    re.IGNORECASE
)

# =========================
# HELPERS
# =========================
def reset_daily():
    global match_count, last_reset, today_matches
    if date.today() != last_reset:
        match_count = defaultdict(int)
        last_reset = date.today()
        today_matches = []


def normalize(name):
    return name.strip().lower().replace("\u00A0", "")


def remaining(player):
    return MAX_MATCHES_PER_DAY - match_count[normalize(player)]


def is_admin(member):
    return any(role.id in ADMIN_ROLE_IDS for role in member.roles)


def resolve_names(message, raw_text):
    text = raw_text

    for m in message.mentions:
        text = text.replace(f"<@{m.id}>", m.display_name)
        text = text.replace(f"<@!{m.id}>", m.display_name)

    match = pattern.search(text)

    if not match:
        return None

    p1, p2, s1, s2 = match.groups()
    p1 = re.sub(r"[\(\[\s]+$", "", p1).strip()
    p2 = re.sub(r"[\(\[\s]+$", "", p2).strip()

    return p1, p2, s1, s2


def get_stats_from_sheet():
    rows = sheet.get_all_values()
    stats = defaultdict(lambda: {"siege": 0, "niederlagen": 0, "spiele": 0})

    for row in rows:
        if len(row) < 7:
            continue
        p1 = row[0].strip()
        p2 = row[1].strip()
        winner = row[6].strip()
        if not p1 or not p2 or p1.lower() == "spieler a":
            continue
        stats[p1]["spiele"] += 1
        stats[p2]["spiele"] += 1
        if winner and winner.lower() != "unentschieden":
            if normalize(winner) == normalize(p1):
                stats[p1]["siege"] += 1
                stats[p2]["niederlagen"] += 1
            elif normalize(winner) == normalize(p2):
                stats[p2]["siege"] += 1
                stats[p1]["niederlagen"] += 1

    return stats


def get_streak_from_sheet(player_name):
    rows = sheet.get_all_values()
    streak = 0
    for row in reversed(rows):
        if len(row) < 7:
            continue
        p1 = row[0].strip()
        p2 = row[1].strip()
        winner = row[6].strip()
        if p1.lower() == "spieler a":
            continue
        if normalize(p1) == normalize(player_name) or normalize(p2) == normalize(player_name):
            if normalize(winner) == normalize(player_name):
                streak += 1
            else:
                break
    return streak


def get_all_match_rows():
    wb = gs_client.open_by_key("19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc")
    all_rows = []
    for ws in wb.worksheets():
        if ws.title == "Ergebnisse" or ws.title.startswith("Archiv_"):
            all_rows.extend(ws.get_all_values())
    return all_rows


def get_tabelle():
    rows = sheet.get_all_values()
    stats = {}

    for row in rows:
        if len(row) < 7:
            continue
        p1 = row[0].strip()
        p2 = row[1].strip()
        try:
            legs_a = int(row[2])
            legs_b = int(row[3])
        except:
            continue
        winner = row[6].strip()

        if not p1 or not p2:
            continue
        if p1.lower() == "spieler a":
            continue

        for p in [p1, p2]:
            if p not in stats:
                stats[p] = {"spiele": 0, "siege": 0, "niederlagen": 0, "legs_plus": 0, "legs_minus": 0}

        stats[p1]["spiele"] += 1
        stats[p1]["legs_plus"] += legs_a
        stats[p1]["legs_minus"] += legs_b

        stats[p2]["spiele"] += 1
        stats[p2]["legs_plus"] += legs_b
        stats[p2]["legs_minus"] += legs_a

        if winner and winner.lower() != "unentschieden":
            if winner in stats:
                stats[winner]["siege"] += 1
            loser = p2 if normalize(winner) == normalize(p1) else p1
            if loser in stats:
                stats[loser]["niederlagen"] += 1

    result = []
    for name, s in stats.items():
        punkte = s["siege"] * 3
        leg_dif = s["legs_plus"] - s["legs_minus"]
        result.append({
            "name": name,
            "spiele": s["spiele"],
            "siege": s["siege"],
            "niederlagen": s["niederlagen"],
            "legs_plus": s["legs_plus"],
            "legs_minus": s["legs_minus"],
            "leg_dif": leg_dif,
            "punkte": punkte,
        })

    result.sort(key=lambda x: (x["punkte"], x["leg_dif"]), reverse=True)
    return result


def generate_tabelle_image(tabelle, title):
    FONT_SIZE = 16
    PADDING = 16
    ROW_HEIGHT = 24
    BG_COLOR = (44, 47, 51)
    HEADER_COLOR = (255, 255, 255)
    ROW_COLOR = (220, 220, 220)
    ALT_ROW_COLOR = (180, 180, 180)
    SEP_COLOR = (100, 100, 100)
    TITLE_COLOR = (255, 255, 255)

    import urllib.request, tempfile
    font_file = "/tmp/mono.ttf"
    if not os.path.exists(font_file):
        try:
            urllib.request.urlretrieve(
                "https://github.com/google/fonts/raw/main/apache/roboto/static/RobotoMono-Regular.ttf",
                font_file
            )
        except:
            font_file = None

    try:
        font = ImageFont.truetype(font_file, FONT_SIZE) if font_file else ImageFont.load_default()
        title_font = ImageFont.truetype(font_file, 18) if font_file else font
    except:
        font = ImageFont.load_default()
        title_font = font

    cols = ["Rg", "Name", "Sp", "S", "N", "L+", "L-", "Dif", "Pkt"]
    col_widths = [35, 180, 35, 35, 35, 45, 45, 45, 45]
    total_width = sum(col_widths) + PADDING * 2

    num_rows = len(tabelle)
    total_height = PADDING + 30 + ROW_HEIGHT + 4 + (num_rows * ROW_HEIGHT) + PADDING

    img = Image.new("RGB", (total_width, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = PADDING
    draw.text((PADDING, y), title, font=title_font, fill=TITLE_COLOR)
    y += 30

    x = PADDING
    for col, w in zip(cols, col_widths):
        draw.text((x, y), col, font=font, fill=HEADER_COLOR)
        x += w
    y += ROW_HEIGHT

    draw.line([(PADDING, y), (total_width - PADDING, y)], fill=SEP_COLOR, width=1)
    y += 4

    for i, p in enumerate(tabelle):
        color = ALT_ROW_COLOR if i % 2 == 0 else ROW_COLOR
        values = [
            str(i + 1),
            p["name"],
            str(p["spiele"]),
            str(p["siege"]),
            str(p["niederlagen"]),
            str(p["legs_plus"]),
            str(p["legs_minus"]),
            str(p["leg_dif"]),
            str(p["punkte"]),
        ]
        x = PADDING
        for val, w in zip(values, col_widths):
            draw.text((x, y), val, font=font, fill=color)
            x += w
        y += ROW_HEIGHT

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def post_tabelle():
    global last_tabelle_message_id
    try:
        tabelle = get_tabelle()
        if not tabelle:
            return
        channel = await client.fetch_channel(TABELLE_CHANNEL_ID)

        if last_tabelle_message_id:
            try:
                old_msg = await channel.fetch_message(last_tabelle_message_id)
                await old_msg.delete()
            except:
                pass

        title = f"Aktuelle Tabelle  {datetime.now().strftime('%d.%m.%Y %H:%M')} Uhr"
        img_buf = generate_tabelle_image(tabelle, title)
        new_msg = await channel.send(file=discord.File(img_buf, filename="tabelle.png"))
        last_tabelle_message_id = new_msg.id
    except Exception as e:
        print("❌ TABELLE ERROR:", e)


async def tabelle_scheduler():
    await client.wait_until_ready()
    UTC_OFFSET = 2
    post_times_local = [(7, 0), (14, 0), (18, 0), (22, 0)]
    post_times_utc = [((h - UTC_OFFSET) % 24, m) for h, m in post_times_local]

    while not client.is_closed():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        next_post = None
        for hour, minute in sorted(post_times_utc):
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                next_post = candidate
                break
        if next_post is None:
            next_post = (now + timedelta(days=1)).replace(
                hour=post_times_utc[0][0], minute=post_times_utc[0][1], second=0, microsecond=0
            )

        wait_seconds = (next_post - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        await post_tabelle()
        
# ==========================================
# 🏠 DISCORD SERVER
# ==========================================
GUILD_ID = 1463104622779695159
GUILD_OBJECT = discord.Object(id=GUILD_ID)
        
# ==========================================
# 📰 MANFRED NEWS
# ==========================================
MANFRED_NEWS_CHANNEL = "manfred-news"
MANFRED_NEWS_MARKER = "Manfred_News_marker.txt"

def lese_news_marker():
    try:
        if os.path.exists(MANFRED_NEWS_MARKER):
            with open(MANFRED_NEWS_MARKER, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception as e:
        print(f"❌ Fehler beim Lesen des News-Markers: {repr(e)}", flush=True)
    return ""

def speichere_news_marker(wert):
    try:
        with open(MANFRED_NEWS_MARKER, "w", encoding="utf-8") as f:
            f.write(str(wert))
    except Exception as e:
        print(f"❌ Fehler beim Speichern des News-Markers: {repr(e)}", flush=True)

def ermittle_siegesserien():
    serien = {}
    try:
        rows = sheet.get_all_values()
        for row in rows:
            if len(row) < 7:
                continue
            p1 = row[0].strip()
            p2 = row[1].strip()
            if not p1 or not p2 or p1.lower() == "spieler a":
                continue
            if p1 not in serien:
                serien[p1] = 0
            if p2 not in serien:
                serien[p2] = 0

        for row in rows:
            if len(row) < 7:
                continue
            p1 = row[0].strip()
            p2 = row[1].strip()
            winner = row[6].strip()
            if not p1 or not p2 or p1.lower() == "spieler a" or not winner:
                continue

            if winner.lower() == "unentschieden":
                serien[p1] = 0
                serien[p2] = 0
                continue

            if normalize(winner) == normalize(p1):
                serien[p1] = serien.get(p1, 0) + 1
                serien[p2] = 0
            elif normalize(winner) == normalize(p2):
                serien[p2] = serien.get(p2, 0) + 1
                serien[p1] = 0
        return serien
    except Exception as e:
        print(f"❌ Fehler bei Siegesserien: {repr(e)}", flush=True)
        return {}

def ermittle_manfred_news():
    try:
        tabelle = get_tabelle()
        if not tabelle:
            return None, None
        serien = ermittle_siegesserien()
        leader = tabelle[0]
        leader_name = leader["name"]
        leader_punkte = leader["punkte"]

        news_id = "|".join(
            f"{spieler['name']}:{spieler['spiele']}:{spieler['siege']}:{spieler['niederlagen']}:{spieler['legs_plus']}:{spieler['legs_minus']}:{spieler['leg_dif']}:{spieler['punkte']}"
            for spieler in tabelle
        )
        letzter_marker = lese_news_marker()

        if news_id == letzter_marker:
            return None, None

        bester_spieler = None
        beste_serie = 0
        for spieler, serie in serien.items():
            if serie > beste_serie:
                beste_serie = serie
                bester_spieler = spieler

        if beste_serie >= 10:
            text = f"🔥 **MANFRED NEWS**\n\n🏆 **{bester_spieler}** ist nicht zu stoppen!\n\n🔥 **{beste_serie} Siege in Folge!**\n\n🤖 **Manfred sagt:**\n\"STOPPT DEN MANN! 🔥🔥🔥\""
        elif beste_serie >= 5:
            text = f"🔥 **MANFRED NEWS**\n\n🏆 **{bester_spieler}** hat eine starke Serie!\n\n🔥 **{beste_serie} Siege in Folge!**\n\n🤖 **Manfred sagt:**\n\"Da läuft aber eine Serie! 🎯🔥\""
        elif beste_serie >= 3:
            text = f"🔥 **MANFRED NEWS**\n\n🏆 **{bester_spieler}** ist aktuell gut unterwegs!\n\n🔥 **{beste_serie} Siege in Folge!**\n\n🤖 **Manfred sagt:**\n\"Drei am Stück! Weiter so! 🎯\""
        elif leader_punkte >= 100:
            text = f"💯 **MANFRED NEWS**\n\n🏆 **{leader_name}** hat die 100-Punkte-Marke geknackt!\n\n🎯 Punkte: **{leader_punkte}**\n🎮 Spiele: **{leader['spiele']}**\n🏆 Siege: **{leader['siege']}**\n❌ Niederlagen: **{leader['niederlagen']}**\n📈 Leg-Differenz: **{leader['leg_dif']:+d}**\n\n🤖 **Manfred sagt:**\n\"Das sieht nach Meisterschaft aus! 🎯\""
        else:
            text = f"📰 **MANFRED NEWS**\n\n🏆 **{leader_name}** führt aktuell die Rangliste an!\n\n🎯 Punkte: **{leader_punkte}**\n🎮 Spiele: **{leader['spiele']}**\n🏆 Siege: **{leader['siege']}**\n❌ Niederlagen: **{leader['niederlagen']}**\n📈 Leg-Differenz: **{leader['leg_dif']:+d}**\n\n🤖 **Manfred:**\n\"Die Tabelle bewegt sich! 👀🎯\""

        return text, news_id
    except Exception as e:
        print(f"❌ Fehler bei Manfred News: {repr(e)}", flush=True)
        return None, None

async def sende_manfred_news():
    try:
        channel = discord.utils.get(client.get_all_channels(), name=MANFRED_NEWS_CHANNEL)
        if channel is None:
            return False
        text, news_id = ermittle_manfred_news()
        if not text or not news_id:
            return False
        await channel.send(text)
        speichere_news_marker(news_id)
        return True
    except Exception as e:
        print(f"❌ Fehler beim Senden der Manfred News: {repr(e)}", flush=True)
        return False

async def manfred_news_scheduler():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            await sende_manfred_news()
        except Exception as e:
            print(f"❌ Manfred-News-Scheduler Fehler: {repr(e)}", flush=True)
        await asyncio.sleep(1800)

# ==========================================
# AUTOMATISCHER MONATSRESET
# ==========================================
VIENNA_TZ = ZoneInfo("Europe/Vienna")
MONTHLY_RESET_MARKER = "last_monthly_reset.txt"
MONTHLY_STATS_FILE = "Manfred_Monatsstatistik.json"

def speichere_monatsstatistik(monat_key, monat_name, gesamtspiele, gesamt_legs, ranking):
    try:
        daten = {}
        if os.path.exists(MONTHLY_STATS_FILE):
            with open(MONTHLY_STATS_FILE, "r", encoding="utf-8") as f:
                try:
                    daten = json.load(f)
                except Exception:
                    daten = {}

        top3 = []
        for spieler in ranking[:3]:
            top3.append({
                "name": spieler["name"],
                "spiele": spieler["spiele"],
                "siege": spieler["siege"],
                "niederlagen": spieler["niederlagen"],
                "legs_plus": spieler["legs_plus"],
                "legs_minus": spieler["legs_minus"],
                "leg_dif": spieler["leg_dif"],
                "punkte": spieler["punkte"]
            })

        daten[monat_key] = {
            "monat": monat_name,
            "gesamtspiele": gesamtspiele,
            "gesamt_legs": gesamt_legs,
            "top3": top3
        }

        with open(MONTHLY_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ Fehler beim Speichern der Monatsstatistik: {repr(e)}", flush=True)

async def sende_manfred_monatsabschluss(monat_name, ranking, gesamtspiele, gesamt_legs):
    try:
        channel = discord.utils.get(client.get_all_channels(), name="manfred-news")
        if channel is None:
            return False

        if not ranking:
            text = f"📰 **MANFRED MONATSABSCHLUSS – {monat_name.upper()}**\n\nℹ️ Keine qualifizierten Spieler für diesen Monat.\n\n🎮 Gesamtspiele: **{gesamtspiele}**\n🎯 Gesamtlegs: **{gesamt_legs}**"
            await channel.send(text)
            return True

        sieger = ranking[0]
        text = f"📰 **MANFRED MONATSABSCHLUSS – {monat_name.upper()}**\n━━━━━━━━━━━━━━━━━━━━\n\n🎮 **Gesamtspiele:** **{gesamtspiele}**\n🎯 **Gesamtlegs:** **{gesamt_legs}**\n\n👑 **Monatssieger:** **{sieger['name']}**\n⭐ **{sieger['punkte']} Punkte**\n🏆 {sieger['siege']} Siege | ❌ {sieger['niederlagen']} Niederlagen\n📈 Leg-Differenz: **{sieger['leg_dif']:+d}**\n\n🥇🥈🥉 **TOP 3 DES MONATS**\n\n"

        emojis = ["🥇", "🥈", "🥉"]
        for i, spieler in enumerate(ranking[:3]):
            text += f"{emojis[i]} **{spieler['name']}** – ⭐ {spieler['punkte']} Punkte | 🎮 {spieler['spiele']} Spiele | 🏆 {spieler['siege']} Siege\n"

        text += "\n🤖 **Manfred sagt:**\n\"Was für ein Monat! 🎯🔥 Glückwunsch an den Monatssieger!\""
        await channel.send(text)
        return True
    except Exception as e:
        print(f"❌ Fehler beim Manfred Monatsabschluss: {repr(e)}", flush=True)
        return False

async def monatlicher_reset(force=False):
    try:
        jetzt = datetime.now(VIENNA_TZ)
        if not force and jetzt.day != 1:
            return False

        if jetzt.month == 1:
            vorheriger_monat = 12
            vorheriges_jahr = jetzt.year - 1
        else:
            vorheriger_monat = jetzt.month - 1
            vorheriges_jahr = jetzt.year

        monate = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        monat_name = f"{monate[vorheriger_monat - 1]} {vorheriges_jahr}"
        monat_key = f"{vorheriges_jahr:04d}-{vorheriger_monat:02d}"

        if not force and os.path.exists(MONTHLY_RESET_MARKER):
            with open(MONTHLY_RESET_MARKER, "r", encoding="utf-8") as f:
                letzter_reset = f.read().strip()
            if letzter_reset == monat_key:
                return False

        all_rows = sheet.get_all_values()
        if len(all_rows) <= 1:
            return False

        gesamtspiele = len(all_rows) - 1
        gesamt_legs = 0
        for row in all_rows[1:]:
            if len(row) < 4:
                continue
            try:
                gesamt_legs += int(row[2]) + int(row[3])
            except Exception:
                continue

        ranking = get_tabelle()
        ranking = [s for s in ranking if s["spiele"] >= 3]

        await sende_manfred_monatsabschluss(monat_name, ranking, gesamtspiele, gesamt_legs)
        speichere_monatsstatistik(monat_key, monat_name, gesamtspiele, gesamt_legs, ranking)

        if ranking:
            try:
                channel = discord.utils.get(client.get_all_channels(), name="bullseye-rangliste")
                if channel:
                    emojis = ["🥇", "🥈", "🥉"]
                    msg = f"🏆 **MONATSSIEGER – {monat_name.upper()}**\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    for i, spieler in enumerate(ranking[:3]):
                        msg += f"{emojis[i]} **{spieler['name']}**\n🎮 {spieler['spiele']} Spiele | 🏆 {spieler['siege']} Siege | 💀 {spieler['niederlagen']} Niederlagen\n🎯 Legs +{spieler['legs_plus']} | 📉 Legs -{spieler['legs_minus']} | 📊 Diff {spieler['leg_dif']}\n⭐ **{spieler['punkte']} Punkte**\n\n"
                    sieger = ranking[0]
                    msg += f"👑 **Monatssieger: {sieger['name']}!**\n⭐ **{sieger['punkte']} Punkte**\n🎯 Herzlichen Glückwunsch! 🔥"
                    await channel.send(msg)
            except Exception as e:
                print(f"❌ Fehler beim Monatssieger-Post: {repr(e)}", flush=True)

        wb = gs_client.open_by_key("19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc")
        archiv_name = f"TEST_Archiv_{jetzt.strftime('%Y-%m-%d_%H-%M-%S')}" if force else f"Archiv_{monat_key}"

        try:
            archiv_sheet = wb.worksheet(archiv_name)
        except Exception:
            archiv_sheet = wb.add_worksheet(title=archiv_name, rows=1000, cols=20)

        archiv_sheet.clear()
        archiv_sheet.update("A1", all_rows)

        if force:
            return True

        header = all_rows[0]
        sheet.clear()
        sheet.update("A1", [header])

        with open(MONTHLY_RESET_MARKER, "w", encoding="utf-8") as f:
            f.write(monat_key)

        return True
    except Exception as e:
        print(f"❌ MONATSRESET ERROR: {repr(e)}", flush=True)
        return False

async def monatlicher_reset_scheduler():
    await client.wait_until_ready()
    while not client.is_closed():
        jetzt = datetime.now(VIENNA_TZ)
        if jetzt.day == 1:
            await monatlicher_reset()
            await asyncio.sleep(70)
        else:
            await asyncio.sleep(60)

@tree.command(name="test_monatsreset", description="Führt den Monatsreset testweise sofort aus", guild=GUILD_OBJECT)
async def test_monatsreset(interaction: discord.Interaction):
    if not any(role.id in ADMIN_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        erfolg = await monatlicher_reset(force=True)
        if erfolg:
            await interaction.followup.send("🧪 **Monatsreset erfolgreich getestet!**", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Der Monatsreset wurde nicht ausgeführt.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ **Fehler:**\n```{e}```", ephemeral=True)

async def geburtstag_checker():
    await client.wait_until_ready()
    while not client.is_closed():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        next_check = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if next_check <= now:
            next_check += timedelta(days=1)
        await asyncio.sleep((next_check - now).total_seconds())

        try:
            wb = gs_client.open_by_key("19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc")
            try:
                gb_sheet = wb.worksheet("Geburtstage")
            except:
                continue

            today = datetime.now()
            rows = gb_sheet.get_all_values()
            channel = await client.fetch_channel(GEBURTSTAGE_CHANNEL_ID)

            for row in rows[1:]:
                if len(row) < 3:
                    continue
                try:
                    name = row[0]
                    tag = int(row[1])
                    monat = int(row[2])
                    if tag == today.day and monat == today.month:
                        sprueche = [
                            f"🎂 Alles Gute zum Geburtstag, **{name}**! Möge deine Trefferquote heute so hoch sein wie deine Laune! 🎯🎉",
                            f"🎉 Happy Birthday, **{name}**! Ein weiteres Jahr älter, aber hoffentlich nicht schlechter am Board! 🎂",
                            f"🎂 **{name}** hat heute Geburtstag! Wir wünschen dir alles Gute und viele Bullseyes! 🎯",
                            f"🥳 Herzlichen Glückwunsch, **{name}**! Heute darfst du verlieren ohne Ausrede! 😂🎂",
                        ]
                        await channel.send(random.choice(sprueche))
                except:
                    continue
        except Exception as e:
            print("❌ GEBURTSTAG CHECKER ERROR:", e)

        await asyncio.sleep(60)

async def midnight_auswertung():
    await client.wait_until_ready()
    WIEN = ZoneInfo("Europe/Vienna")
    while not client.is_closed():
        try:
            jetzt = datetime.now(WIEN)
            naechster_lauf = jetzt.replace(hour=0, minute=5, second=0, microsecond=0)
            if naechster_lauf <= jetzt:
                naechster_lauf += timedelta(days=1)
            wartezeit = (naechster_lauf - jetzt).total_seconds()
            await asyncio.sleep(wartezeit)
            stats_channel = await client.fetch_channel(STATS_CHANNEL_ID)

            if not today_matches:
                await stats_channel.send("📊 **Tagesauswertung:** Heute wurden keine Spiele gespielt.")
            else:
                player_wins = defaultdict(int)
                player_games = defaultdict(int)
                for m in today_matches:
                    if m["winner"] and m["winner"].lower() != "unentschieden":
                        player_wins[m["winner"]] += 1
                    player_games[m["p1"]] += 1
                    player_games[m["p2"]] += 1
                most_games_player = max(player_games, key=player_games.get)
                most_wins_player = max(player_wins, key=player_wins.get) if player_wins else None
                msg = f"🌙 **Tagesauswertung {jetzt.strftime('%d.%m.%Y')}**\n\n🎮 Gespielte Matches heute: **{len(today_matches)}**\n🏅 Meiste Spiele: **{most_games_player}** ({player_games[most_games_player]} Spiele)\n"
                if most_wins_player:
                    msg += f"🏆 Meiste Siege: **{most_wins_player}** ({player_wins[most_wins_player]} Siege)\n"
                await stats_channel.send(msg)

            today_matches.clear()
            match_count.clear()
        except Exception as e:
            print(f"❌ MIDNIGHT ERROR: {repr(e)}", flush=True)
        await asyncio.sleep(60)

# =========================
# BOT READY
# =========================
_tasks_started = False

@client.event
async def on_ready():
    global _tasks_started
    if _tasks_started:
        return
    _tasks_started = True

    try:
        tree.copy_global_to(guild=GUILD_OBJECT)
        await tree.sync(guild=GUILD_OBJECT)
    except Exception as e:
        print(f"❌ Slash-Sync Fehler: {repr(e)}", flush=True)

    client.loop.create_task(midnight_auswertung())
    client.loop.create_task(tabelle_scheduler())
    client.loop.create_task(geburtstag_checker())
    asyncio.create_task(monatlicher_reset_scheduler())
    asyncio.create_task(manfred_news_scheduler())

    try:
        today_str = datetime.now().strftime("%d.%m.%Y")
        rows = sheet.get_all_values()
        for row in rows:
            if len(row) < 8:
                continue
            p1 = row[0].strip()
            p2 = row[1].strip()
            datum = row[7].strip()
            if not p1 or p1.lower() == "spieler a":
                continue
            if datum == today_str:
                match_count[normalize(p1)] += 1
                match_count[normalize(p2)] += 1
    except Exception as e:
        print(f"❌ Counter-Restore Fehler: {e}")

# =========================
# MESSAGE HANDLER
# =========================
@client.event
async def on_message(message):
    if message.author.bot:
        return

    is_stats_channel = message.channel.id == STATS_CHANNEL_ID
    is_main_channel = message.channel.name == CHANNEL_NAME
    is_spielabsprachen = message.channel.id == LOG_CHANNEL_ID
    is_spieler_info = message.channel.id == SPIELER_INFO_CHANNEL_ID
    is_abwesenheit = message.channel.id == ABWESENHEIT_CHANNEL_ID
    is_geburtstage = message.channel.id == GEBURTSTAGE_CHANNEL_ID
   
    if not (is_main_channel or is_stats_channel or is_spielabsprachen or is_spieler_info or is_abwesenheit or is_geburtstage):
        return

    reset_daily()
    content = message.content

    # =========================
    # !stats Spieler
    # =========================
    if content.lower().startswith("!stats"):
        if not is_stats_channel:
            return

        parts = content.split(None, 1)
        if message.mentions:
            player = message.mentions[0].display_name
        elif len(parts) > 1:
            player = parts[1].strip()
        else:
            await message.channel.send("❌ Nutzung: !stats Spielername")
            return

        try:
            stats = get_stats_from_sheet()
            s = stats.get(player)
            if not s:
                for k, v in stats.items():
                    if normalize(k) == normalize(player):
                        s = v
                        player = k
                        break

            if not s or s["spiele"] == 0:
                await message.channel.send(f"❌ Keine Daten für {player} gefunden.")
                return

            winrate = round(s["siege"] / s["spiele"] * 100, 1)
            msg = (
                f"📊 **Stats für {player}**\n"
                f"🎮 Spiele gesamt: {s['spiele']}\n"
                f"🏆 Siege: {s['siege']}\n"
                f"💀 Niederlagen: {s['niederlagen']}\n"
                f"📈 Win-Rate: {winrate}%"
            )
            await message.channel.send(msg)
        except Exception as e:
            await message.channel.send("❌ Fehler beim Laden der Stats.")
        return

    if content.lower().startswith("!los"):
        if not is_stats_channel:
            return
        los_sprueche = [
            "Irgendwer hier wirft Pfeile als ob er blind verbunden ist... und trotzdem besser als Lanzi_90 🎯",
            "Diese Runde hat mehr Fehlwuerfe als ein blinder Oktopus mit Parkinson 🐙",
            "Manche hier spielen Dart — andere werfen einfach Pfeile in die Gegend und hoffen 🙏",
            "Der Durchschnitt in dieser Liga ist so niedrig, er braucht einen Aufzug nach oben 📉",
            "Irgendwer hier sollte mal ernsthaft ueberlegen ob Darts das richtige Hobby ist... ihr wisst wer gemeint ist 😏",
            "👑 Stille bitte. Red_Apple17 betritt den Raum. Alle anderen duerfen weiterspielen... wenn ihr euch traut.",
            "🏆 Red_Apple17 — der einzige Grund warum diese Liga einen Sinn ergibt. Der Rest ist Dekoration.",
            "Ich wollte gerade jemanden beleidigen... aber ehrlich gesagt seid ihr alle ganz okay. Ausser Lanzi_90. 😇",
        ]
        await message.channel.send(random.choice(los_sprueche))
        return

    if content.lower().startswith("!gesamt"):
        if not is_stats_channel:
            return
        try:
            rows = sheet.get_all_values()
            spiele = [r for r in rows if len(r) >= 2 and r[0].lower() != "spieler a" and r[0].strip()]
            total = len(spiele)
            await message.channel.send(f"🎯 Bisher wurden insgesamt **{total} Spiele** gespielt!")
        except Exception as e:
            await message.channel.send(f"❌ Fehler: `{e}`")
        return

    if content.lower().startswith("!rivalitaeten") or content.lower().startswith("!rivalitäten"):
        if not is_stats_channel:
            return

        if message.mentions:
            spieler = message.mentions[0].display_name
        else:
            parts = content.split(None, 1)
            if len(parts) > 1 and not parts[1].startswith("@"):
                spieler = parts[1].strip()
            else:
                spieler = message.author.display_name

        try:
            rows = sheet.get_all_values()
            gegner_count = defaultdict(int)

            for row in rows:
                if len(row) < 7:
                    continue
                p1 = row[0].strip()
                p2 = row[1].strip()
                if p1.lower() == "spieler a":
                    continue

                if normalize(p1) == normalize(spieler):
                    gegner_count[p2] += 1
                elif normalize(p2) == normalize(spieler):
                    gegner_count[p1] += 1

            if not gegner_count:
                await message.channel.send(f"❌ Keine Spiele fuer **{spieler}** gefunden.")
                return

            top5 = sorted(gegner_count.items(), key=lambda x: x[1], reverse=True)[:5]
            msg = f"⚔️ **Top 5 Rivalitaeten von {spieler}:**\n\n"
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, (gegner, count) in enumerate(top5):
                msg += f"{medals[i]} **{gegner}** - {count} Duelle\n"

            await message.channel.send(msg)
        except Exception as e:
            await message.channel.send(f"❌ Fehler: `{e}`")
        return

    if content.lower().startswith("!h2h"):
        if not (is_stats_channel or is_spieler_info):
            return

        parts = content.split()
        if len(message.mentions) >= 2:
            p1 = message.mentions[0].display_name
            p2 = message.mentions[1].display_name
        elif len(message.mentions) == 1:
            p1 = message.author.display_name
            p2 = message.mentions[0].display_name
        elif len(parts) >= 3:
            p1 = parts[1]
            p2 = parts[2]
        else:
            await message.channel.send("❌ Nutzung: `!h2h @Spieler1 @Spieler2`")
            return

        try:
            rows = get_all_match_rows()
            p1_wins = 0
            p2_wins = 0
            p1_legs = 0
            p2_legs = 0
            total = 0
            last_game = None

            for row in rows:
                if len(row) < 7:
                    continue
                ra = row[0].strip()
                rb = row[1].strip()
                winner = row[6].strip()
                if ra.lower() == "spieler a":
                    continue

                if (normalize(ra) == normalize(p1) and normalize(rb) == normalize(p2)) or (normalize(ra) == normalize(p2) and normalize(rb) == normalize(p1)):
                    total += 1
                    try:
                        legs_a = int(row[2])
                        legs_b = int(row[3])
                    except:
                        continue

                    if normalize(ra) == normalize(p1):
                        p1_legs += legs_a
                        p2_legs += legs_b
                    else:
                        p1_legs += legs_b
                        p2_legs += legs_a

                    if normalize(winner) == normalize(p1):
                        p1_wins += 1
                    elif normalize(winner) == normalize(p2):
                        p2_wins += 1

                    last_game = row

            if total == 0:
                await message.channel.send(f"❌ Keine Spiele zwischen **{p1}** und **{p2}** gefunden.")
                return

            p1_wr = round(p1_wins / total * 100, 1) if total > 0 else 0
            p2_wr = round(p2_wins / total * 100, 1) if total > 0 else 0

            leader = f"👑 {p1}" if p1_wins > p2_wins else (f"👑 {p2}" if p2_wins > p1_wins else "🤝 Gleichstand")

            msg = f"⚔️ **Head-to-Head**\n\n🎯 {p1} vs {p2}\n\n🎮 Duelle gesamt: **{total}**\n\n🏆 **{p1}**\nSiege: {p1_wins} | Winrate: {p1_wr}% | Legs: {p1_legs}\n\n🏆 **{p2}**\nSiege: {p2_wins} | Winrate: {p2_wr}% | Legs: {p2_legs}\n\n{leader}"
            if last_game:
                msg += f"\n\n📌 Letztes Duell:\n{last_game[0]} vs {last_game[1]} {last_game[2]}:{last_game[3]}"

            await message.channel.send(msg)
        except Exception as e:
            await message.channel.send(f"❌ Fehler beim Laden der H2H-Daten.")
        return

    if content.lower().startswith("!tabelle"):
        if not is_stats_channel:
            return
        try:
            tabelle = get_tabelle()
            if not tabelle:
                await message.channel.send("❌ Keine Daten gefunden.")
                return
            title = f"Aktuelle Tabelle  {datetime.now().strftime('%d.%m.%Y %H:%M')} Uhr"
            img_buf = generate_tabelle_image(tabelle, title)
            await message.channel.send(file=discord.File(img_buf, filename="tabelle.png"))
        except Exception as e:
            await message.channel.send(f"❌ Fehler beim Laden der Tabelle.")
        return

    if content.lower().startswith("!top") or content.lower().startswith("!rangliste"):
        if not is_stats_channel:
            return
        try:
            stats = get_stats_from_sheet()
            if not stats:
                await message.channel.send("❌ Keine Daten gefunden.")
                return

            sorted_players = sorted(stats.items(), key=lambda x: (x[1]["siege"], -x[1]["niederlagen"]), reverse=True)[:10]
            msg = "🏆 **Top 10 Rangliste**\n\n"
            medals = ["🥇", "🥈", "🥉"]
            for i, (player, s) in enumerate(sorted_players):
                medal = medals[i] if i < 3 else f"{i+1}."
                winrate = round(s["siege"] / s["spiele"] * 100, 1) if s["spiele"] > 0 else 0
                msg += f"{medal} {player} — {s['siege']}S / {s['niederlagen']}N ({winrate}%)\n"

            await message.channel.send(msg)
        except Exception as e:
            await message.channel.send("❌ Fehler beim Laden der Rangliste.")
        return

    if content.lower().startswith("!streak"):
        if not is_stats_channel:
            return
        parts = content.split(None, 1)
        if message.mentions:
            player = message.mentions[0].display_name
        elif len(parts) > 1:
            player = parts[1].strip()
        else:
            await message.channel.send("❌ Nutzung: !streak Spielername")
            return

        try:
            streak = get_streak_from_sheet(player)
            if streak == 0:
                await message.channel.send(f"😬 {player} hat gerade keine Siegesserie.")
            elif streak == 1:
                await message.channel.send(f"🔥 {player} hat 1 Sieg in Folge!")
            else:
                await message.channel.send(f"🔥 {player} ist auf einer {streak}er Siegesserie!")
        except Exception as e:
            await message.channel.send("❌ Fehler beim Laden der Streak-Daten.")
        return

    if content.lower().startswith("!undo"):
        if not is_main_channel:
            return
        if not is_admin(message.author):
            await message.channel.send("⛔ Nur Admins dürfen das.")
            return
        try:
            all_rows = sheet.get_all_values()
            if not all_rows:
                await message.channel.send("❌ Keine Einträge zum Löschen.")
                return
            sheet.delete_rows(len(all_rows))
            await message.channel.send("🗑️ Letzter Eintrag wurde gelöscht!")
        except Exception as e:
            await message.channel.send("❌ Fehler beim Löschen.")
        return

    if content.lower().startswith("!rename"):
        if not is_admin(message.author):
            await message.channel.send("⛔ Nur Admins duerfen das.")
            return

        parts = content.split()
        if len(parts) < 3:
            await message.channel.send("❌ Nutzung: `!rename AlterName NeuerName`")
            return

        alter_name = parts[1].strip()
        neuer_name = parts[2].strip()

        try:
            rows = sheet.get_all_values()
            count = 0
            safe_columns = [0, 1, 4, 5]
            col_letters = {0: "A", 1: "B", 4: "E", 5: "F"}

            for i, row in enumerate(rows):
                updated = False
                for j in safe_columns:
                    if j < len(row) and normalize(row[j]) == normalize(alter_name):
                        cell_ref = f"{col_letters[j]}{i+1}"
                        sheet.update(cell_ref, [[neuer_name]])
                        updated = True
                if updated:
                    count += 1

            if count == 0:
                await message.channel.send(f"❌ Kein Spieler mit Namen `{alter_name}` gefunden.")
            else:
                await message.channel.send(f"✅ `{alter_name}` wurde in `{neuer_name}` umbenannt. ({count} Zeilen)")
        except Exception as e:
            await message.channel.send(f"❌ Fehler beim Umbenennen.")
        return

    if content.lower().startswith("!delete"):
        if not is_admin(message.author):
            await message.channel.send("⛔ Nur Admins duerfen das.")
            return

        if message.mentions:
            spieler = message.mentions[0].display_name
        else:
            parts = content.split(None, 1)
            if len(parts) < 2:
                await message.channel.send("❌ Nutzung: `!delete @Spieler`")
                return
            spieler = parts[1].strip()

        if "confirm" not in content.lower():
            await message.channel.send(f"⚠️ Alle Eintraege von **{spieler}** werden geloescht!\nZum Bestaetigen: `!delete {spieler} confirm`")
            return

        spieler = spieler.replace(" confirm", "").replace("confirm", "").strip()

        try:
            rows = sheet.get_all_values()
            to_delete = []

            for i, row in enumerate(rows):
                if len(row) < 2:
                    continue
                if normalize(row[0]) == normalize(spieler) or normalize(row[1]) == normalize(spieler):
                    to_delete.append(i + 1)

            if not to_delete:
                await message.channel.send(f"❌ Keine Eintraege fuer `{spieler}` gefunden.")
                return

            for row_idx in reversed(to_delete):
                sheet.delete_rows(row_idx)

            await message.channel.send(f"🗑️ **{spieler}** wurde geloescht ({len(to_delete)} Eintraege).")
        except Exception as e:
            await message.channel.send(f"❌ Fehler beim Loeschen.")
        return

    if content.lower().startswith("!saisonreset"):
        if not is_admin(message.author):
            await message.channel.send("⛔ Nur Admins duerfen das.")
            return

        if "confirm" not in content.lower():
            await message.channel.send("⚠️ **Saisonreset!** Zum Bestaetigen: `!saisonreset confirm`")
            return

        try:
            all_rows = sheet.get_all_values()
            if len(all_rows) <= 1:
                await message.channel.send("❌ Keine Daten zum Archivieren.")
                return

            archiv_name = f"Archiv_{datetime.now().strftime('%B_%Y')}"
            wb = gs_client.open_by_key("19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc")

            try:
                archiv_sheet = wb.worksheet(archiv_name)
            except:
                archiv_sheet = wb.add_worksheet(title=archiv_name, rows=1000, cols=20)

            archiv_sheet.clear()
            archiv_sheet.update("A1", all_rows)

            header = all_rows[0]
            sheet.clear()
            sheet.update("A1", [header])

            await message.channel.send(f"✅ **Saisonreset abgeschlossen!** Archiviert als `{archiv_name}`.")
        except Exception as e:
            await message.channel.send(f"❌ Fehler beim Saisonreset.")
        return
        
    if content.lower().startswith("!hilfe"):
        if not is_stats_channel and not is_spieler_info:
            return

        if is_stats_channel:
            hilfe_admin = """🎯 MANFRED - ALLE KOMMANDOS

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 STATISTIK (#statistik-fuer-admin)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
!stats @Spieler → Stats eines Spielers
!top / !rangliste → Top 10 Rangliste
!streak @Spieler → Aktuelle Siegesserie
!h2h Spieler1 Spieler2 → Direktvergleich
!tabelle → Tabelle als Bild
!rivalitaeten → Deine Top 5 Gegner
!gesamt → Gesamtanzahl Spiele
!los → 😈

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 ADMIN (#bullseye-rangliste-ergebnisse)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
!add @Spieler +1/-1 → Tageslimit anpassen
!undo → Letzten Eintrag löschen
/test-monatsreset → Monatsreset testen
!rename / !delete → Spielerverwaltung

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔄 SAISON
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
!saisonreset confirm → Saison archivieren & leeren
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
            await message.channel.send(hilfe_admin)
            return

        if is_spieler_info:
            hilfe_text = """🎯 MANFRED – EUER DART-BOT 🎯

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 ERGEBNIS EINTRAGEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Schreibt einfach so:
@Spieler1 vs @Spieler2 3:1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 MEINE KOMMANDOS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
!ich → Eigene Stats
!ziel → Nächste Meilensteine & Rang
!nächster → Freie Gegner heute
!quote → Motivationsspruch 💪
!h2h @Spieler → Direktvergleich ⚔️"""
            await message.channel.send(hilfe_text)
            return

    if content.lower().startswith("!quote"):
        if message.channel.id != SPIELER_INFO_CHANNEL_ID:
            return
        quotes = [
            "🎯 Ein schlechter Tag am Dartboard ist besser als ein guter Tag ohne Dart!",
            "🎯 Uebung macht den Meister — wirf einfach weiter!",
            "🎯 Jeder Profi war mal ein Anfaenger. Heute koennte dein Tag sein!",
            "🎯 Dart ist 10% Talent und 90% nicht aufhoeren zu ueben!",
        ]
        await message.channel.send(random.choice(quotes))
        return

    if content.lower().startswith("!ich"):
        if message.channel.id != SPIELER_INFO_CHANNEL_ID:
            return
        spieler = message.author.display_name
        try:
            stats = get_stats_from_sheet()
            s = None
            for k, v in stats.items():
                if normalize(k) == normalize(spieler):
                    s = v
                    break
            if not s or s["spiele"] == 0:
                await message.channel.send(f"❌ Keine Daten fuer {spieler} gefunden.")
                return
            winrate = round(s["siege"] / s["spiele"] * 100, 1)
            await message.channel.send(f"📊 **Deine Stats, {spieler}**\n🎮 Spiele: {s['spiele']} | 🏆 Siege: {s['siege']} | 💀 Niederlagen: {s['niederlagen']} | 📈 Win-Rate: {winrate}%")
        except Exception as e:
            await message.channel.send(f"❌ Fehler: `{e}`")
        return

    if content.lower().startswith("!ziel"):
        if message.channel.id != SPIELER_INFO_CHANNEL_ID:
            return
        spieler = message.author.display_name
        try:
            stats = get_stats_from_sheet()
            s = None
            for k, v in stats.items():
                if normalize(k) == normalize(spieler):
                    s = v
                    break
            if not s or s["spiele"] == 0:
                await message.channel.send(f"❌ Keine Daten fuer {spieler} gefunden.")
                return

            msg = f"🎯 **Naechste Ziele fuer {spieler}:**\n\n"
            naechstes_spiel_ziel = next((m for m in sorted(SPIELE_MEILENSTEINE.keys()) if s["spiele"] < m), None)
            if naechstes_spiel_ziel:
                msg += f"🎮 Spiele: noch **{naechstes_spiel_ziel - s['spiele']}** bis zum Meilenstein\n"
            naechstes_sieg_ziel = next((m for m in sorted(SIEGE_MEILENSTEINE.keys()) if s["siege"] < m), None)
            if naechstes_sieg_ziel:
                msg += f"🏆 Siege: noch **{naechstes_sieg_ziel - s['siege']}** bis zum Meilenstein\n"
            await message.channel.send(msg)
        except Exception as e:
            await message.channel.send(f"❌ Fehler: `{e}`")
        return

    if content.lower().startswith("!naechster") or content.lower().startswith("!nächster"):
        if message.channel.id != SPIELER_INFO_CHANNEL_ID:
            return
        try:
            verfuegbar = [f"• {player} ({MAX_MATCHES_PER_DAY - count} Spiele übrig)" for player, count in match_count.items() if count < MAX_MATCHES_PER_DAY and normalize(player) != normalize(message.author.display_name)]
            if not verfuegbar:
                await message.channel.send("😴 Heute hat niemand mehr Spiele übrig!")
            else:
                await message.channel.send("🎯 **Verfuegbare Gegner heute:**\n" + "\n".join(verfuegbar))
        except Exception as e:
             await message.channel.send(f"❌ Fehler: `{e}`")
        return

    if content.lower().startswith("!urlaub") and not content.lower().startswith("!urlaube"):
        if not is_abwesenheit:
            return
        spieler = message.author.display_name
        if "löschen" in content.lower() or "loeschen" in content.lower():
            try:
                wb = gs_client.open_by_key("19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc")
                urlaub_sheet = wb.worksheet("Urlaube")
                rows = urlaub_sheet.get_all_values()
                for i, row in enumerate(rows):
                    if len(row) >= 1 and normalize(row[0]) == normalize(spieler):
                        urlaub_sheet.delete_rows(i + 1)
                        await message.channel.send(f"✅ Urlaub von **{spieler}** wurde gelöscht.")
                        return
                await message.channel.send(f"❌ Kein Urlaub gefunden.")
            except Exception as e:
                await message.channel.send(f"❌ Fehler: `{e}`")
            return

        parts = content.split(None, 1)
        if len(parts) < 2:
            await message.channel.send("❌ Nutzung: `!urlaub 20.06 - 30.06`")
            return
        try:
            dates = re.findall(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?", parts[1].strip())
            if len(dates) < 2:
                await message.channel.send("❌ Nutzung: `!urlaub 20.06 - 30.06`")
                return
            year = datetime.now().year
            von = datetime(int(dates[0][2]) if dates[0][2] else year, int(dates[0][1]), int(dates[0][0]))
            bis = datetime(int(dates[1][2]) if dates[1][2] else year, int(dates[1][1]), int(dates[1][0]))

            wb = gs_client.open_by_key("19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc")
            try:
                urlaub_sheet = wb.worksheet("Urlaube")
            except:
                urlaub_sheet = wb.add_worksheet(title="Urlaube", rows=200, cols=5)
                urlaub_sheet.update("A1", [["Spieler", "Von", "Bis"]])

            urlaub_sheet.append_row([spieler, von.strftime("%d.%m.%Y"), bis.strftime("%d.%m.%Y")])
            await message.channel.send(f"✅ Urlaub eingetragen für **{spieler}**.")
        except Exception as e:
            await message.channel.send(f"❌ Fehler: `{e}`")
        return

    if content.lower().startswith("!urlaube"):
        if not is_abwesenheit:
            return
        try:
            wb = gs_client.open_by_key("19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc")
            urlaub_sheet = wb.worksheet("Urlaube")
            rows = urlaub_sheet.get_all_values()
            today = datetime.now().date()
            aktuell = []

            for row in rows[1:]:
                if len(row) < 3:
                    continue
                von = datetime.strptime(row[1], "%d.%m.%Y").date()
                bis = datetime.strptime(row[2], "%d.%m.%Y").date()
                if von <= today <= bis:
                    aktuell.append(f"• {row[0]}: {row[1]} - {row[2]}")

            msg = "📅 **Urlaubs-Uebersicht**\n\n" + ("\n".join(aktuell) if aktuell else "Niemand im Urlaub.")
            await message.channel.send(msg)
        except Exception as e:
            await message.channel.send(f"📅 Noch keine Urlaube eingetragen.")
        return

    if content.lower().startswith("!geburtstag"):
        if not is_geburtstage:
            return
        spieler = message.author.display_name
        parts = content.split(None, 1)
        if len(parts) < 2:
            await message.channel.send("❌ Nutzung: `!geburtstag 15.03`")
            return
        try:
            dates = re.findall(r"(\d{1,2})[.\-/](\d{1,2})", parts[1].strip())
            tag, monat = int(dates[0][0]), int(dates[0][1])

            wb = gs_client.open_by_key("19Ax_hj9exjwfM6NPyw9JBL2ad3qW1_LOkMHddJ6stlc")
            try:
                gb_sheet = wb.worksheet("Geburtstage")
            except:
                gb_sheet = wb.add_worksheet(title="Geburtstage", rows=200, cols=3)
                gb_sheet.update("A1", [["Spieler", "Tag", "Monat"]])

            gb_sheet.append_row([spieler, str(tag), str(monat)])
            await message.channel.send(f"✅ Geburtstag eingetragen: **{spieler}** am {tag:02d}.{monat:02d} 🎂")
        except Exception as e:
            await message.channel.send(f"❌ Fehler: `{e}`")
        return

    if not is_main_channel:
        return

    if content.lower().startswith("!add"):
        if not is_admin(message.author):
            await message.channel.send("⛔ Nur Admins dürfen das.")
            return

        parts = content.split()
        player = message.mentions[0].display_name if message.mentions else (parts[1] if len(parts) > 1 else None)
        if not player:
            await message.channel.send("❌ Nutzung: `!add Spieler +1`")
            return

        try:
            change = int(parts[2]) if len(parts) > 2 else 1
        except ValueError:
            await message.channel.send("❌ Die Änderung muss eine Zahl sein.")
            return

        match_count[normalize(player)] += change
        if match_count[normalize(player)] < 0:
            match_count[normalize(player)] = 0

        await message.channel.send(f"🔧 {player}: Änderung {change} | Restspiele: {remaining(player)}")
        return

    if len(message.mentions) < 2:
        await message.channel.send("⚠️ **Bitte Spieler mit @ markieren!**\nBeispiel: `@Spieler1 vs @Spieler2 3:2`")
        return

    result = resolve_names(message, content)
    if not result:
        await message.channel.send("❌ Format: Spieler A vs Spieler B 3:0")
        return

    p1, p2, s1, s2 = result
    s1, s2 = int(s1), int(s2)

    if normalize(p1) == normalize(p2):
        await message.channel.send("🤦 Niemand kann gegen sich selbst spielen.")
        return

    if s1 == 0 and s2 == 0 or s1 > 20 or s2 > 20:
        await message.channel.send("❌ Ungültiger Score.")
        return

    if s1 > s2:
        winner, loser, w_score, l_score = p1, p2, s1, s2
    elif s2 > s1:
        winner, loser, w_score, l_score = p2, p1, s2, s1
    else:
        winner, loser, w_score, l_score = "Unentschieden", "", s1, s2

    if winner != "Unentschieden":
        if match_count[normalize(winner)] >= MAX_MATCHES_PER_DAY or match_count[normalize(loser)] >= MAX_MATCHES_PER_DAY:
            await message.channel.send("⚠️ Ein Spieler hat keine Spiele mehr übrig.")
            return

    try:
        legs_a = w_score if normalize(p1) == normalize(winner) else (l_score if winner != "Unentschieden" else s1)
        legs_b = l_score if normalize(p2) == normalize(winner) else (w_score if winner != "Unentschieden" else s2)

        await asyncio.to_thread(sheet.append_row, [p1, p2, legs_a, legs_b, p2, p1, winner])
        last_row = await asyncio.to_thread(lambda: len(sheet.get_all_values()))
        await asyncio.to_thread(sheet.update_cell, last_row, 8, datetime.now().strftime("%d.%m.%Y"))
    except Exception as e:
        await message.channel.send("❌ Fehler beim Speichern!")
        return

    if winner != "Unentschieden":
        match_count[normalize(winner)] += 1
        match_count[normalize(loser)] += 1

    today_matches.append({"p1": p1, "p2": p2, "winner": winner})

    if winner == "Unentschieden":
        await message.channel.send(f"🤝 Unentschieden {w_score}:{l_score}")
    else:
        rest_winner = remaining(winner)
        rest_loser = remaining(loser)
        await message.channel.send(f"🏆 Sieger: {winner} ({w_score}:{l_score})\n🎮 {winner} noch {rest_winner} Spiele\n🎮 {loser} noch {rest_loser} Spiele")

    try:
        spielabsprachen = await client.fetch_channel(LOG_CHANNEL_ID)
        for player in [p1, p2]:
            await check_meilensteine(player, spielabsprachen)

        msg = f"📊 Match Update:\n{p1} vs {p2}\n\n🎮 Restspiele:\n- {p1}: {remaining(p1)}\n- {p2}: {remaining(p2)}\n"
        await spielabsprachen.send(msg)
    except Exception as e:
        print("❌ LOG ERROR:", e)

# =========================
# SLASH COMMANDS
# =========================
@tree.command(name="ich", description="Zeigt deine persoenlichen Stats")
async def slash_ich(interaction: discord.Interaction):
    spieler = interaction.user.display_name
    try:
        stats = get_stats_from_sheet()
        s = next((v for k, v in stats.items() if normalize(k) == normalize(spieler)), None)
        if not s or s["spiele"] == 0:
            await interaction.response.send_message(f"Keine Daten fuer {spieler} gefunden.", ephemeral=True)
            return
        winrate = round(s["siege"] / s["spiele"] * 100, 1)
        await interaction.response.send_message(f"📊 **Deine Stats, {spieler}**\n🎮 Spiele: {s['spiele']} | 🏆 Siege: {s['siege']} | 💀 Niederlagen: {s['niederlagen']} | 📈 Win-Rate: {winrate}%", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

@tree.command(name="ziel", description="Zeigt deinen naechsten Meilenstein und Rang")
async def slash_ziel(interaction: discord.Interaction):
    spieler = interaction.user.display_name
    try:
        stats = get_stats_from_sheet()
        s = next((v for k, v in stats.items() if normalize(k) == normalize(spieler)), None)
        if not s or s["spiele"] == 0:
            await interaction.response.send_message(f"Keine Daten fuer {spieler} gefunden.", ephemeral=True)
            return

        msg = f"🎯 **Naechste Ziele fuer {spieler}:**\n\n"
        naechstes_spiel_ziel = next((m for m in sorted(SPIELE_MEILENSTEINE.keys()) if s["spiele"] < m), None)
        if naechstes_spiel_ziel:
            msg += f"🎮 Spiele: noch **{naechstes_spiel_ziel - s['spiele']}** bis zum Meilenstein\n"
        naechstes_sieg_ziel = next((m for m in sorted(SIEGE_MEILENSTEINE.keys()) if s["siege"] < m), None)
        if naechstes_sieg_ziel:
            msg += f"🏆 Siege: noch **{naechstes_sieg_ziel - s['siege']}** bis zum Meilenstein\n"

        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

@tree.command(name="naechster", description="Zeigt wer heute noch Spiele uebrig hat")
async def slash_naechster(interaction: discord.Interaction):
    try:
        verfuegbar = [f"• {player} ({MAX_MATCHES_PER_DAY - count} Spiele uebrig)" for player, count in match_count.items() if count < MAX_MATCHES_PER_DAY and normalize(player) != normalize(interaction.user.display_name)]
        if not verfuegbar:
            await interaction.response.send_message("😴 Heute hat niemand mehr Spiele uebrig!", ephemeral=True)
        else:
            await interaction.response.send_message("🎯 **Verfuegbare Gegner heute:**\n" + "\n".join(verfuegbar), ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

@tree.command(name="quote", description="Zufaelliger Motivationsspruch")
async def slash_quote(interaction: discord.Interaction):
    quotes = [
        "🎯 Ein schlechter Tag am Dartboard ist besser als ein guter Tag ohne Dart!",
        "🎯 Uebung macht den Meister — wirf einfach weiter!",
        "🎯 Jeder Profi war mal ein Anfaenger. Heute koennte dein Tag sein!",
    ]
    await interaction.response.send_message(random.choice(quotes))

@tree.command(name="hilfe", description="Zeigt alle verfuegbaren Befehle")
async def slash_hilfe(interaction: discord.Interaction):
    hilfe_text = """🎯 MANFRED - EUER DART-BOT 🎯
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 ERGEBNIS EINTRAGEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Schreibt einfach so:
@Spieler1 vs @Spieler2 3:1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 BEFEHLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
/ich       → Deine persoenlichen Stats
/ziel      → Naechster Meilenstein & Rang
/naechster → Wer hat heute noch Spiele uebrig?
/quote     → Motivationsspruch 💪
/hilfe     → Diese Uebersicht"""
    await interaction.response.send_message(hilfe_text, ephemeral=True)

# =========================
# RUN
# =========================
client.run(TOKEN)
