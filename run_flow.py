#!/usr/bin/env python
"""
Executa o fluxo de teste - exibe mensagens e retorno da Blip no terminal.
Modos: Blip API (direto, sem navegador) ou WhatsApp (com navegador).
"""
import sys
import time
from flow_executor import carregar_fluxo, executar_fluxo, _get_client
from config import BLIP_KEY, BLIP_BOT_ID, BLIP_USER_ID, USE_WHATSAPP, WHATSAPP_NUMERO_ALVO, PAUSE_ANTES_INICIAR


def main():
    usa_blip = BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID
    usa_whatsapp = (USE_WHATSAPP or not usa_blip) and WHATSAPP_NUMERO_ALVO

    if not usa_blip and not usa_whatsapp:
        print("Configure o .env:")
        print("  Blip API (recomendado): BLIP_KEY, BLIP_BOT_ID, BLIP_USER_ID")
        print("  Ou WhatsApp: WHATSAPP_NUMERO_ALVO")
        sys.exit(1)

    fluxo_arquivo = sys.argv[1] if len(sys.argv) > 1 else "flows/fluxo_agendar.json"
    indice_cpf = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    print(f"Carregando fluxo: {fluxo_arquivo}")
    fluxo = carregar_fluxo(fluxo_arquivo)
    print(f"\nFluxo: {fluxo.get('nome', 'Sem nome')}")
    print("=" * 50)
    print("Será exibido no terminal:")
    print("  → Enviado: sua mensagem")
    print("  ← Blip API: retorno do bot")
    print("=" * 50)

    cliente = _get_client()

    if usa_whatsapp:
        print("\nAbrindo WhatsApp Web...")
        print("Na primeira vez, escaneie o QR code.")
        if PAUSE_ANTES_INICIAR:
            input("Pressione Enter para continuar...")
        try:
            if not cliente.iniciar():
                input("Escaneie o QR code. Quando logado, pressione Enter...")
                cliente._abrir_chat()
                time.sleep(5)
        except Exception as e:
            print(f"Erro ao iniciar WhatsApp: {e}")
            sys.exit(1)

    print("\n--- Iniciando fluxo ---\n")
    try:
        resultados = executar_fluxo(fluxo, cliente, exibir_no_terminal=True, indice_cpf=indice_cpf)
        print("\n" + "=" * 50)
        print("✓ Fluxo concluído!")
        for r in resultados:
            print(f"  Passo {r['passo']}: {r['mensagem']} - {r['status']}")
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
