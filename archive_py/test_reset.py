#!/usr/bin/env python
"""
Reset via TUNNEL IDENTITY (padrão Beholder) - versão otimizada.

1. Extrai GUIDs do thread history
2. Probe rápido: testa {GUID}.{router}@0mn.io - só limpa as que TÊM variáveis
3. Também limpa WhatsApp identity + central endpoint

Uso: python test_reset.py
"""
import sys
import uuid
import time
import re
import requests
from config import (
    BLIP_KEY, BLIP_BOT_ID, BLIP_USER_ID, BLIP_USER_DOMAIN,
    WHATSAPP_MEU_NUMERO, USE_WHATSAPP,
)

SEP = "=" * 70


def _headers():
    key = BLIP_KEY.strip()
    if not key.upper().startswith("KEY "):
        key = f"Key {key}"
    return {"Authorization": key, "Content-Type": "application/json"}


def cmd(url, headers, payload):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("status", "?"), data
    except Exception as e:
        return "exception", {"error": str(e)}


def get_keys(url, headers, identity):
    """GET das variáveis de uma identidade."""
    status, data = cmd(url, headers, {
        "id": str(uuid.uuid4()),
        "method": "get",
        "to": "postmaster@msging.net",
        "uri": f"/contexts/{identity}?withContextValues=true&$take=1000",
    })
    if status != "success":
        return []
    resource = data.get("resource", {})
    if not isinstance(resource, dict):
        return []
    items = resource.get("items", [])
    keys = []
    for v in items:
        if isinstance(v, str):
            keys.append(v)
        elif isinstance(v, dict):
            name = v.get("name", v.get("key", ""))
            if name:
                keys.append(name)
    return keys


def delete_all_keys(url, headers, identity, keys):
    """DELETE de cada variável."""
    ok = 0
    for name in keys:
        s, _ = cmd(url, headers, {
            "id": f"del-{name[:40]}-{int(time.time()*1000)}",
            "method": "delete",
            "to": "postmaster@msging.net",
            "uri": f"/contexts/{identity}/{name}",
        })
        if s == "success":
            ok += 1
    return ok


def clean_loop(url, headers, identity):
    """Limpa uma identidade em loop até zerar. Retorna total deletado."""
    total = 0
    for _ in range(10):
        keys = get_keys(url, headers, identity)
        if not keys:
            break
        ok = delete_all_keys(url, headers, identity, keys)
        total += ok
        time.sleep(0.3)
    return total


def extract_guids(url, headers, user_id):
    """Extrai GUIDs do thread. Retorna set de GUIDs."""
    guids = set()
    guid_re = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)

    for uri_path in [
        f"/threads/{user_id}?$take=100",
        f"/threads/{user_id}/messages?$take=50",
    ]:
        s, d = cmd(url, headers, {
            "id": str(uuid.uuid4()),
            "method": "get",
            "to": "postmaster@msging.net",
            "uri": uri_path,
        })
        if s == "success":
            raw = str(d)
            guids.update(guid_re.findall(raw))

    return guids


def main():
    if not (BLIP_KEY and BLIP_BOT_ID):
        print("Configure BLIP_KEY e BLIP_BOT_ID no .env")
        sys.exit(1)

    user_id = WHATSAPP_MEU_NUMERO if (USE_WHATSAPP and WHATSAPP_MEU_NUMERO) else BLIP_USER_ID
    if not user_id:
        print("Configure BLIP_USER_ID ou WHATSAPP_MEU_NUMERO no .env")
        sys.exit(1)

    user_id = user_id.replace("+", "").replace(" ", "")
    if "@" not in user_id:
        domain = BLIP_USER_DOMAIN or "wa.gw.msging.net"
        user_id = f"{user_id}@{domain}"

    headers = _headers()
    url_central = "https://msging.net/commands"
    url_bot = f"https://{BLIP_BOT_ID}.http.msging.net/commands"
    router = BLIP_BOT_ID

    print(SEP)
    print("RESET VIA TUNNEL IDENTITY (otimizado)")
    print(SEP)
    print(f"Router: {router}")
    print(f"User:   {user_id}\n")

    # ── FASE 1: Encontrar GUIDs ──
    print("── FASE 1: Buscando GUIDs ──")
    guids = set()
    for url in [url_central, url_bot]:
        guids.update(extract_guids(url, headers, user_id))
    print(f"  {len(guids)} GUIDs encontrados no thread\n")

    # ── FASE 2: Probe rápido - quais tunnel identities TÊM variáveis? ──
    print("── FASE 2: Probe (testando cada tunnel identity) ──")
    identities_com_vars = []
    tested = 0

    for guid in sorted(guids):
        identity = f"{guid}.{router}@0mn.io"
        keys = get_keys(url_bot, headers, identity)
        tested += 1
        if tested % 50 == 0:
            print(f"  ... testados {tested}/{len(guids)}")
        if keys:
            identities_com_vars.append((identity, keys))
            print(f"  ✓ {identity[:50]}... → {len(keys)} var(s)")

    print(f"\n  Testados: {tested}")
    print(f"  Com variáveis: {len(identities_com_vars)}")

    # ── FASE 3: Deletar de tunnel identities com variáveis ──
    print(f"\n── FASE 3: Deletando ──")
    grand_total = 0

    for identity, keys in identities_com_vars:
        ok = delete_all_keys(url_bot, headers, identity, keys)
        remaining = clean_loop(url_bot, headers, identity)
        total = ok + remaining
        grand_total += total
        print(f"  {identity[:50]}... → {total} deletadas")

    # ── FASE 4: Limpar WhatsApp identity (ambos endpoints) ──
    print(f"\n── FASE 4: WhatsApp identity ──")
    for label, url in [("central", url_central), ("bot", url_bot)]:
        total = clean_loop(url, headers, user_id)
        if total > 0:
            print(f"  [{label}] {total} deletadas")
        else:
            print(f"  [{label}] já limpo")
        grand_total += total

    # ── FASE 5: Verificação ──
    print(f"\n── FASE 5: Verificação ──")
    remaining = 0

    for identity, _ in identities_com_vars:
        keys = get_keys(url_bot, headers, identity)
        if keys:
            remaining += len(keys)
            print(f"  ✗ {identity[:50]}... → {len(keys)} restante(s)")

    for url in [url_central, url_bot]:
        keys = get_keys(url, headers, user_id)
        if keys:
            remaining += len(keys)
            print(f"  ✗ {user_id} → {len(keys)} restante(s)")

    print(f"\n{SEP}")
    print(f"TOTAL: {grand_total} variáveis deletadas")
    if remaining == 0:
        print("TUDO LIMPO ✓ — Envie mensagem ao bot para testar!")
    else:
        print(f"ATENÇÃO: {remaining} variável(is) restante(s)")
    print(SEP)


if __name__ == "__main__":
    main()
