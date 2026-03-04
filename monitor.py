"""מוניטור פיקוד העורף — סורק ערוץ טלגרם ושולח התראות מסוננות.

גישת whitelist: שולח התראה רק כשההודעה מכילה את *כל* התנאים —
שם העיר + ביטוי חיובי ("ניתן לצאת"). מונע false alarms מהתראות אזעקה.

שימוש:
    TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python monitor.py
"""
import asyncio
import os
import signal
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from database import init_db, is_seen, mark_seen, is_alert_sent, save_alert, cleanup_old
from logger import get_logger
from notifier import send_alert, send_message
from scraper import fetch_latest_messages

log = get_logger("Monitor")

# ── הגדרות ──

# מרווח סריקה — כל 45 שניות (ברירת מחדל)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "45"))

# אזור זמן
_TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Jerusalem"))

# ── כללי סינון (whitelist) ──
# ערים/אזורים לניטור — הודעה חייבת להכיל לפחות אחד מהם
# אפשר להגדיר דרך env: ALERT_CITIES="תל אביב,רמת גן,גבעתיים"
_cities_env = os.environ.get("ALERT_CITIES", "תל אביב")
ALERT_CITIES: list[str] = [c.strip() for c in _cities_env.split(",") if c.strip()]

# ביטויים חיוביים — הודעה חייבת להכיל לפחות אחד מהם בנוסף לעיר.
# מבוסס על ניסוחי פיקוד העורף בפועל.
_positives_env = os.environ.get("ALERT_POSITIVES", "")
POSITIVE_PHRASES: list[str] = (
    [p.strip() for p in _positives_env.split(",") if p.strip()]
    if _positives_env
    else [
        "ניתן לצאת מהמרחב המוגן",
        "ניתן לצאת ממרחב המוגן",
        "ניתן לצאת מהמקלט",
        "ניתן לעזוב את המרחב המוגן",
        "השוהים במרחב המוגן יכולים לצאת",
    ]
)

# ביטויים שליליים — אם ההודעה מכילה אחד מהם, לא נשלח (בטיחות נוספת).
# מונע מצב שביטוי חיובי מופיע בהקשר שלילי.
NEGATIVE_PHRASES: list[str] = [
    "אין לצאת",
    "להישאר במרחב המוגן",
    "להישאר במקלט",
]


def matches_filter(text: str) -> tuple[bool, str]:
    """בודק אם ההודעה עוברת את הפילטר — whitelist בלבד.

    מחזיר (True, סיבה) אם עוברת, (False, "") אם לא.

    לוגיקה:
      1. חייב להכיל שם עיר/אזור
      2. חייב להכיל ביטוי חיובי (ניתן לצאת...)
      3. לא יכול להכיל ביטוי שלילי (אין לצאת...)
    """
    text_lower = text.lower()

    # בדיקת עיר — חייב לפחות אחת
    matched_city = None
    for city in ALERT_CITIES:
        if city.lower() in text_lower:
            matched_city = city
            break
    if not matched_city:
        return False, ""

    # בדיקת ביטוי חיובי — whitelist
    matched_positive = None
    for phrase in POSITIVE_PHRASES:
        if phrase in text:
            matched_positive = phrase
            break
    if not matched_positive:
        return False, ""

    # בדיקת ביטוי שלילי — safety check
    for neg in NEGATIVE_PHRASES:
        if neg in text:
            log.warning(f"הודעה הכילה ביטוי חיובי + שלילי, לא נשלחת: {text[:80]}")
            return False, ""

    reason = f"עיר: {matched_city} | {matched_positive}"
    return True, reason


async def run_cycle():
    """מחזור סריקה בודד — fetch → filter → alert."""
    messages = await asyncio.to_thread(fetch_latest_messages)
    if not messages:
        return

    new_count = 0
    alert_count = 0

    for msg in messages:
        msg_id = msg["id"]

        if is_seen(msg_id):
            continue

        mark_seen(msg_id, "PikudHaOref_all")
        new_count += 1

        match, reason = matches_filter(msg["text"])
        if match:
            if is_alert_sent(msg_id):
                log.debug(f"הודעה {msg_id} כבר נשלחה")
                continue

            log.info(f"🔔 התראה! {reason} | msg_id={msg_id}")
            content = msg["text"]
            if msg["date"]:
                content += f"\n\n🕐 {msg['date']}"

            success = await asyncio.to_thread(send_alert, content)
            if success:
                save_alert(msg_id, "PikudHaOref_all", msg["text"])
                alert_count += 1

    if new_count:
        log.info(f"עובדו {new_count} הודעות חדשות, {alert_count} התראות נשלחו")


async def main():
    """לולאה ראשית — סריקה כל POLL_INTERVAL שניות."""
    init_db()

    log.info(f"מתחיל ניטור פיקוד העורף | poll={POLL_INTERVAL}s")
    log.info(f"ערים: {ALERT_CITIES}")
    log.info(f"ביטויים חיוביים: {POSITIVE_PHRASES}")

    # הודעת אתחול
    startup_msg = (
        "✅ מוניטור פיקוד העורף פעיל\n"
        f"ערים: {', '.join(ALERT_CITIES)}\n"
        f"סריקה כל {POLL_INTERVAL} שניות"
    )
    await asyncio.to_thread(send_message, startup_msg)

    # ניקוי ישן כל 6 שעות
    cleanup_counter = 0
    cleanup_every = (6 * 3600) // POLL_INTERVAL  # כל כמה סבבים לנקות

    while True:
        try:
            await run_cycle()
        except Exception as e:
            log.error(f"שגיאה במחזור סריקה: {e}")

        cleanup_counter += 1
        if cleanup_counter >= cleanup_every:
            cleanup_counter = 0
            try:
                await asyncio.to_thread(cleanup_old, 14)
            except Exception as e:
                log.error(f"שגיאה בניקוי DB: {e}")

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    # graceful shutdown
    def _handle_signal(sig, frame):
        log.info("shutting down...")
        sys.exit(0)

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(main())
