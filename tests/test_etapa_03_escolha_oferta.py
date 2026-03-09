import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import blip_client as blip_module
from blip_client import BlipClient
from config import (
    BLIP_BOT_ID,
    BLIP_KEY,
    BLIP_USER_ID,
    BLIP_USER_DOMAIN,
    USE_WHATSAPP,
    WHATSAPP_MEU_NUMERO,
    WHATSAPP_NUMERO_ALVO,
)
from whatsapp_client import WhatsAppClient

# Mantém output limpo para terminal/HTML.
blip_module.DEBUG_BLIP = False

MENSAGEM_INICIAL = (
    "Oi! Vim do aplicativo Carteira de Trabalho Digital para solicitar o "
    "Empréstimo Consignado Privado. Meu CPF é 06105813503. "
    "Meu código de solicitação é: 30776386"
)
RESPOSTA_AUTORIZACAO = "Li e autorizo"
BOTAO_LISTA = "Escolher ofertas"


def _blip_no_mesmo_usuario_whatsapp() -> BlipClient:
    blip = BlipClient()
    user_id = WHATSAPP_MEU_NUMERO or BLIP_USER_ID
    user_id = user_id.replace("+", "").replace(" ", "")
    if "@" not in user_id:
        user_id = f"{user_id}@{BLIP_USER_DOMAIN or 'wa.gw.msging.net'}"
    blip.user_identity = user_id
    return blip


def _imprimir_contexto_blip(blip: BlipClient):
    print("\n[contexto blip]")
    print(f"  Base URL: {blip.base_url}")
    print(f"  Central URL: {blip.base_url_central}")
    print(f"  User: {blip.user_identity} | Bot: {blip.bot_identity}")


def _parse_iso_datetime(value: str):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _capturar_respostas_ate_estabilizar(
    blip: BlipClient,
    ultimo_id_antes: str,
    timeout_total: int,
    segundos_sem_novas: int,
    intervalo_poll: int = 2,
) -> dict:
    inicio = time.time()
    ultimo_movimento = inicio
    ids_vistos = set()
    raws = []

    while (time.time() - inicio) < timeout_total:
        items = blip._obter_items_thread()
        novas = blip._extrair_novas_respostas(items, ultimo_id_antes)

        alterou = False
        for msg in novas:
            raw = msg.get("raw", {})
            msg_id = raw.get("id") or f"idx-{len(ids_vistos)}"
            if msg_id in ids_vistos:
                continue
            ids_vistos.add(msg_id)
            raws.append(raw)
            alterou = True

        if alterou:
            ultimo_movimento = time.time()
        elif ids_vistos and (time.time() - ultimo_movimento) >= segundos_sem_novas:
            break

        time.sleep(intervalo_poll)

    return {
        "raw": raws,
        "timeout": (time.time() - inicio) >= timeout_total,
    }


def _filtrar_raw_por_envio(raw_seq: list, envio_utc: datetime, janela_segundos: int) -> list:
    inicio = envio_utc - timedelta(seconds=2)
    fim = envio_utc + timedelta(seconds=janela_segundos)
    filtradas = []
    for item in raw_seq:
        dt = _parse_iso_datetime(item.get("date")) if isinstance(item, dict) else None
        if dt and inicio <= dt <= fim:
            filtradas.append(item)
    filtradas.sort(key=lambda x: _parse_iso_datetime(x.get("date")) or inicio)
    return filtradas


def _capturar_sent_do_thread_por_janela(blip: BlipClient, envio_utc: datetime, janela_segundos: int) -> list:
    """Fallback robusto: captura mensagens SENT direto do thread completo por janela temporal."""
    items = blip._obter_items_thread()
    sent = [it for it in items if (it.get("direction") or "").lower() == "sent"]
    return _filtrar_raw_por_envio(sent, envio_utc, janela_segundos)


def _raw_texto(item: dict) -> str:
    content = item.get("content")
    if isinstance(content, dict) and "text" in content:
        return str(content.get("text") or "")
    return str(content or "")


def _enviar_e_capturar(
    client: WhatsAppClient,
    blip: BlipClient,
    texto: str,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
) -> dict:
    ultimo_id_antes = blip._ultimo_id_bot(blip._obter_items_thread())
    envio_utc = datetime.now(timezone.utc)
    client.enviar_e_aguardar(texto, delay=6, exibir_resposta=False)
    captura = _capturar_respostas_ate_estabilizar(
        blip=blip,
        ultimo_id_antes=ultimo_id_antes,
        timeout_total=timeout_total,
        segundos_sem_novas=segundos_sem_novas,
        intervalo_poll=2,
    )
    raw_filtrado = _filtrar_raw_por_envio(captura.get("raw", []), envio_utc, janela_segundos)
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_do_thread_por_janela(blip, envio_utc, janela_segundos)
    return {
        "raw": captura.get("raw", []),
        "raw_filtrado": raw_filtrado,
        "texto_filtrado": "\n\n".join([_raw_texto(x) for x in raw_filtrado if _raw_texto(x)]).strip(),
        "timeout": captura.get("timeout", False),
        "envio_utc": envio_utc.isoformat(),
        "janela_segundos": janela_segundos,
    }


def _selecionar_lista_e_capturar(
    client: WhatsAppClient,
    blip: BlipClient,
    botao_lista: str,
    opcao_lista: str,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
) -> dict:
    ultimo_id_antes = blip._ultimo_id_bot(blip._obter_items_thread())
    envio_utc = datetime.now(timezone.utc)
    client.selecionar_opcao_lista(botao_lista, opcao_lista, timeout=30)
    captura = _capturar_respostas_ate_estabilizar(
        blip=blip,
        ultimo_id_antes=ultimo_id_antes,
        timeout_total=timeout_total,
        segundos_sem_novas=segundos_sem_novas,
        intervalo_poll=2,
    )
    raw_filtrado = _filtrar_raw_por_envio(captura.get("raw", []), envio_utc, janela_segundos)
    # Fallback 1: consulta direta no thread por janela, caso o delta não venha.
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_do_thread_por_janela(blip, envio_utc, janela_segundos)

    # Fallback 2: se ainda vazio, envia a opção como texto para destravar o fluxo.
    fallback_texto_enviado = False
    if not raw_filtrado:
        envio_fallback_utc = datetime.now(timezone.utc)
        ultimo_id_fallback_antes = blip._ultimo_id_bot(blip._obter_items_thread())
        client.enviar_e_aguardar(opcao_lista, delay=4, exibir_resposta=False)
        captura_fallback = _capturar_respostas_ate_estabilizar(
            blip=blip,
            ultimo_id_antes=ultimo_id_fallback_antes,
            timeout_total=120,
            segundos_sem_novas=15,
            intervalo_poll=2,
        )
        raw_filtrado = _filtrar_raw_por_envio(
            captura_fallback.get("raw", []),
            envio_fallback_utc,
            janela_segundos=180,
        )
        if not raw_filtrado:
            raw_filtrado = _capturar_sent_do_thread_por_janela(blip, envio_fallback_utc, 180)
        fallback_texto_enviado = True

    return {
        "raw": captura.get("raw", []),
        "raw_filtrado": raw_filtrado,
        "texto_filtrado": "\n\n".join([_raw_texto(x) for x in raw_filtrado if _raw_texto(x)]).strip(),
        "timeout": captura.get("timeout", False),
        "envio_utc": envio_utc.isoformat(),
        "janela_segundos": janela_segundos,
        "fallback_texto_enviado": fallback_texto_enviado,
    }


def _imprimir_seq(rotulo: str, raw_seq: list, mensagem_usuario: str = "", truncar: bool = False):
    print(f"\n[sequencia capturada] {rotulo}")
    idx = 1
    if mensagem_usuario:
        msg = mensagem_usuario.replace("\r", "").strip()
        print(f"\n  {idx}. → Usuário:")
        for ln in msg.split("\n"):
            print(f"      {ln}")
        idx += 1
    if not raw_seq:
        print("  nenhuma mensagem")
        return
    for item in raw_seq:
        texto = _raw_texto(item).replace("\r", "").strip()
        linhas = texto.split("\n")
        print(f"\n  {idx}. ← Bot:")
        for ln in linhas:
            if ln.strip():
                print(f"      {ln}")
        idx += 1


def _classificar_desfecho(raw_seq: list) -> dict:
    textos = "\n".join([_raw_texto(x).lower() for x in raw_seq])
    continuidade_fluxo = any(k in textos for k in [
        "valor a receber",
        "resumo da oferta",
        "você confirma que deseja contratar este empréstimo",
        "voce confirma que deseja contratar este emprestimo",
        "sim, confirmo",
        "agora não",
        "agora nao",
        "conta corrente",
        "conta poupança",
        "conta poupanca",
        "qual é o tipo de conta",
        "qual e o tipo de conta",
        "qual é o número da sua conta",
        "qual e o numero da sua conta",
    ])
    return {
        "oferta": any(k in textos for k in [
            "temos uma oportunidade para você",
            "temos uma oportunidade para voce",
            "resumo da oferta",
            "você confirma que deseja contratar este empréstimo",
            "voce confirma que deseja contratar este emprestimo",
            "escolher ofertas",
            "receba: r$",
        ]),
        "nao_elegivel": any(k in textos for k in [
            "desculpe, não consegui seguir com a sua solicitação",
            "desculpe, nao consegui seguir com a sua solicitacao",
            "ir para o menu",
            "finalizar conversa",
        ]),
        "continuidade_fluxo": continuidade_fluxo,
    }


def _imprimir_resultado_etapa(nome: str, ok: bool):
    print(f"[{'OK' if ok else 'FAIL'}] {nome}")


def _salvar_log(resultado: dict) -> str:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    opcao_slug = resultado.get("opcao_escolhida", "opcao").replace(" ", "_").replace("ç", "c")
    path = log_dir / f"raw_etapa_03_escolha_oferta_{opcao_slug}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    return str(path)


def _pausa_reset_manual(cenario: str):
    pausar = os.getenv("PAUSAR_PARA_RESET_MANUAL", "true").lower() in ("1", "true", "yes")
    if not pausar:
        print(f"\n[RESET MANUAL] ignorado (PAUSAR_PARA_RESET_MANUAL=false) | {cenario}")
        return
    print(f"\n[RESET MANUAL] Cenario: {cenario}")
    input("Faça o reset manual no Beholder e pressione Enter para continuar...")


@pytest.mark.integration
@pytest.mark.manual_reset
@pytest.mark.parametrize(
    "opcao_escolhida",
    [
        "Seguir com proteção Mega",
        "Seguir sem proteção",
    ],
    ids=["com_seguro", "sem_seguro"],
)
def test_etapa_03_escolha_oferta(opcao_escolhida: str):
    if not USE_WHATSAPP:
        pytest.skip("Defina USE_WHATSAPP=true no .env")
    if not WHATSAPP_NUMERO_ALVO:
        pytest.skip("Configure WHATSAPP_NUMERO_ALVO no .env")
    if not (BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID):
        pytest.skip("Configure BLIP_KEY, BLIP_BOT_ID e BLIP_USER_ID no .env")

    _pausa_reset_manual(f"etapa_03_escolha_oferta::{opcao_escolhida}")

    client = WhatsAppClient()
    blip = _blip_no_mesmo_usuario_whatsapp()
    _imprimir_contexto_blip(blip)
    try:
        pronto = client.iniciar()
        if not pronto:
            pytest.fail("WhatsApp Web nao pronto. Escaneie o QR e rode o teste novamente.")

        etapa1 = _enviar_e_capturar(
            client, blip, MENSAGEM_INICIAL,
            timeout_total=120, segundos_sem_novas=15, janela_segundos=120
        )
        _imprimir_seq("ETAPA 1 (mensagem inicial)", etapa1.get("raw_filtrado", []), mensagem_usuario=MENSAGEM_INICIAL)
        assert etapa1.get("raw_filtrado"), "ETAPA 1 falhou: sem retorno após mensagem inicial."

        etapa2 = _enviar_e_capturar(
            client, blip, RESPOSTA_AUTORIZACAO,
            timeout_total=240, segundos_sem_novas=25, janela_segundos=240
        )
        _imprimir_seq("ETAPA 2 (Li e autorizo)", etapa2.get("raw_filtrado", []), mensagem_usuario=RESPOSTA_AUTORIZACAO)
        assert etapa2.get("raw_filtrado"), "ETAPA 2 falhou: sem retorno após 'Li e autorizo'."

        etapa3 = _selecionar_lista_e_capturar(
            client, blip, BOTAO_LISTA, opcao_escolhida,
            timeout_total=240, segundos_sem_novas=25, janela_segundos=240
        )
        _imprimir_seq("ETAPA 3 (escolha de oferta)", etapa3.get("raw_filtrado", []), mensagem_usuario=f"{BOTAO_LISTA} -> {opcao_escolhida}")
        assert etapa3.get("raw_filtrado"), "ETAPA 3 falhou: sem retorno após selecionar opção."
    finally:
        client.fechar()

    desfecho = _classificar_desfecho(etapa3.get("raw_filtrado", []))
    print("\n[resultado por etapa]")
    _imprimir_resultado_etapa("ETAPA 3.1 - retorno após seleção", bool(etapa3.get("raw_filtrado")))
    _imprimir_resultado_etapa("ETAPA 3.2 - ramo oferta", desfecho["oferta"])
    _imprimir_resultado_etapa("ETAPA 3.3 - ramo não elegível", desfecho["nao_elegivel"])
    _imprimir_resultado_etapa("ETAPA 3.4 - continuidade de fluxo", desfecho["continuidade_fluxo"])

    resultado = {
        "cenario": "etapa_03_escolha_oferta",
        "opcao_escolhida": opcao_escolhida,
        "mensagem_inicial": MENSAGEM_INICIAL,
        "resposta_autorizacao": RESPOSTA_AUTORIZACAO,
        "captura_etapa1": etapa1,
        "captura_etapa2": etapa2,
        "captura_etapa3": etapa3,
        "desfecho_etapa3": desfecho,
        "thread_json_final": blip.obter_thread_json(),
    }
    _salvar_log(resultado)

    # Após escolher oferta, o esperado é continuar no ramo de oferta.
    assert not desfecho["nao_elegivel"], (
        "ETAPA 3 falhou: fluxo caiu no ramo de não elegível após seleção da oferta."
    )
    assert (desfecho["oferta"] or desfecho["continuidade_fluxo"]), (
        "ETAPA 3.2/3.4 falhou: não identificou oferta nem continuidade de fluxo após seleção."
    )
