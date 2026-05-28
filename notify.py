"""
notify.py — Notificación del mundial-bot

═══════════════════════════════════════════════════════════════════════════
REGLA DURA (instrucción explícita de JP, 28-may-2026):
  El mundial-bot NO puede enviar nada a CallMeBot bajo ninguna circunstancia
  hasta que JP defina canal + horario + formato finales.

Por eso esta versión:
  1. NO importa nada relacionado con CallMeBot
  2. NO tiene función para enviar a CallMeBot
  3. NO acepta "callmebot" como CHANNEL
  4. Kill-switch global NOTIFICATIONS_ENABLED=false por default

Si un futuro contributor (humano o IA) trata de reintroducir CallMeBot aquí,
revisar primero memoria/project_mundial_bot.md y confirmar con JP.
═══════════════════════════════════════════════════════════════════════════

Canales soportados (cuando JP encienda el switch):
  - 'stdout' (default temporal): solo loguea, no envía a ningún lado
  - 'telegram': cuando JP cree bot en @BotFather y pase TOKEN+CHAT_ID
"""
from __future__ import annotations
import os
import requests


NOTIFICATIONS_ENABLED = os.environ.get("NOTIFICATIONS_ENABLED", "false").lower() in ("1", "true", "yes")
CHANNEL = os.environ.get("CHANNEL", "stdout").lower()

# Canales VÁLIDOS — CallMeBot NO está en esta lista a propósito
ALLOWED_CHANNELS = {"stdout", "telegram"}


def send(message: str) -> bool:
    """
    Envía notificación SOLO si NOTIFICATIONS_ENABLED=true.
    Por default está APAGADO. Aún encendido, NUNCA va a CallMeBot.
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

    if CHANNEL == "telegram":
        return _send_telegram(message)

    return False


def _send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
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
    send("Test mensaje. Si NOTIFICATIONS_ENABLED=false, esto no se envía.")
