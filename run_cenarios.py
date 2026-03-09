#!/usr/bin/env python
"""
Executa cenários de teste - lista e roda por nome ou ID.
Modo --parallel: executa vários cenários em threads (Blip API).
"""
import sys
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from flow_executor import carregar_fluxo, executar_fluxo, _get_client
from config import BLIP_KEY, BLIP_BOT_ID, BLIP_USER_ID, USE_WHATSAPP, WHATSAPP_NUMERO_ALVO, PAUSE_ANTES_INICIAR

_print_lock = threading.Lock()


def _print_safe(*args, **kwargs):
    """Print thread-safe para modo paralelo."""
    with _print_lock:
        print(*args, **kwargs)


def carregar_cenarios() -> list:
    """Carrega cenários de cenarios.json."""
    path = Path(__file__).parent / "cenarios.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("cenarios", [])


def listar_cenarios():
    """Lista todos os cenários disponíveis."""
    cenarios = carregar_cenarios()
    print("\nCenários disponíveis:\n")
    for i, c in enumerate(cenarios):
        print(f"  {i}  {c['id']}")
        print(f"      {c['nome']} - {c.get('descricao', '')}")
        print(f"      CPF: cpfs[{c.get('cpf_index', 0)}]")
        print()
    print("Uso: python run_cenarios.py <id ou número> [--parallel id1 id2 ...]")
    print("Ex: python run_cenarios.py fluxo_feliz")
    print("Ex: python run_cenarios.py 0")
    print("Ex: python run_cenarios.py --parallel fluxo_feliz aceite_oferta  (Blip API)")


def main():
    usa_blip = BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID
    usa_whatsapp = (USE_WHATSAPP or not usa_blip) and WHATSAPP_NUMERO_ALVO

    if not usa_blip and not usa_whatsapp:
        print("Configure no .env: USE_WHATSAPP=true + WHATSAPP_NUMERO_ALVO ou BLIP_KEY/BOT_ID/USER_ID")
        sys.exit(1)

    cenarios = carregar_cenarios()
    if not cenarios:
        print("Nenhum cenário em cenarios.json")
        sys.exit(1)

    # Sem argumento: lista cenários
    if len(sys.argv) < 2:
        listar_cenarios()
        return

    args = sys.argv[1:]
    modo_parallel = "--parallel" in args
    if modo_parallel:
        args = [a for a in args if a != "--parallel"]

    if not args:
        print("Informe cenário(s) ou use --parallel id1 id2 ...")
        sys.exit(1)

    def _buscar_cenario(arg):
        if arg.isdigit():
            idx = int(arg)
            if idx < 0 or idx >= len(cenarios):
                return None, f"Cenário {idx} não encontrado"
            return cenarios[idx], None
        c = next((c for c in cenarios if c["id"] == arg), None)
        if not c:
            return None, f"Cenário '{arg}' não encontrado"
        return c, None

    # Modo paralelo (apenas Blip API - WhatsApp usa 1 navegador)
    if modo_parallel:
        if usa_whatsapp:
            print("Modo --parallel não suportado com WhatsApp. Use Blip API.")
            sys.exit(1)
        cenarios_para_rodar = []
        for arg in args:
            c, err = _buscar_cenario(arg)
            if err:
                print(err)
                sys.exit(1)
            cenarios_para_rodar.append(c)

        print(f"\n--- Executando {len(cenarios_para_rodar)} cenário(s) em paralelo ---\n")

        def _rodar_cenario(cenario):
            fluxo = carregar_fluxo(cenario["fluxo"])
            cliente = _get_client()
            rotulo = cenario["id"]
            try:
                return rotulo, executar_fluxo(
                    fluxo, cliente, exibir_no_terminal=True,
                    indice_cpf=cenario.get("cpf_index", 0), rotulo=rotulo
                )
            except Exception as e:
                return rotulo, None, str(e)

        with ThreadPoolExecutor(max_workers=len(cenarios_para_rodar)) as ex:
            futures = {ex.submit(_rodar_cenario, c): c for c in cenarios_para_rodar}
            for f in as_completed(futures):
                resultado = f.result()
                rotulo = resultado[0]
                if len(resultado) == 3:
                    with _print_lock:
                        print(f"\n✗ [{rotulo}] Erro: {resultado[2]}")
                else:
                    with _print_lock:
                        print(f"\n✓ [{rotulo}] Concluído - {len(resultado[1])} passos")
        return

    # Modo único
    cenario, err = _buscar_cenario(args[0])
    if err:
        print(err)
        sys.exit(1)

    fluxo_path = cenario["fluxo"]
    cpf_index = cenario.get("cpf_index", 0)

    print(f"\nCenário: {cenario['nome']}")
    print(f"Descrição: {cenario.get('descricao', '')}")
    print(f"Fluxo: {fluxo_path} | CPF: cpfs[{cpf_index}]")
    print("=" * 50)

    fluxo = carregar_fluxo(fluxo_path)
    cliente = _get_client()

    if usa_whatsapp:
        import time
        print("\nAbrindo WhatsApp Web...")
        if PAUSE_ANTES_INICIAR:
            input("Pressione Enter para continuar...")
        try:
            if not cliente.iniciar():
                input("Escaneie o QR code. Quando logado, pressione Enter...")
                cliente._abrir_chat()
                time.sleep(5)
        except Exception as e:
            print(f"Erro: {e}")
            sys.exit(1)

    print("\n--- Executando cenário ---\n")
    try:
        resultados = executar_fluxo(fluxo, cliente, exibir_no_terminal=True, indice_cpf=cpf_index, rotulo=cenario["id"])
        print("\n" + "=" * 50)
        print("✓ Cenário concluído!")
        for r in resultados:
            print(f"  Passo {r['passo']}: {r['mensagem'][:50]}... - {r['status']}")
    except Exception as e:
        print(f"\n✗ Erro: {e}")
        sys.exit(1)
    finally:
        if hasattr(cliente, "fechar"):
            if PAUSE_ANTES_INICIAR:
                input("\nPressione Enter para encerrar...")
            cliente.fechar()


if __name__ == "__main__":
    main()
