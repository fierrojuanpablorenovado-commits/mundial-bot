"""
notify.py — Notificación del mundial-bot

Canales soportados:
  - 'stdout'    : solo loguea (default para testing)
  - 'callmebot' : WhatsApp via api.callmebot.com (activado por JP 29-may-2026)
  - 'telegram'  : cuando JP configure TOKEN+CHAT_ID

NOTA: CallMeBot reactivado por instrucción explícita de JP (29-may-2026).
  El bot manda con prefijo 🏆 MUNDIAL para diferenciarlo del tipster normal.
  Misma cuenta WhatsApp de JP — revisar que los mensajes sean distinguibles.
"""
from __future__ import annotations
import os
import re
import urllib.parse
import requests


NOTIFICATIONS_ENABLED = os.environ.get("NOTIFICATIONS_ENABLED", "false").lower() in ("1", "true", "yes")
CHANNEL = os.environ.get("CHANNEL", "stdout").lower()

ALLOWED_CHANNELS = {"stdout", "callmebot", "telegram"}


def send(message: str) -> bool:
    """
    Envía notificación solo si NOTIFICATIONS_ENABLED=true.
    """
    if not NOTIFICATIONS_ENABLED:
        print("[notify] APAGADO (NOTIFICATIONS_ENABLED=false) — mensaje no enviado")
        print("[notify] mensaje que se hubiera enviado:")
        print("─" * 60)
        print(message)
        print("─" * 60)
        return False

    if CHANNEL not in ALLOWED_CHANNELS:
        print(f"[notify] CHANNEL='{CHANNEL}' NO permitido. "
              f"Válidos: {sorted(ALLOWED_CHANNELS)}. Mensaje no enviado.")
        return False

    if CHANNEL == "stdout":
        print(message)
        return True

    if CHANNEL == "callmebot":
        return _send_callmebot(message)

    if CHANNEL == "telegram":
        return _send_telegram(message)

    return False


def _send_callmebot(mensaje: str) -> bool:
    phone  = os.environ.get("CALLMEBOT_PHONE", "").strip()
    apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not phone or not apikey:
        print("[notify] CallMeBot: sin CALLMEBOT_PHONE/APIKEY — mensaje no enviado")
        return False
    # CallMeBot espera solo dígitos, sin '+', sin espacios, sin guiones
    phone_clean = re.sub(r'\D', '', phone)   # extrae solo dígitos
    if not phone_clean:
        print("[notify] CallMeBot: CALLMEBOT_PHONE sin dígitos — mensaje no enviado")
        return False
    print(f"[notify] CallMeBot → phone={phone_clean[:4]}***{phone_clean[-3:]} len={len(phone_clean)} apikey_len={len(apikey)}")
    try:
        # params= deja que requests maneje el URL-encoding (evita doble-encoding manual)
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone_clean, "text": mensaje, "apikey": apikey},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        ok = r.status_code == 200
        if not ok:
            print(f"[notify] CallMeBot fallo: HTTP {r.status_code} — {r.text[:100]}")
        else:
            print("[notify] ✅ CallMeBot enviado")
        return ok
    except Exception as e:
        print(f"[notify] CallMeBot excepción: {e}")
        return False


def _send_telegram(message: str) -> bool:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[notify] Telegram: sin TOKEN/CHAT_ID — mensaje no enviado")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": chat_id, "text": message,
            "parse_mode": "Markdown", "disable_web_page_preview": True,
        }, timeout=15)
        ok = r.status_code == 200 and r.json().get("ok")
        if not ok:
            print(f"[notify] Telegram fallo: HTTP {r.status_code}")
        return ok
    except Exception as e:
        print(f"[notify] Telegram excepción: {e}")
        return False


if __name__ == "__main__":
    print(f"NOTIFICATIONS_ENABLED = {NOTIFICATIONS_ENABLED}")
    print(f"CHANNEL = {CHANNEL!r} (allowed: {sorted(ALLOWED_CHANNELS)})")
    print()
    send("🏆 MUNDIAL IA | Test mensaje desde notify.py")
