"""
notify.py — Notificación del mundial-bot vía TELEGRAM (NO CallMeBot)

Diseño deliberado: canal SEPARADO del playdoit-bot.
  - playdoit-bot usa CallMeBot/WhatsApp
  - mundial-bot usa Telegram bot dedicado

Por qué Telegram:
  - 100% gratis, sin límites
  - Bot dedicado garantiza 0 cruce con el otro bot
  - 30 seg para crearlo: chatea con @BotFather en Telegram, /newbot
  - Después se obtiene TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
"""
from __future__ import annotations
import os
import requests


def send(message: str) -> bool:
    """
    Envía vía Telegram bot. Requiere env vars:
      - TELEGRAM_BOT_TOKEN: token del bot (creado en @BotFather)
      - TELEGRAM_CHAT_ID: ID del chat destino (usualmente el chat 1:1 con JP)
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("[notify] sin TELEGRAM_BOT_TOKEN/CHAT_ID — mensaje no enviado")
        print("[notify] mensaje que se hubiera enviado:")
        print("─" * 60)
        print(message)
        print("─" * 60)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=15)
        ok = r.status_code == 200 and r.json().get("ok")
        if not ok:
            print(f"[notify] fallo Telegram: HTTP {r.status_code} body={r.text[:200]}")
        return ok
    except Exception as e:
        print(f"[notify] excepción: {e}")
        return False


if __name__ == "__main__":
    send("🏆 *MUNDIAL IA*\n\nTest de conexión Telegram OK.")
