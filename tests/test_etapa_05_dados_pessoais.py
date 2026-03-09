import json
import os
import re
import random
import time
import unicodedata
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

blip_module.DEBUG_BLIP = False

def _montar_mensagem_inicial() -> str:
    cpf = (os.getenv("TESTE_CPF", "") or "06105813503").strip()
    codigo = (os.getenv("TESTE_CODIGO_SOLICITACAO", "") or "30776386").strip()
    return (
        "Oi! Vim do aplicativo Carteira de Trabalho Digital para solicitar o "
        f"Empréstimo Consignado Privado. Meu CPF é {cpf}. "
        f"Meu código de solicitação é: {codigo}"
    )


MENSAGEM_INICIAL = _montar_mensagem_inicial()
RESPOSTA_AUTORIZACAO = "Li e autorizo"
BOTAO_LISTA = "Escolher ofertas"
OPCAO_COM_SEGURO = "Seguir com proteção Mega"
CONFIRMACAO = "Sim, confirmo"


def _carregar_dados_teste() -> dict:
    path = Path(__file__).resolve().parents[1] / "dados_teste.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _blip_no_mesmo_usuario_whatsapp() -> BlipClient:
    blip = BlipClient()
    user_id = WHATSAPP_MEU_NUMERO or BLIP_USER_ID
    user_id = user_id.replace("+", "").replace(" ", "")
    if "@" not in user_id:
        user_id = f"{user_id}@{BLIP_USER_DOMAIN or 'wa.gw.msging.net'}"
    blip.user_identity = user_id
    return blip


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

def _normalizar_texto(s: str) -> str:
    if not s:
        return ""
    # Corrige mojibake comum de UTF-8 lido como latin-1/cp1252.
    if any(tok in s for tok in ("Ã", "Â", "â")):
        try:
            s = s.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
        except Exception:
            pass
    s = re.sub(r"<[^>]+>", " ", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # Mantem somente caracteres basicos para reduzir variacao de encoding.
    s = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in s)
    s = s.lower()
    return re.sub(r"\s+", " ", s).strip()



def _obter_items_thread_com_retry(blip: BlipClient, tentativas: int = 5, espera: float = 2.0) -> list:
    ultimo_erro = None
    for _ in range(tentativas):
        try:
            return blip._obter_items_thread()
        except Exception as exc:
            ultimo_erro = exc
            time.sleep(espera)
    if ultimo_erro:
        raise ultimo_erro
    return []


def _ultimo_id_bot_seguro(blip: BlipClient) -> str:
    items = _obter_items_thread_com_retry(blip)
    return blip._ultimo_id_bot(items)


def _capturar_respostas_ate_estabilizar(
    blip: BlipClient,
    ultimo_id_antes: str,
    timeout_total: int,
    segundos_sem_novas: int,
    intervalo_poll: int = 2,
) -> list:
    inicio = time.time()
    ultimo_movimento = inicio
    ids_vistos = set()
    raws = []

    while (time.time() - inicio) < timeout_total:
        items = _obter_items_thread_com_retry(blip)
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
    return raws


def _capturar_sent_thread_por_janela(blip: BlipClient, envio_utc: datetime, janela_segundos: int) -> list:
    items = _obter_items_thread_com_retry(blip)
    sent = [it for it in items if (it.get("direction") or "").lower() == "sent"]
    return _filtrar_raw_por_envio(sent, envio_utc, janela_segundos)


def _acao_texto_e_captura(
    client: WhatsAppClient,
    blip: BlipClient,
    texto: str,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
) -> dict:
    ultimo_id_antes = _ultimo_id_bot_seguro(blip)
    envio_utc = datetime.now(timezone.utc)
    client.enviar_e_aguardar(texto, delay=6, exibir_resposta=False)
    raw = _capturar_respostas_ate_estabilizar(
        blip, ultimo_id_antes, timeout_total=timeout_total, segundos_sem_novas=segundos_sem_novas
    )
    raw_filtrado = _filtrar_raw_por_envio(raw, envio_utc, janela_segundos)
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_utc, janela_segundos)
    return {"raw_filtrado": raw_filtrado}


def _acao_lista_e_captura(
    client: WhatsAppClient,
    blip: BlipClient,
    botao_lista: str,
    opcao_lista: str,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
) -> dict:
    ultimo_id_antes = _ultimo_id_bot_seguro(blip)
    envio_utc = datetime.now(timezone.utc)

    fallback_texto_enviado = False
    try:
        client.selecionar_opcao_lista(botao_lista, opcao_lista, timeout=30)
    except Exception:
        fallback_texto_enviado = True
        client.enviar_e_aguardar(opcao_lista, delay=4, exibir_resposta=False)

    raw = _capturar_respostas_ate_estabilizar(
        blip, ultimo_id_antes, timeout_total=timeout_total, segundos_sem_novas=segundos_sem_novas
    )
    raw_filtrado = _filtrar_raw_por_envio(raw, envio_utc, janela_segundos)
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_utc, janela_segundos)

    if not raw_filtrado and not fallback_texto_enviado:
        envio_fallback_utc = datetime.now(timezone.utc)
        ultimo_id_fallback_antes = _ultimo_id_bot_seguro(blip)
        client.enviar_e_aguardar(opcao_lista, delay=4, exibir_resposta=False)
        raw_fallback = _capturar_respostas_ate_estabilizar(
            blip, ultimo_id_fallback_antes, timeout_total=120, segundos_sem_novas=15
        )
        raw_filtrado = _filtrar_raw_por_envio(raw_fallback, envio_fallback_utc, 180)
        if not raw_filtrado:
            raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_fallback_utc, 180)
        fallback_texto_enviado = True

    return {"raw_filtrado": raw_filtrado, "fallback_texto_enviado": fallback_texto_enviado}


def _clicar_opcao_e_captura(
    client: WhatsAppClient,
    blip: BlipClient,
    texto_opcao: str,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
) -> dict:
    ultimo_id_antes = _ultimo_id_bot_seguro(blip)
    envio_utc = datetime.now(timezone.utc)

    clicou_fallback_texto = False
    try:
        client.clicar_opcao_por_texto(texto_opcao, timeout=20)
    except Exception:
        clicou_fallback_texto = True
        client.enviar_e_aguardar(texto_opcao, delay=4, exibir_resposta=False)

    raw = _capturar_respostas_ate_estabilizar(
        blip, ultimo_id_antes, timeout_total=timeout_total, segundos_sem_novas=segundos_sem_novas
    )
    raw_filtrado = _filtrar_raw_por_envio(raw, envio_utc, janela_segundos)
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_utc, janela_segundos)
    return {"raw_filtrado": raw_filtrado, "fallback_texto": clicou_fallback_texto}


def _confirmar_endereco_e_captura(
    client: WhatsAppClient,
    blip: BlipClient,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
) -> dict:
    ultimo_id_antes = _ultimo_id_bot_seguro(blip)
    envio_utc = datetime.now(timezone.utc)

    opcoes = [
        "Sim, confirmo",
        "Sim, confirmar",
        "Sim, estÃ¡ correto",
        "Sim, esta correto",
        "Confirmar",
        "Sim",
    ]
    confirmou_por_click = False
    for op in opcoes:
        try:
            client.clicar_opcao_por_texto(op, timeout=6)
            confirmou_por_click = True
            break
        except Exception:
            continue

    if not confirmou_por_click:
        client.enviar_e_aguardar("Sim, confirmo", delay=4, exibir_resposta=False)

    raw = _capturar_respostas_ate_estabilizar(
        blip, ultimo_id_antes, timeout_total=timeout_total, segundos_sem_novas=segundos_sem_novas
    )
    raw_filtrado = _filtrar_raw_por_envio(raw, envio_utc, janela_segundos)
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_utc, janela_segundos)
    return {"raw_filtrado": raw_filtrado, "confirmou_por_click": confirmou_por_click}


def _imprimir_seq(rotulo: str, raw_seq: list, mensagem_usuario: str = ""):
    print(f"\n[sequencia capturada] {rotulo}")
    idx = 1
    if mensagem_usuario:
        print(f"\n  {idx}. -> Usuario:")
        for ln in mensagem_usuario.replace("\r", "").split("\n"):
            print(f"      {ln}")
        idx += 1
    if not raw_seq:
        print("  nenhuma mensagem")
        return
    for item in raw_seq:
        texto = _raw_texto(item).replace("\r", "").strip()
        print(f"\n  {idx}. <- Bot:")
        for ln in texto.split("\n"):
            if ln.strip():
                print(f"      {ln}")
        idx += 1


def _imprimir_resultado_etapa(nome: str, ok: bool):
    print(f"[{'OK' if ok else 'FAIL'}] {nome}")


def _classificar_fluxo_dados(raw_seq: list) -> dict:
    textos = " ".join([_normalizar_texto(_raw_texto(x)) for x in raw_seq])
    return {
        "pediu_email": ("e-mail" in textos or "email" in textos or "e mail" in textos),
        "pediu_cep": ("cep" in textos),
        "pediu_confirmacao_endereco": any(k in textos for k in [
            "voce confirma o seu endereco",
            "confirma o seu endereco",
            "voce confirme o seu endereco",
            "confirme o seu endereco",
        ]),
        "pediu_numero_casa": any(k in textos for k in [
            "numero da casa",
            "numero da sua casa",
            "qual o numero da sua casa",
            "qual e o numero da sua casa",
            "no da casa",
            "n da casa",
        ]),
        "pediu_complemento": any(k in textos for k in [
            "tem complemento",
            "nao tenho",
            "não tenho",
            "endereco tem complemento",
            "endereco tem complemento",
            "complemento",
        ]),
        "chegou_dados_bancarios": any(k in textos for k in [
            "tipo de conta",
            "conta corrente",
            "conta poupanca",
            "numero da sua conta",
            "agencia",
            "dados bancarios",
            "qual e o banco",
            "qual e o banco em que voce deseja receber o beneficio",
            "escolha uma opcao",
            "escolher_banco-",
            "banco bmg",
            "banco do brasil",
            "itau",
            "caixa economica",
            "bradesco",
            "santander",
        ]),
        "ramo_negativo": any(k in textos for k in [
            "desculpe, nao consegui seguir com a sua solicitacao",
            "finalizar conversa",
            "ir para o menu",
        ]),
    }

def _pausa_reset_manual(cenario: str):
    pausar = os.getenv("PAUSAR_PARA_RESET_MANUAL", "true").lower() in ("1", "true", "yes")
    if not pausar:
        print(f"\n[RESET MANUAL] ignorado (PAUSAR_PARA_RESET_MANUAL=false) | {cenario}")
        return
    print(f"\n[RESET MANUAL] Cenario: {cenario}")
    input("FaÃ§a o reset manual no Beholder e pressione Enter para continuar...")


def _salvar_log(resultado: dict) -> None:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"raw_etapa_05_dados_pessoais_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)


def _obter_thread_json_seguro(blip: BlipClient):
    try:
        return blip.obter_thread_json()
    except Exception as exc:
        return {"erro_thread_json": str(exc)}


@pytest.mark.integration
@pytest.mark.manual_reset
def test_etapa_05_dados_pessoais_ate_bancarios():
    if not USE_WHATSAPP:
        pytest.skip("Defina USE_WHATSAPP=true no .env")
    if not WHATSAPP_NUMERO_ALVO:
        pytest.skip("Configure WHATSAPP_NUMERO_ALVO no .env")
    if not (BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID):
        pytest.skip("Configure BLIP_KEY, BLIP_BOT_ID e BLIP_USER_ID no .env")

    dados = _carregar_dados_teste()
    email = str(dados.get("email", "")).strip()
    cep = str(dados.get("cep", "")).strip()
    numero_casa = str(dados.get("numero_casa", "")).strip()
    complemento = str(dados.get("complemento", "casa 6 fundos")).strip()[:30]

    assert email, "dados_teste.json sem 'email'."
    assert cep, "dados_teste.json sem 'cep'."
    assert numero_casa, "dados_teste.json sem 'numero_casa'."

    _pausa_reset_manual("etapa_05_dados_pessoais_ate_bancarios")

    client = WhatsAppClient()
    blip = _blip_no_mesmo_usuario_whatsapp()
    try:
        if not client.iniciar():
            pytest.fail("WhatsApp Web nao pronto. Escaneie o QR e rode o teste novamente.")

        etapa1 = _acao_texto_e_captura(client, blip, MENSAGEM_INICIAL, 120, 15, 120)
        _imprimir_seq("ETAPA 1", etapa1["raw_filtrado"], mensagem_usuario=MENSAGEM_INICIAL)
        if not etapa1["raw_filtrado"]:
            print("  [info] ETAPA 1 sem retorno na primeira tentativa, repetindo envio...")
            etapa1 = _acao_texto_e_captura(client, blip, MENSAGEM_INICIAL, 180, 25, 180)
            _imprimir_seq("ETAPA 1 (retry)", etapa1["raw_filtrado"], mensagem_usuario=MENSAGEM_INICIAL)
        assert etapa1["raw_filtrado"], "ETAPA 1 falhou."

        etapa2 = _acao_texto_e_captura(client, blip, RESPOSTA_AUTORIZACAO, 240, 25, 240)
        _imprimir_seq("ETAPA 2", etapa2["raw_filtrado"], mensagem_usuario=RESPOSTA_AUTORIZACAO)
        assert etapa2["raw_filtrado"], "ETAPA 2 falhou."

        etapa3 = _acao_lista_e_captura(client, blip, BOTAO_LISTA, OPCAO_COM_SEGURO, 240, 25, 240)
        _imprimir_seq("ETAPA 3", etapa3["raw_filtrado"], mensagem_usuario=f"{BOTAO_LISTA} -> {OPCAO_COM_SEGURO}")
        assert etapa3["raw_filtrado"], "ETAPA 3 falhou."

        etapa4 = _clicar_opcao_e_captura(client, blip, CONFIRMACAO, 240, 25, 240)
        _imprimir_seq("ETAPA 4", etapa4["raw_filtrado"], mensagem_usuario=CONFIRMACAO)
        assert etapa4["raw_filtrado"], "ETAPA 4 falhou."

        etapa5 = _acao_texto_e_captura(client, blip, email, 180, 18, 180)
        _imprimir_seq("ETAPA 5 (email)", etapa5["raw_filtrado"], mensagem_usuario=email)
        assert etapa5["raw_filtrado"], "ETAPA 5 falhou."

        etapa6 = _acao_texto_e_captura(client, blip, cep, 180, 18, 180)
        _imprimir_seq("ETAPA 6 (cep)", etapa6["raw_filtrado"], mensagem_usuario=cep)
        assert etapa6["raw_filtrado"], "ETAPA 6 falhou."

        etapa6_val = _classificar_fluxo_dados(etapa6["raw_filtrado"])
        if etapa6_val["pediu_confirmacao_endereco"]:
            etapa7 = _confirmar_endereco_e_captura(client, blip, 180, 18, 180)
            _imprimir_seq("ETAPA 7 (confirmacao endereco)", etapa7["raw_filtrado"], mensagem_usuario="Sim, confirmo")
            if not etapa7["raw_filtrado"]:
                print("  [info] ETAPA 7 sem retorno na primeira tentativa, repetindo confirmacao...")
                etapa7 = _confirmar_endereco_e_captura(client, blip, 240, 25, 240)
                _imprimir_seq("ETAPA 7 (retry confirmacao endereco)", etapa7["raw_filtrado"], mensagem_usuario="Sim, confirmo")
            assert etapa7["raw_filtrado"], "ETAPA 7 falhou."
        else:
            etapa7 = {"raw_filtrado": []}

        etapa8 = _acao_texto_e_captura(client, blip, numero_casa, 180, 18, 180)
        _imprimir_seq("ETAPA 8 (numero casa)", etapa8["raw_filtrado"], mensagem_usuario=numero_casa)
        assert etapa8["raw_filtrado"], "ETAPA 8 falhou."

        etapa8_val = _classificar_fluxo_dados(etapa8["raw_filtrado"])
        if etapa8_val["pediu_complemento"]:
            usar_complemento = random.choice([True, False])
            if usar_complemento:
                etapa9a = _clicar_opcao_e_captura(client, blip, "Tem complemento", 180, 18, 180)
                _imprimir_seq("ETAPA 9A (opcao complemento)", etapa9a["raw_filtrado"], mensagem_usuario="Tem complemento")
                etapa9b = _acao_texto_e_captura(client, blip, complemento, 180, 18, 180)
                _imprimir_seq("ETAPA 9B (texto complemento)", etapa9b["raw_filtrado"], mensagem_usuario=complemento)
                etapa9 = {"raw_filtrado": (etapa9a["raw_filtrado"] + etapa9b["raw_filtrado"])}
            else:
                etapa9 = _clicar_opcao_e_captura(client, blip, "Não tenho", 180, 18, 180)
                _imprimir_seq("ETAPA 9 (sem complemento)", etapa9["raw_filtrado"], mensagem_usuario="Não tenho")
            assert etapa9["raw_filtrado"], "ETAPA 9 falhou."
        else:
            etapa9 = {"raw_filtrado": []}

        etapa9_val = _classificar_fluxo_dados(etapa9["raw_filtrado"])
        if etapa9_val["pediu_confirmacao_endereco"]:
            etapa10 = _confirmar_endereco_e_captura(client, blip, 180, 18, 180)
            _imprimir_seq("ETAPA 10 (confirmacao final endereco)", etapa10["raw_filtrado"], mensagem_usuario="Sim, confirmo")
            if not etapa10["raw_filtrado"]:
                print("  [info] ETAPA 10 sem retorno na primeira tentativa, repetindo confirmacao final...")
                etapa10 = _confirmar_endereco_e_captura(client, blip, 240, 25, 240)
                _imprimir_seq("ETAPA 10 (retry confirmacao final endereco)", etapa10["raw_filtrado"], mensagem_usuario="Sim, confirmo")
            assert etapa10["raw_filtrado"], "ETAPA 10 falhou."
        else:
            etapa10 = {"raw_filtrado": etapa9["raw_filtrado"]}
    finally:
        client.fechar()

    valid_4 = _classificar_fluxo_dados(etapa4["raw_filtrado"])
    valid_5 = _classificar_fluxo_dados(etapa5["raw_filtrado"])
    valid_6 = _classificar_fluxo_dados(etapa6["raw_filtrado"])
    valid_7 = _classificar_fluxo_dados(etapa7["raw_filtrado"])
    valid_8 = _classificar_fluxo_dados(etapa8["raw_filtrado"])
    valid_9 = _classificar_fluxo_dados(etapa9["raw_filtrado"])
    valid_10 = _classificar_fluxo_dados(etapa10["raw_filtrado"])

    print("\n[resultado por etapa]")
    _imprimir_resultado_etapa("ETAPA 1.1 - pediu e-mail", valid_4["pediu_email"])
    _imprimir_resultado_etapa("ETAPA 2.1 - pediu CEP", valid_5["pediu_cep"])
    _imprimir_resultado_etapa(
        "ETAPA 3.1 - pediu confirmacao endereco ou numero da casa",
        valid_6["pediu_confirmacao_endereco"] or valid_6["pediu_numero_casa"],
    )
    _imprimir_resultado_etapa(
        "ETAPA 4.1 - confirmou endereco (quando solicitado)",
        (not valid_6["pediu_confirmacao_endereco"]) or bool(etapa7["raw_filtrado"]),
    )
    _imprimir_resultado_etapa(
        "ETAPA 5.1 - pediu complemento ou ja chegou em dados bancarios",
        valid_8["pediu_complemento"] or valid_8["chegou_dados_bancarios"],
    )
    _imprimir_resultado_etapa(
        "ETAPA 6.1 - confirmou endereco final (quando solicitado)",
        (not valid_9["pediu_confirmacao_endereco"]) or bool(etapa10["raw_filtrado"]),
    )
    _imprimir_resultado_etapa("ETAPA 7.1 - chegou em dados bancarios", valid_10["chegou_dados_bancarios"])
    _imprimir_resultado_etapa("ETAPA 7.2 - sem ramo negativo", not valid_10["ramo_negativo"])
    assert valid_4["pediu_email"], "ETAPA 1.1 falhou: nao pediu e-mail."
    assert valid_5["pediu_cep"], "ETAPA 2.1 falhou: nao pediu CEP."
    assert (
        valid_6["pediu_confirmacao_endereco"] or valid_6["pediu_numero_casa"]
    ), "ETAPA 3.1 falhou: nao pediu confirmacao de endereco nem numero da casa."
    if valid_6["pediu_confirmacao_endereco"]:
        assert etapa7["raw_filtrado"], "ETAPA 4.1 falhou: sem retorno apos confirmar endereco."
    assert (
        valid_8["pediu_complemento"] or valid_8["chegou_dados_bancarios"]
    ), "ETAPA 5.1 falhou: nao pediu complemento nem chegou em dados bancarios."
    if valid_9["pediu_confirmacao_endereco"]:
        assert etapa10["raw_filtrado"], "ETAPA 6.1 falhou: sem retorno apos confirmar endereco final."
    assert valid_10["chegou_dados_bancarios"], "ETAPA 7.1 falhou: nao chegou em dados bancarios."
    assert not valid_10["ramo_negativo"], "ETAPA 7.2 falhou: fluxo caiu no ramo negativo."

    _salvar_log(
        {
            "cenario": "etapa_05_dados_pessoais_ate_bancarios",
            "dados_utilizados": {"email": email, "cep": cep, "numero_casa": numero_casa, "complemento": complemento},
            "captura_etapa1": etapa1,
            "captura_etapa2": etapa2,
            "captura_etapa3": etapa3,
            "captura_etapa4": etapa4,
            "captura_etapa5": etapa5,
            "captura_etapa6": etapa6,
            "captura_etapa7": etapa7,
            "captura_etapa8": etapa8,
            "captura_etapa9": etapa9,
            "captura_etapa10": etapa10,
            "thread_json_final": _obter_thread_json_seguro(blip),
        }
    )



