"""
notify.py — Notificación del mundial-bot

KILL-SWITCH GLOBAL: NOTIFICATIONS_ENABLED env var
  - Default: false (NO envía nada, solo loguea a stdout)
  - Solo se enciende cuando JP confirme:
      1. Canal final (Telegram dedicado, CallMeBot con prefijo nuevo, etc.)
      2. Horario final del cron
      3. Formato del mensaje aprobado

Hasta entonces, el bot corre el pipeline completo SILENCIOSO. Los picks
y el estado se persisten en bets_history.json y model_state.json para que
JP pueda revisar la calidad del modelo sin recibir notificaciones de prueba.
"""
from __future__ import annotations
import os
import requests


NOTIFICATIONS_ENABLED = os.environ.get("NOTIFICATIONS_ENABLED", "false").lower() in ("1", "true", "yes")


def send(message: str) -> bool:
    """
    Envía notificación SOLO si NOTIFICATIONS_ENABLED=true.
    Por default está APAGADO hasta que JP defina canal+horario+formato finales.

    Canales soportados (se elige por env CHANNEL):
      - 'telegram' (default): requiere TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
      - 'callmebot': requiere CALLMEBOT_PHONE + CALLMEBOT_APIKEY (NO recomendado
        para mundial-bot — comparte canal con playdoit-bot)
      - 'stdout': solo imprime, no envía (igual que NOTIFICATIONS_ENABLED=false)
    """
    if not NOTIFICATIONS_ENABLED:
        print("[notify] APAGADO (NOTIFICATIONS_ENABLED=false) — mensaje no enviado")
        print("[notify] mensaje que se hubiera enviado:")
        print("─" * 60)
        print(message)
        print("─" * 60)
        return False

    channel = os.environ.get("CHANNEL", "telegram").lower()

    if channel == "telegram":
        return _send_telegram(message)
    elif channel == "callmebot":
        return _send_callmebot(message)
    elif channel == "stdout":
        print(message)
        return True
    else:
        print(f"[notify] CHANNEL desconocido: {channel}")
        return False


def _send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[notify] Telegram: sin TOKEN/CHAT_ID")
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


def _send_callmebot(message: str) -> bool:
    import urllib.parse
    phone = os.environ.get("CALLMEBOT_PHONE", "")
    apikey = os.environ.get("CALLMEBOT_APIKEY", "")
    if not phone or not apikey:
        print("[notify] CallMeBot: sin credenciales")
        return False
    url = (f"https://api.callmebot.com/whatsapp.php"
           f"?phone={phone}&text={urllib.parse.quote(message)}&apikey={apikey}")
    try:
        r = requests.get(url, timeout=15)
        return r.status_code == 200 and "Message queued" in r.text
    except Exception:
        return False


if __name__ == "__main__":
    print(f"NOTIFICATIONS_ENABLED = {NOTIFICATIONS_ENABLED}")
    send("Test mensaje. Si lo ves arriba en consola, el kill-switch está apagando el envío real.")
