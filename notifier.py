"""שליחת התראות לטלגרם."""
import os

import requests

from logger import get_logger

log = get_logger("Notifier")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text: str, *, chat_id: str | None = None) -> bool:
    """שולח הודעת טקסט לטלגרם. מחזיר True בהצלחה."""
    target = chat_id or CHAT_ID
    if not BOT_TOKEN or not target:
        log.error("חסרים TELEGRAM_BOT_TOKEN או TELEGRAM_CHAT_ID")
        return False

    try:
        resp = requests.post(
            f"{_API}/sendMessage",
            json={
                "chat_id": target,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            log.info(f"הודעה נשלחה ל-{target}")
            return True
        log.error(f"טלגרם החזיר {resp.status_code} (chat_id={target!r}): {resp.text[:200]}")
        return False
    except Exception as e:
        log.error(f"שגיאה בשליחה לטלגרם: {e}")
        return False


def validate_chat() -> bool:
    """בודק שהבוט יכול לשלוח הודעות ל-CHAT_ID. מחזיר True אם תקין."""
    if not BOT_TOKEN:
        log.error("חסר TELEGRAM_BOT_TOKEN")
        return False
    if not CHAT_ID:
        log.error("חסר TELEGRAM_CHAT_ID")
        return False

    log.info(f"בודק חיבור לטלגרם (chat_id={CHAT_ID!r})")
    try:
        resp = requests.post(
            f"{_API}/getChat",
            json={"chat_id": CHAT_ID},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("result", {})
            name = data.get("first_name") or data.get("title") or "unknown"
            log.info(f"חיבור תקין — צ'אט: {name}")
            return True
        log.error(
            f"chat_id={CHAT_ID!r} לא נמצא בטלגרם ({resp.status_code}). "
            "שלח הודעה כלשהי לבוט ובדוק שה-CHAT_ID נכון."
        )
        return False
    except Exception as e:
        log.error(f"שגיאה בבדיקת חיבור לטלגרם: {e}")
        return False


def send_alert(content: str, *, chat_id: str | None = None) -> bool:
    """שולח התראת פיקוד העורף מפורמטת."""
    text = (
        "🔔 התראת פיקוד העורף\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{content}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    return send_message(text, chat_id=chat_id)
