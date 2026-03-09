#!/usr/bin/env python
"""
Script de diagnóstico para verificar o contexto do usuário na Blip.
Mostra a resposta RAW da API para entender o formato.
Execute: python debug_contexto.py
"""
import json
import uuid
import requests
from config import BLIP_KEY, BLIP_BOT_ID, BLIP_USER_ID, BLIP_USER_DOMAIN, WHATSAPP_MEU_NUMERO, USE_WHATSAPP

def main():
    user_id = (WHATSAPP_MEU_NUMERO if (USE_WHATSAPP and WHATSAPP_MEU_NUMERO) else BLIP_USER_ID).replace("+", "").replace(" ", "")
    if "@" not in user_id:
        domain = BLIP_USER_DOMAIN or "wa.gw.msging.net"
        user_id = f"{user_id}@{domain}"

    key = BLIP_KEY.strip()
    if not key.upper().startswith("KEY "):
        key = f"Key {key}"
    headers = {"Authorization": key, "Content-Type": "application/json"}
    base = f"https://{BLIP_BOT_ID}.http.msging.net"

    print(f"User identity: {user_id}")
    print(f"Base URL: {base}")
    print("=" * 60)

    for destino in ["postmaster@builder.msging.net", "postmaster@msging.net", f"{BLIP_BOT_ID}@msging.net"]:
        print(f"\n--- GET /contexts/{user_id}  to={destino} ---")
        payload = {"id": str(uuid.uuid4()), "method": "get", "uri": f"/contexts/{user_id}", "to": destino}
        try:
            r = requests.post(f"{base}/commands", headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            print(f"Status: {data.get('status')}")
            rsc = data.get("resource", {})
            if rsc:
                items = rsc.get("items", [])
                total = rsc.get("total", len(items))
                print(f"Items: {len(items)}, total: {total}")
                if items:
                    print(f"Primeiro item: {items[0]!r} (type={type(items[0])})")
                    if isinstance(items[0], dict):
                        print(f"  Keys: {list(items[0].keys())}")
                print(f"Resource keys: {list(rsc.keys())}")
                print(f"Resource (pretty):\n{json.dumps(rsc, indent=2, ensure_ascii=False)[:1500]}...")
            else:
                print(f"Resposta completa: {json.dumps(data, indent=2, ensure_ascii=False)[:800]}")
        except Exception as e:
            print(f"Erro: {e}")

def test_delete():
    """Testa um DELETE e mostra a resposta."""
    from blip_client import BlipClient
    c = BlipClient()
    user_id = (WHATSAPP_MEU_NUMERO if (USE_WHATSAPP and WHATSAPP_MEU_NUMERO) else BLIP_USER_ID).replace("+", "").replace(" ", "")
    if "@" not in user_id:
        user_id = f"{user_id}@{BLIP_USER_DOMAIN or 'wa.gw.msging.net'}"
    c.user_identity = user_id
    items = c.obter_contexto_atual()
    if not items:
        print("Nenhuma variável para testar DELETE")
        return
    test_key = items[0]
    print(f"\n--- Testando DELETE de '{test_key}' ---")
    key = BLIP_KEY.strip()
    if not key.upper().startswith("KEY "):
        key = f"Key {key}"
    payload = {"id": str(uuid.uuid4()), "method": "delete", "uri": f"/contexts/{user_id}/{test_key}", "to": "postmaster@builder.msging.net"}
    r = requests.post(f"https://{BLIP_BOT_ID}.http.msging.net/commands", headers={"Authorization": key, "Content-Type": "application/json"}, json=payload, timeout=30)
    print(f"HTTP {r.status_code}")
    print(f"Resposta: {json.dumps(r.json(), indent=2, ensure_ascii=False)}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "delete":
        test_delete()
    else:
        main()
