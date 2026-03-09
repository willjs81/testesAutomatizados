import json
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

# Evita logs repetidos "[DEBUG]" no terminal/HTML.
blip_module.DEBUG_BLIP = False

# Ajuste esta mensagem conforme o gatilho oficial do seu bot.
MENSAGEM_INICIAL = (
    "Oi! Vim do aplicativo Carteira de Trabalho Digital para solicitar o "
    "Empréstimo Consignado Privado. Meu CPF é 06105813503. "
    "Meu código de solicitação é: 30776386"
)

# Palavras-chave amplas para validar que o bot entrou no fluxo esperado.
PALAVRAS_CHAVE_ESPERADAS = [
    "banco bmg",
    "termo de consentimento",
    "autoriza",
    "consulta de dados",
]


def _salvar_raw_em_log(resultado: dict) -> str:
    """Salva retorno bruto para analise QA."""
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"raw_etapa_inicial_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    return str(path)


def _capturar_thread_bruto_whatsapp() -> dict:
    """Busca thread bruto da Blip usando identidade do WhatsApp."""
    blip = BlipClient()
    user_id = WHATSAPP_MEU_NUMERO or BLIP_USER_ID
    user_id = user_id.replace("+", "").replace(" ", "")
    if "@" not in user_id:
        user_id = f"{user_id}@{BLIP_USER_DOMAIN or 'wa.gw.msging.net'}"
    blip.user_identity = user_id
    return blip.obter_thread_json()


def _blip_no_mesmo_usuario_whatsapp() -> BlipClient:
    """Instancia BlipClient apontando para a mesma identidade usada no WhatsApp."""
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


def _capturar_apenas_respostas_novas(blip: BlipClient, ultimo_id_antes: str, timeout: int = 90) -> dict:
    """
    Captura apenas mensagens novas do bot após o envio.
    Evita pegar histórico antigo do thread.
    """
    inicio = time.time()
    while (time.time() - inicio) < timeout:
        items = blip._obter_items_thread()
        ultimo_id_agora = blip._ultimo_id_bot(items)
        if ultimo_id_agora and ultimo_id_agora != ultimo_id_antes:
            novas = blip._extrair_novas_respostas(items, ultimo_id_antes)
            textos = [x.get("texto", "") for x in novas if x.get("texto")]
            raws = [x.get("raw", {}) for x in novas]
            return {
                "texto": "\n\n".join(textos).strip(),
                "mensagens": textos,
                "raw": raws,
            }
        time.sleep(2)

    return {"texto": "", "mensagens": [], "raw": [], "timeout": True}


def _capturar_respostas_ate_estabilizar(
    blip: BlipClient,
    ultimo_id_antes: str,
    timeout_total: int = 120,
    segundos_sem_novas: int = 15,
    intervalo_poll: int = 2,
) -> dict:
    """
    Captura respostas novas e só encerra quando o bot parar de enviar mensagens
    por `segundos_sem_novas` segundos.
    """
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
    """Converte ISO com ou sem sufixo Z para datetime UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _filtrar_raw_por_janela_envio(
    thread_json: dict,
    envio_utc: datetime,
    janela_segundos: int = 90,
) -> list:
    """
    Filtra apenas mensagens SENT após o envio (janela curta).
    Evita histórico antigo na análise da etapa inicial.
    """
    items = (((thread_json or {}).get("resource") or {}).get("items") or [])
    inicio = envio_utc
    fim = envio_utc + timedelta(seconds=janela_segundos)
    filtradas = []

    for item in items:
        if (item.get("direction") or "").lower() != "sent":
            continue
        dt = _parse_iso_datetime(item.get("date"))
        if dt and inicio <= dt <= fim:
            filtradas.append(item)

    filtradas.sort(key=lambda x: _parse_iso_datetime(x.get("date")) or inicio)
    return filtradas


def _filtrar_sent_apos_mensagem_recebida(
    thread_json: dict,
    mensagem_enviada: str,
    envio_utc: datetime,
    janela_segundos: int = 90,
) -> list:
    """
    Encontra a última mensagem RECEIVED igual à mensagem enviada (após envio_utc)
    e retorna apenas SENT dentro da janela após esse marco.
    """
    items = (((thread_json or {}).get("resource") or {}).get("items") or [])
    recebidas = []
    alvo = (mensagem_enviada or "").strip()

    for item in items:
        if (item.get("direction") or "").lower() != "received":
            continue
        if (item.get("content") or "").strip() != alvo:
            continue
        dt = _parse_iso_datetime(item.get("date"))
        if dt and dt >= (envio_utc - timedelta(seconds=5)):
            recebidas.append((dt, item))

    if not recebidas:
        return []

    marco_dt, _ = max(recebidas, key=lambda x: x[0])
    fim = marco_dt + timedelta(seconds=janela_segundos)
    filtradas = []
    for item in items:
        if (item.get("direction") or "").lower() != "sent":
            continue
        dt = _parse_iso_datetime(item.get("date"))
        if dt and marco_dt <= dt <= fim:
            filtradas.append(item)

    filtradas.sort(key=lambda x: _parse_iso_datetime(x.get("date")) or marco_dt)
    return filtradas


def _texto_de_raw(item: dict) -> str:
    """Extrai texto para validação a partir de item raw."""
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        return str(content)
    return str(content or "")


def _imprimir_sequencia_terminal(raw_seq: list):
    """Imprime no terminal a sequência capturada para conferência rápida."""
    if not raw_seq:
        print("\n[sequencia capturada] nenhuma mensagem")
        return
    print("\n[sequencia capturada no teste]")
    for i, item in enumerate(raw_seq, 1):
        typ = item.get("type", "")
        texto = _texto_de_raw(item).replace("\r", "").replace("\n", " ").strip()
        if len(texto) > 180:
            texto = texto[:180] + "..."
        print(f"  {i}. ({typ}) {texto}")


def _conteudo_raw_texto(item: dict) -> str:
    """Retorna conteúdo textual normalizado para validações."""
    return _texto_de_raw(item).lower()


def _marcar_etapa(nome: str, ok: bool):
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {nome}")


@pytest.mark.integration
def test_etapa_inicial_envio_whatsapp_retorno_bruto():
    if not USE_WHATSAPP:
        pytest.skip("Defina USE_WHATSAPP=true no .env")
    if not WHATSAPP_NUMERO_ALVO:
        pytest.skip("Configure WHATSAPP_NUMERO_ALVO no .env")
    if not (BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID):
        pytest.skip("Configure BLIP_KEY, BLIP_BOT_ID e BLIP_USER_ID no .env")

    client = WhatsAppClient()
    blip = _blip_no_mesmo_usuario_whatsapp()
    _imprimir_contexto_blip(blip)
    ultimo_id_antes = blip._ultimo_id_bot(blip._obter_items_thread())
    envio_utc = None
    try:
        pronto = client.iniciar()
        if not pronto:
            pytest.fail(
                "WhatsApp Web nao pronto. Escaneie o QR e rode o teste novamente."
            )

        # Envio real via WhatsApp.
        envio_utc = datetime.now(timezone.utc)
        client.enviar_e_aguardar(
            MENSAGEM_INICIAL,
            delay=6,
            exibir_resposta=True,
        )
        captura = _capturar_respostas_ate_estabilizar(
            blip,
            ultimo_id_antes,
            timeout_total=120,
            segundos_sem_novas=15,
            intervalo_poll=2,
        )
        resposta = captura.get("texto", "")
        raw_thread = _capturar_thread_bruto_whatsapp()
    finally:
        client.fechar()

    raw_janela = _filtrar_raw_por_janela_envio(
        raw_thread,
        envio_utc or datetime.now(timezone.utc),
        janela_segundos=90,
    )
    raw_ancorado = _filtrar_sent_apos_mensagem_recebida(
        raw_thread,
        MENSAGEM_INICIAL,
        envio_utc or datetime.now(timezone.utc),
        janela_segundos=90,
    )
    textos_janela = [_texto_de_raw(x) for x in raw_janela]
    texto_janela = "\n\n".join([t for t in textos_janela if t]).strip()
    textos_ancorado = [_texto_de_raw(x) for x in raw_ancorado]
    texto_ancorado = "\n\n".join([t for t in textos_ancorado if t]).strip()

    resultado = {
        "canal": "whatsapp",
        "mensagem_enviada": MENSAGEM_INICIAL,
        "texto_capturado": resposta,
        "mensagens_capturadas": captura.get("mensagens", []),
        "raw_capturado": captura.get("raw", []),
        "texto_filtrado_janela_envio": texto_janela,
        "raw_filtrado_janela_envio": raw_janela,
        "texto_filtrado_ancorado_received": texto_ancorado,
        "raw_filtrado_ancorado_received": raw_ancorado,
        "thread_json": raw_thread,
    }

    # Estrutura minima esperada do log.
    assert isinstance(resultado, dict)
    assert "texto_capturado" in resultado
    assert "thread_json" in resultado

    texto = (resultado.get("texto_filtrado_ancorado_received") or "").strip()
    if not texto:
        texto = (resultado.get("texto_filtrado_janela_envio") or "").strip()
    if not texto:
        texto = (resultado.get("texto_capturado") or "").strip()
    assert texto, "Bot nao retornou mensagens novas na etapa inicial"

    thread_json = resultado.get("thread_json") or {}
    assert isinstance(thread_json, dict), "thread_json deve ser dict"

    texto_lower = texto.lower()
    assert any(p in texto_lower for p in PALAVRAS_CHAVE_ESPERADAS), (
        "Retorno inicial nao contem palavras esperadas do fluxo. "
        f"Texto recebido: {texto[:500]}"
    )

    seq_para_terminal = (
        resultado.get("raw_filtrado_ancorado_received")
        or resultado.get("raw_filtrado_janela_envio")
        or resultado.get("raw_capturado")
        or []
    )
    _imprimir_sequencia_terminal(seq_para_terminal)

    # Validação visual por etapa (QA-friendly)
    etapa_welcome = False
    etapa_termo = False
    etapa_pdf = False
    etapa_consentimento = False

    for item in seq_para_terminal:
        conteudo = _conteudo_raw_texto(item)
        item_type = (item.get("type") or "").lower()
        content_obj = item.get("content") if isinstance(item, dict) else None

        if "banco bmg" in conteudo and ("olá! aqui é" in conteudo or "ola! aqui e" in conteudo):
            etapa_welcome = True
        if "por favor, leia o termo de consentimento e scr" in conteudo:
            etapa_termo = True
        if item_type == "application/vnd.lime.media-link+json":
            if isinstance(content_obj, dict) and content_obj.get("type") == "application/pdf":
                etapa_pdf = True
        if "para continuar, precisamos que você autorize" in conteudo or "para continuar, precisamos que voce autorize" in conteudo:
            etapa_consentimento = True

    print("\n[resultado por etapa]")
    _marcar_etapa("ETAPA 1.1 - boas-vindas", etapa_welcome)
    _marcar_etapa("ETAPA 1.2 - termo de consentimento", etapa_termo)
    _marcar_etapa("ETAPA 1.3 - envio do PDF", etapa_pdf)
    _marcar_etapa("ETAPA 1.4 - bloco de autorizacao", etapa_consentimento)

    assert etapa_welcome, "ETAPA 1.1 falhou: boas-vindas nao encontrada."
    assert etapa_termo, "ETAPA 1.2 falhou: mensagem do termo nao encontrada."
    assert etapa_pdf, "ETAPA 1.3 falhou: PDF nao foi capturado."
    assert etapa_consentimento, "ETAPA 1.4 falhou: bloco de autorizacao nao encontrado."

    log_path = _salvar_raw_em_log(resultado)
    print(f"\n[raw salvo em] {log_path}")
