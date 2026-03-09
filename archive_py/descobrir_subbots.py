#!/usr/bin/env python
"""
Tenta descobrir informações dos sub-bots via API do Router.
Isso pode revelar as API keys dos sub-bots para que possamos limpar
o contexto diretamente neles.
"""
import uuid
import json
import requests
from config import BLIP_KEY, BLIP_BOT_ID

def _headers():
    key = BLIP_KEY.strip()
    if not key.upper().startswith("KEY "):
        key = f"Key {key}"
    return {"Authorization": key, "Content-Type": "application/json"}

def cmd(base_url, headers, payload):
    try:
        r = requests.post(f"{base_url}/commands", headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"status": "exception", "error": str(e)}

base_url = f"https://{BLIP_BOT_ID}.http.msging.net"
headers = _headers()

print(f"Router: {BLIP_BOT_ID}\n")

# Tentar vários endpoints para descobrir sub-bots e suas configs
endpoints = [
    ("/services", "postmaster@msging.net"),
    ("/services", "postmaster@builder.msging.net"),
    ("/applications", "postmaster@msging.net"),
    ("/applications", "postmaster@portal.msging.net"),
    ("/profile", "postmaster@msging.net"),
    ("/configuration", "postmaster@builder.msging.net"),
    ("/buckets/blip_portal:builder_configuration", "postmaster@builder.msging.net"),
    ("/delegations", "postmaster@msging.net"),
    ("/account", None),
]

for uri, dest in endpoints:
    payload = {
        "id": str(uuid.uuid4()),
        "method": "get",
        "uri": uri,
    }
    if dest:
        payload["to"] = dest

    data = cmd(base_url, headers, payload)
    status = data.get("status", "?")

    if status == "success":
        resource = data.get("resource", {})
        raw = json.dumps(resource, indent=2, ensure_ascii=False)
        if len(raw) > 2000:
            raw = raw[:2000] + "\n... (truncado)"
        print(f"✓ {uri} @ {dest or '(sem to)'}")
        print(f"  {raw}\n")
    else:
        reason = data.get("reason", {}).get("description", "")
        if reason:
            print(f"✗ {uri} @ {dest or '(sem to)'} → {status}: {reason}")
        # Silencia erros sem motivo
