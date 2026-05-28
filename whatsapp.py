"""whatsapp.py — Envío vía CallMeBot."""
from __future__ import annotations
import os
import urllib.parse
import requests


def send(message: str) -> bool:
    phone = os.environ.get("CALLMEBOT_PHONE", "")
    apikey = os.environ.get("CALLMEBOT_APIKEY", "")
    if not phone or not apikey:
        print("[whatsapp] sin credenciales — mensaje no enviado")
        return False

    url = (
        f"https://api.callmebot.com/whatsapp.php"
        f"?phone={phone}&text={urllib.parse.quote(message)}&apikey={apikey}"
    )
    try:
        r = requests.get(url, timeout=15)
        ok = r.status_code == 200 and "Message queued" in r.text
        if not ok:
            print(f"[whatsapp] fallo: HTTP {r.status_code} body={r.text[:200]}")
        return ok
    except Exception as e:
        print(f"[whatsapp] excepción: {e}")
        return False
