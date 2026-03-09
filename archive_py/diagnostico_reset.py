#!/usr/bin/env python
"""
Diagnóstico do reset: testa acesso a cada bot e mostra onde está o contexto.
Ajuda a descobrir por que o usuário não cai no topo do bot.
"""
import sys
import uuid
import requests
from config import BLIP_KEY, BLIP_BOT_ID, BLIP_USER_ID, BLIP_USER_DOMAIN, WHATSAPP_MEU_NUMERO, USE_WHATSAPP, BLIP_SUB_BOT_IDS, BLIP_SUB_BOT_ID


def main():
    if not (BLIP_KEY and BLIP_BOT_ID):
        print("Configure BLIP_KEY e BLIP_BOT_ID no .env")
        sys.exit(1)

    user_id = WHATSAPP_MEU_NUMERO if (USE_WHATSAPP and WHATSAPP_MEU_NUMERO) else BLIP_USER_ID
    if not user_id:
        user_id = BLIP_USER_ID
    if not user_id:
        print("Configure BLIP_USER_ID ou WHATSAPP_MEU_NUMERO")
        sys.exit(1)

    user_identity = user_id.replace("+", "").replace(" ", "")
    if "@" not in user_identity:
        user_identity = f"{user_identity}@{BLIP_USER_DOMAIN or 'wa.gw.msging.net'}"

    key = BLIP_KEY.strip()
    if not key.upper().startswith("KEY "):
        key = f"Key {key}"
    headers = {"Authorization": key, "Content-Type": "application/json"}

    bots = [("Router", f"https://{BLIP_BOT_ID}.http.msging.net")]
    for sid in (BLIP_SUB_BOT_IDS or ([BLIP_SUB_BOT_ID] if BLIP_SUB_BOT_ID else [])):
        if sid:
            bots.append((sid, f"https://{sid}.http.msging.net"))

    print("=" * 70)
    print("DIAGNÓSTICO DE RESET - Onde está o contexto?")
    print("=" * 70)
    print(f"User: {user_identity}")
    print(f"Bots a testar: {[b[0] for b in bots]}")
    print()

    destinos = ["postmaster@builder.msging.net", "postmaster@msging.net"]

    for nome, base_url in bots:
        print(f"\n--- {nome} ({base_url}) ---")
        for dest in destinos:
            try:
                r = requests.post(
                    f"{base_url}/commands",
                    headers=headers,
                    json={
                        "id": str(uuid.uuid4()),
                        "method": "get",
                        "uri": f"/contexts/{user_identity}?withContextValues=true",
                        "to": dest,
                    },
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    status = data.get("status", "?")
                    resource = data.get("resource", {})
                    items = resource.get("items", []) if isinstance(resource, dict) else []
                    if isinstance(items, list):
                        vars_list = []
                        for x in items:
                            if isinstance(x, str):
                                vars_list.append(x)
                            elif isinstance(x, dict):
                                vars_list.append(x.get("id") or x.get("name") or x.get("key") or str(x))
                            else:
                                vars_list.append(str(x))
                        print(f"  {dest}: OK - {len(vars_list)} variável(is)")
                        if vars_list:
                            for v in vars_list[:8]:
                                print(f"      - {v}")
                            if len(vars_list) > 8:
                                print(f"      ... +{len(vars_list)-8} mais")
                    else:
                        print(f"  {dest}: OK - formato inesperado: {type(items)}")
                else:
                    print(f"  {dest}: ERRO {r.status_code} - {r.text[:200]}")
            except requests.exceptions.ConnectionError as e:
                print(f"  {dest}: ERRO conexão - {e}")
            except Exception as e:
                print(f"  {dest}: ERRO - {e}")

    print()
    print("=" * 70)
    print("INTERPRETAÇÃO:")
    print("- 'Use router context' ativado: contexto fica só no ROUTER (sub-bots não têm)")
    print("- Se Router tem variáveis: reset deve limpar. Se sub-bots dão 401: Key só acessa router")
    print("- Se todos 0 variáveis mas bot continua do meio: contexto em outro destino ou cache")
    print("=" * 70)


if __name__ == "__main__":
    main()
