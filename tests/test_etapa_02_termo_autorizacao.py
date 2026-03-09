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

MENSAGEM_INICIAL = (
    "Oi! Vim do aplicativo Carteira de Trabalho Digital para solicitar o "
    "Empréstimo Consignado Privado. Meu CPF é 06105813503. "
    "Meu código de solicitação é: 30776386"
)
RESPOSTA_AUTORIZACAO = "Li e autorizo"

# Evita poluição visual de logs repetidos "[DEBUG]" no terminal/HTML.
blip_module.DEBUG_BLIP = False


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


def _capturar_respostas_ate_estabilizar(
    blip: BlipClient,
    ultimo_id_antes: str,
    timeout_total: int = 120,
    segundos_sem_novas: int = 15,
    intervalo_poll: int = 2,
) -> dict:
    inicio = time.time()
    ultimo_movimento = inicio
    ids_vistos = set()
    textos = []
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
            if msg.get("texto"):
                textos.append(msg["texto"])
            alterou = True

        if alterou:
            ultimo_movimento = time.time()
        elif ids_vistos and (time.time() - ultimo_movimento) >= segundos_sem_novas:
            break

        time.sleep(intervalo_poll)

    return {
        "texto": "\n\n".join(textos).strip(),
        "mensagens": textos,
        "raw": raws,
        "timeout": (time.time() - inicio) >= timeout_total,
    }


def _parse_iso_datetime(value: str):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


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
    # Evita duplicar mensagem no output; a conversa completa já é exibida em [sequencia capturada].
    client.enviar_e_aguardar(texto, delay=6, exibir_resposta=False)
    captura = _capturar_respostas_ate_estabilizar(
        blip,
        ultimo_id_antes,
        timeout_total=timeout_total,
        segundos_sem_novas=segundos_sem_novas,
        intervalo_poll=2,
    )
    raw_filtrado = _filtrar_raw_por_envio(captura.get("raw", []), envio_utc, janela_segundos)
    captura["raw_filtrado"] = raw_filtrado
    captura["texto_filtrado"] = "\n\n".join([_raw_texto(x) for x in raw_filtrado if _raw_texto(x)]).strip()
    captura["envio_utc"] = envio_utc.isoformat()
    captura["janela_segundos"] = janela_segundos
    return captura


def _imprimir_seq(rotulo: str, raw_seq: list, mensagem_usuario: str = "", truncar: bool = False):
    print(f"\n[sequencia capturada] {rotulo}")
    if mensagem_usuario:
        msg = mensagem_usuario.replace("\r", "").strip()
        print("\n  1. → Usuário:")
        for ln in msg.split("\n"):
            print(f"      {ln}")
        base_idx = 2
    else:
        base_idx = 1
    if not raw_seq:
        print("  nenhuma mensagem")
        return
    for i, item in enumerate(raw_seq, base_idx):
        typ = item.get("type", "")
        content = item.get("content")
        if isinstance(content, dict) and "text" in content:
            texto = str(content.get("text") or "")
        else:
            texto = str(content or "")
        texto = texto.replace("\r", "").strip()
        print(f"\n  {i}. ← Bot:")
        for ln in texto.split("\n"):
            if ln.strip():
                print(f"      {ln}")


def _imprimir_seq_completa(rotulo: str, raw_seq: list, mensagem_usuario: str = ""):
    """Imprime todas as mensagens completas para aparecer no relatório HTML."""
    print(f"\n[sequencia completa] {rotulo}")
    if mensagem_usuario:
        print("\n  1. → Usuário:")
        print(mensagem_usuario)
        base_idx = 2
    else:
        base_idx = 1
    if not raw_seq:
        print("  nenhuma mensagem")
        return
    for i, item in enumerate(raw_seq, base_idx):
        typ = item.get("type", "")
        content = item.get("content")
        if isinstance(content, dict) and "text" in content:
            texto = str(content.get("text") or "")
        else:
            texto = str(content or "")
        print(f"\n  {i}. ← Bot:")
        if typ == "application/vnd.lime.media-link+json":
            print(f"[anexo] {texto}")
        else:
            print(texto)


def _imprimir_resultado_etapa(nome: str, ok: bool):
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {nome}")


def _validar_etapa1(raw_seq: list):
    welcome = False
    termo = False
    evidencia_termo_pdf = False
    consent = False
    for item in raw_seq:
        s = _raw_texto(item).lower()
        item_type = (item.get("type") or "").lower()
        content = item.get("content")
        if "banco bmg" in s and ("olá! aqui é" in s or "ola! aqui e" in s):
            welcome = True
        if "por favor, leia o termo de consentimento e scr" in s:
            termo = True
        # Não exige obrigatoriamente media-link; basta evidência de que o termo/PDF apareceu.
        if item_type == "application/vnd.lime.media-link+json":
            if isinstance(content, dict) and content.get("type") == "application/pdf":
                evidencia_termo_pdf = True
        if "termos de consentimento e scr - consignado privado.pdf" in s:
            evidencia_termo_pdf = True
        if "termo de consentimento e scr - consignado privado" in s:
            evidencia_termo_pdf = True
        if "para continuar, precisamos que você autorize" in s or "para continuar, precisamos que voce autorize" in s:
            consent = True
    return {
        "welcome": welcome,
        "termo": termo,
        "evidencia_termo_pdf": evidencia_termo_pdf,
        "consent": consent,
    }


def _classificar_etapa2(raw_seq: list):
    textos = "\n".join([_raw_texto(x).lower() for x in raw_seq])
    aguardando_api = "um instante enquanto confirmo os dados para sua segurança" in textos

    ramo_oferta = any(k in textos for k in [
        "temos uma oportunidade para você",
        "temos uma oportunidade para voce",
        "escolher ofertas",
        "receba: r$",
    ])
    ramo_nao_elegivel = any(k in textos for k in [
        "desculpe, não consegui seguir com a sua solicitação",
        "desculpe, nao consegui seguir com a sua solicitacao",
        "ir para o menu",
        "finalizar conversa",
    ])
    return {
        "aguardando_api": aguardando_api,
        "ramo_oferta": ramo_oferta,
        "ramo_nao_elegivel": ramo_nao_elegivel,
    }


def _salvar_log(resultado: dict) -> str:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"raw_etapa_02_li_autorizo_{ts}.json"
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
@pytest.mark.etapa2
@pytest.mark.manual_reset
def test_etapa_02_li_e_autorizo():
    if not USE_WHATSAPP:
        pytest.skip("Defina USE_WHATSAPP=true no .env")
    if not WHATSAPP_NUMERO_ALVO:
        pytest.skip("Configure WHATSAPP_NUMERO_ALVO no .env")
    if not (BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID):
        pytest.skip("Configure BLIP_KEY, BLIP_BOT_ID e BLIP_USER_ID no .env")

    _pausa_reset_manual("etapa_02_li_e_autorizo")

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
        _imprimir_seq(
            "ETAPA 1 (mensagem inicial)",
            etapa1.get("raw_filtrado", []),
            mensagem_usuario=MENSAGEM_INICIAL,
            truncar=False,
        )
        _imprimir_seq_completa(
            "ETAPA 1 (mensagem inicial)",
            etapa1.get("raw_filtrado", []),
            mensagem_usuario=MENSAGEM_INICIAL,
        )
        assert etapa1.get("raw_filtrado"), "ETAPA 1 falhou: nao houve resposta apos mensagem inicial."

        etapa2 = _enviar_e_capturar(
            client, blip, RESPOSTA_AUTORIZACAO,
            timeout_total=240, segundos_sem_novas=25, janela_segundos=240
        )
        _imprimir_seq(
            "ETAPA 2 (Li e autorizo)",
            etapa2.get("raw_filtrado", []),
            mensagem_usuario=RESPOSTA_AUTORIZACAO,
            truncar=False,
        )
        _imprimir_seq_completa(
            "ETAPA 2 (Li e autorizo)",
            etapa2.get("raw_filtrado", []),
            mensagem_usuario=RESPOSTA_AUTORIZACAO,
        )
        assert etapa2.get("raw_filtrado"), "ETAPA 2 falhou: nao houve resposta apos 'Li e autorizo'."
    finally:
        client.fechar()

    etapa1_val = _validar_etapa1(etapa1.get("raw_filtrado", []))
    etapa2_val = _classificar_etapa2(etapa2.get("raw_filtrado", []))

    print("\n[resultado por etapa]")
    _imprimir_resultado_etapa("ETAPA 1.1 - boas-vindas", etapa1_val["welcome"])
    _imprimir_resultado_etapa("ETAPA 1.2 - termo de consentimento", etapa1_val["termo"])
    _imprimir_resultado_etapa("ETAPA 1.3 - evidencia do termo/PDF", etapa1_val["evidencia_termo_pdf"])
    _imprimir_resultado_etapa("ETAPA 1.4 - bloco de autorizacao", etapa1_val["consent"])
    _imprimir_resultado_etapa("ETAPA 2.1 - retorno de processamento", etapa2_val["aguardando_api"])
    _imprimir_resultado_etapa("ETAPA 2.2 - ramo oferta", etapa2_val["ramo_oferta"])
    _imprimir_resultado_etapa("ETAPA 2.3 - ramo nao elegivel", etapa2_val["ramo_nao_elegivel"])

    assert etapa1_val["welcome"], "ETAPA 1.1 falhou: boas-vindas nao encontrada."
    assert etapa1_val["termo"], "ETAPA 1.2 falhou: termo de consentimento nao encontrado."
    assert etapa1_val["evidencia_termo_pdf"], "ETAPA 1.3 falhou: sem evidencia do termo/PDF no log."
    assert etapa1_val["consent"], "ETAPA 1.4 falhou: bloco de autorizacao nao encontrado."
    assert etapa2_val["aguardando_api"], "ETAPA 2.1 falhou: mensagem de processamento nao encontrada."
    assert (etapa2_val["ramo_oferta"] ^ etapa2_val["ramo_nao_elegivel"]), (
        "ETAPA 2.2/2.3 falhou: esperado exatamente um desfecho (oferta OU nao elegivel)."
    )

    resultado = {
        "cenario": "etapa_02_li_e_autorizo",
        "mensagem_inicial": MENSAGEM_INICIAL,
        "resposta_autorizacao": RESPOSTA_AUTORIZACAO,
        "captura_etapa1": etapa1,
        "captura_etapa2": etapa2,
        "validacao_etapa1": etapa1_val,
        "validacao_etapa2": etapa2_val,
        "thread_json_final": blip.obter_thread_json(),
    }
    _salvar_log(resultado)
