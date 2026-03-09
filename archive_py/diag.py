"""Reset COMPLETO: deleta contexto do Router + cada sub-bot."""
import uuid, requests, time
from concurrent.futures import ThreadPoolExecutor, as_completed

KEY = "Key Ym1ncm91dGVyYmV0YTo1SlBHaXVtbVVXM3hFWTE4R01Zcw=="
USER = "5511918545848@wa.gw.msging.net"
BOT = "bmgrouterbeta"
SUB_BOTS = ["bmgcascatadev", "bmgsystemoutdev", "bmgcpfdev", "bmglinkdev", "bmgin100dev", "bmgemprestimosdev"]

h = {"Authorization": KEY, "Content-Type": "application/json"}
url_c = "https://msging.net/commands"

def cmd(endpoint, payload):
    payload["id"] = str(uuid.uuid4())
    return requests.post(endpoint, headers=h, json=payload, timeout=15).json()

def limpar_contexto(label, endpoint):
    """Lista e deleta todas as variaveis de contexto num endpoint."""
    r = cmd(endpoint, {"to": "postmaster@msging.net", "method": "get",
        "uri": f"/contexts/{USER}?$take=1000"})
    items = r.get("resource", {}).get("items", [])
    if not items:
        print(f"  [{label}] 0 variaveis", flush=True)
        return 0

    ok = 0
    def del_var(var):
        r2 = cmd(endpoint, {"to": "postmaster@msging.net", "method": "delete",
            "uri": f"/contexts/{USER}/{var}"})
        return r2.get("status") == "success"

    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(del_var, v): v for v in items}
        for f in as_completed(futures):
            if f.result():
                ok += 1

    print(f"  [{label}] {ok}/{len(items)} deletadas", flush=True)
    return ok

print("=== RESET COMPLETO (Router + Sub-bots) ===\n", flush=True)

total = 0

# 1. Router (endpoint central)
total += limpar_contexto("Router (central)", url_c)

# 2. Router (endpoint bot-specific)
total += limpar_contexto("Router (bot)", f"https://{BOT}.http.msging.net/commands")

# 3. Cada sub-bot
for sb in SUB_BOTS:
    sb_url = f"https://{sb}.http.msging.net/commands"
    try:
        total += limpar_contexto(sb, sb_url)
    except Exception as e:
        print(f"  [{sb}] ERRO: {e}", flush=True)

print(f"\nTotal deletadas: {total}", flush=True)

# Esperar e verificar
time.sleep(3)
print("\n=== VERIFICACAO ===\n", flush=True)
rest_total = 0

r = cmd(url_c, {"to": "postmaster@msging.net", "method": "get",
    "uri": f"/contexts/{USER}?$take=1000"})
rest = len(r.get("resource", {}).get("items", []))
print(f"  Router (central): {rest} restantes", flush=True)
rest_total += rest

for sb in SUB_BOTS:
    sb_url = f"https://{sb}.http.msging.net/commands"
    try:
        r2 = cmd(sb_url, {"to": "postmaster@msging.net", "method": "get",
            "uri": f"/contexts/{USER}?$take=1000"})
        rest_sb = len(r2.get("resource", {}).get("items", []))
        if rest_sb > 0:
            print(f"  {sb}: {rest_sb} restantes", flush=True)
        rest_total += rest_sb
    except:
        pass

if rest_total == 0:
    print("  TUDO LIMPO!", flush=True)
else:
    print(f"  {rest_total} variaveis restantes - rodando 2a limpeza...", flush=True)

print("\n>>> Agora envie uma mensagem pelo WhatsApp para testar <<<", flush=True)
