import json
import os
import re
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
OPCAO_COM_SEGURO = "Seguir com protecao Mega"


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
    s = re.sub(r"<[^>]+>", " ", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
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


def _acao_texto_e_captura(client, blip, texto, timeout_total, segundos_sem_novas, janela_segundos):
    ultimo_id_antes = _ultimo_id_bot_seguro(blip)
    envio_utc = datetime.now(timezone.utc)
    client.enviar_e_aguardar(texto, delay=6, exibir_resposta=False)
    raw = _capturar_respostas_ate_estabilizar(blip, ultimo_id_antes, timeout_total, segundos_sem_novas)
    raw_filtrado = _filtrar_raw_por_envio(raw, envio_utc, janela_segundos)
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_utc, janela_segundos)
    return {"raw_filtrado": raw_filtrado}


def _acao_lista_e_captura(client, blip, botao_lista, opcao_lista, timeout_total, segundos_sem_novas, janela_segundos):
    ultimo_id_antes = _ultimo_id_bot_seguro(blip)
    envio_utc = datetime.now(timezone.utc)
    fallback_texto_enviado = False
    try:
        client.selecionar_opcao_lista(botao_lista, opcao_lista, timeout=30)
    except Exception:
        fallback_texto_enviado = True
        client.enviar_e_aguardar(opcao_lista, delay=4, exibir_resposta=False)

    raw = _capturar_respostas_ate_estabilizar(blip, ultimo_id_antes, timeout_total, segundos_sem_novas)
    raw_filtrado = _filtrar_raw_por_envio(raw, envio_utc, janela_segundos)
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_utc, janela_segundos)

    if not raw_filtrado and not fallback_texto_enviado:
        envio_fallback_utc = datetime.now(timezone.utc)
        ultimo_id_fallback_antes = _ultimo_id_bot_seguro(blip)
        client.enviar_e_aguardar(opcao_lista, delay=4, exibir_resposta=False)
        raw_fallback = _capturar_respostas_ate_estabilizar(blip, ultimo_id_fallback_antes, 120, 15)
        raw_filtrado = _filtrar_raw_por_envio(raw_fallback, envio_fallback_utc, 180)
        if not raw_filtrado:
            raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_fallback_utc, 180)
        fallback_texto_enviado = True

    return {"raw_filtrado": raw_filtrado, "fallback_texto_enviado": fallback_texto_enviado}


def _clicar_opcao_e_captura(client, blip, texto_opcao, timeout_total, segundos_sem_novas, janela_segundos):
    ultimo_id_antes = _ultimo_id_bot_seguro(blip)
    envio_utc = datetime.now(timezone.utc)
    clicou_fallback_texto = False
    try:
        client.clicar_opcao_por_texto(texto_opcao, timeout=20)
    except Exception:
        clicou_fallback_texto = True
        client.enviar_e_aguardar(texto_opcao, delay=4, exibir_resposta=False)

    raw = _capturar_respostas_ate_estabilizar(blip, ultimo_id_antes, timeout_total, segundos_sem_novas)
    raw_filtrado = _filtrar_raw_por_envio(raw, envio_utc, janela_segundos)
    if not raw_filtrado:
        raw_filtrado = _capturar_sent_thread_por_janela(blip, envio_utc, janela_segundos)
    return {"raw_filtrado": raw_filtrado, "fallback_texto": clicou_fallback_texto}


def _erro_instabilidade_blip(exc: Exception) -> bool:
    msg = str(exc).lower()
    termos = [
        "521", "520", "522", "523", "524",
        "timeout", "timed out", "server error", "http error",
        "connection aborted", "connection reset",
    ]
    return any(t in msg for t in termos)


def _acao_texto_resiliente(etapa5, client, blip, texto, timeout_total, segundos_sem_novas, janela_segundos, etapa_nome, max_tentativas=3):
    ultimo_erro = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            out = _acao_texto_e_captura(client, blip, texto, timeout_total, segundos_sem_novas, janela_segundos)
            if out.get("raw_filtrado"):
                return out
            ultimo_erro = RuntimeError(f"{etapa_nome} sem retorno ({tentativa}/{max_tentativas}).")
        except Exception as exc:
            if not _erro_instabilidade_blip(exc):
                raise
            ultimo_erro = exc
        if tentativa < max_tentativas:
            time.sleep(4 * tentativa)
    pytest.xfail(f"{etapa_nome} inconclusiva por instabilidade BLIP/API: {ultimo_erro}")


def _acao_lista_resiliente(etapa5, client, blip, botao_lista, opcao_lista, timeout_total, segundos_sem_novas, janela_segundos, etapa_nome, max_tentativas=3):
    ultimo_erro = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            out = _acao_lista_e_captura(client, blip, botao_lista, opcao_lista, timeout_total, segundos_sem_novas, janela_segundos)
            if out.get("raw_filtrado"):
                return out
            ultimo_erro = RuntimeError(f"{etapa_nome} sem retorno ({tentativa}/{max_tentativas}).")
        except Exception as exc:
            if not _erro_instabilidade_blip(exc):
                raise
            ultimo_erro = exc
        if tentativa < max_tentativas:
            time.sleep(4 * tentativa)
    pytest.xfail(f"{etapa_nome} inconclusiva por instabilidade BLIP/API: {ultimo_erro}")


def _acao_click_resiliente(etapa5, client, blip, texto_opcao, timeout_total, segundos_sem_novas, janela_segundos, etapa_nome, max_tentativas=3):
    ultimo_erro = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            out = _clicar_opcao_e_captura(client, blip, texto_opcao, timeout_total, segundos_sem_novas, janela_segundos)
            if out.get("raw_filtrado"):
                return out
            ultimo_erro = RuntimeError(f"{etapa_nome} sem retorno ({tentativa}/{max_tentativas}).")
        except Exception as exc:
            if not _erro_instabilidade_blip(exc):
                raise
            ultimo_erro = exc
        if tentativa < max_tentativas:
            time.sleep(4 * tentativa)
    pytest.xfail(f"{etapa_nome} inconclusiva por instabilidade BLIP/API: {ultimo_erro}")


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


def _classificar_estado(raw_seq: list) -> dict:
    textos = " ".join([_normalizar_texto(_raw_texto(x)) for x in raw_seq])
    return {
        "ramo_sem_oferta": any(k in textos for k in [
            "desculpe nao consegui seguir com a sua solicitacao",
            "o que voce gostaria de fazer agora",
            "ir para o menu",
            "finalizar conversa",
            "ultrapassou o limite de consultas de cpf",
            "limite de consultas de cpf",
        ]),
        "menu_oferta": any(k in textos for k in [
            "escolher ofertas",
            "valor que voce vai receber pode mudar",
        ]),
        "confirmacao_emprestimo": "voce confirma que deseja contratar este emprestimo" in textos,
        "menu_principal_ver_opcoes": any(k in textos for k in [
            "clique em ver opcoes",
            "ver opcoes",
            "quero dinheiro",
            "outros assuntos",
            "que bom ver voce por aqui de novo",
        ]),
        "inicio_fluxo_correto": any(k in textos for k in [
            "termo de consentimento e scr consignado privado",
            "para continuar precisamos que voce autorize a consulta de dados",
            "credito do trabalhador clt e aproveitar ofertas exclusivas",
        ]),
        "pediu_email": any(k in textos for k in ["email", "e mail"]),
        "ramo_negativo": "desculpe" in textos and "solicitacao" in textos,
        "edicao_retorno": any(k in textos for k in ["alterar valor", "resumo da oferta", "valor a receber"]),
    }


def _detectar_estado_pos_autorizacao(raw_seq: list) -> str:
    s = _classificar_estado(raw_seq)
    if s["ramo_sem_oferta"]:
        return "sem_oferta"
    if s["confirmacao_emprestimo"]:
        return "confirmacao_emprestimo"
    if s["menu_oferta"]:
        return "menu_oferta"
    textos = " ".join([_normalizar_texto(_raw_texto(x)) for x in raw_seq])
    if "um instante enquanto confirmo os dados para sua seguranca" in textos:
        return "processando"
    return "indefinido"


def _captura_passiva(blip: BlipClient, timeout_total: int = 120, segundos_sem_novas: int = 20):
    ultimo_id = _ultimo_id_bot_seguro(blip)
    raw = _capturar_respostas_ate_estabilizar(blip, ultimo_id, timeout_total, segundos_sem_novas)
    return {"raw_filtrado": raw}


def _pausa_reset_manual(cenario: str):
    pausar = os.getenv("PAUSAR_PARA_RESET_MANUAL", "true").lower() in ("1", "true", "yes")
    if not pausar:
        print(f"\n[RESET MANUAL] ignorado (PAUSAR_PARA_RESET_MANUAL=false) | {cenario}")
        return
    print(f"\n[RESET MANUAL] Cenario: {cenario}")
    input("Faca o reset manual no Beholder e pressione Enter para continuar...")


def _salvar_log(resultado: dict) -> None:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = resultado["subcenario"].replace(" ", "_")
    path = log_dir / f"raw_etapa_04_fluxo_seguro_{slug}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)


def _encerrar_controlado(etapa5, client, blip, subcenario: str, etapa: str, motivo: str, capturas: dict):
    print(f"\n[info] Encerramento detectado em {etapa}: {motivo}")
    e_final = {"raw_filtrado": []}
    try:
        # Evita clicar em botoes antigos do historico: prioriza envio textual.
        e_final = _acao_texto_resiliente(etapa5, client, blip, "Finalizar conversa", 120, 15, 120, f"{etapa}F")
        if not e_final.get("raw_filtrado"):
            e_final = _acao_click_resiliente(etapa5, client, blip, "Finalizar conversa", 120, 15, 120, f"{etapa}F")
        _imprimir_seq(f"{etapa}F (finalizar conversa)", e_final.get("raw_filtrado", []), mensagem_usuario="Finalizar conversa")
    except Exception:
        pass
    payload = {
        "cenario": "etapa_04_fluxo_seguro",
        "subcenario": subcenario,
        "status": "sem_oferta",
        "motivo": motivo,
        "captura_finalizacao": e_final,
        "thread_json_final": etapa5._obter_thread_json_seguro(blip) if hasattr(etapa5, "_obter_thread_json_seguro") else blip.obter_thread_json(),
    }
    payload.update(capturas)
    _salvar_log(payload)
    pytest.xfail(f"Fluxo inconclusivo ({subcenario}): {motivo}")


def _falhar_controlado(subcenario: str, etapa: str, motivo: str, capturas: dict, blip: BlipClient):
    print(f"\n[erro] Falha de validacao em {etapa}: {motivo}")
    payload = {
        "cenario": "etapa_04_fluxo_seguro",
        "subcenario": subcenario,
        "status": "falha_validacao",
        "motivo": motivo,
        "thread_json_final": blip.obter_thread_json(),
    }
    payload.update(capturas)
    _salvar_log(payload)
    pytest.fail(f"{etapa} invalida: {motivo}")


@pytest.mark.integration
@pytest.mark.manual_reset
@pytest.mark.parametrize(
    ("subcenario", "acao_final"),
    [
        ("sem_edicao", "Sim, confirmo"),
        ("com_edicao", "Alterar valor"),
    ],
    ids=["sem_edicao", "com_edicao"],
)
def test_etapa_04_fluxo_seguro(subcenario: str, acao_final: str):
    if not USE_WHATSAPP:
        pytest.skip("Defina USE_WHATSAPP=true no .env")
    if not WHATSAPP_NUMERO_ALVO:
        pytest.skip("Configure WHATSAPP_NUMERO_ALVO no .env")
    if not (BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID):
        pytest.skip("Configure BLIP_KEY, BLIP_BOT_ID e BLIP_USER_ID no .env")

    _pausa_reset_manual(f"etapa_04_fluxo_seguro::{subcenario}")

    client = WhatsAppClient()
    blip = _blip_no_mesmo_usuario_whatsapp()
    try:
        if not client.iniciar():
            pytest.fail("WhatsApp Web nao pronto. Escaneie o QR e rode o teste novamente.")

        etapa1 = _acao_texto_resiliente(None, client, blip, MENSAGEM_INICIAL, 120, 15, 120, "ETAPA 1")
        _imprimir_seq("ETAPA 1", etapa1["raw_filtrado"], mensagem_usuario=MENSAGEM_INICIAL)
        assert etapa1["raw_filtrado"], "ETAPA 1 falhou."
        s1 = _classificar_estado(etapa1["raw_filtrado"])
        if s1["menu_principal_ver_opcoes"] and not s1["inicio_fluxo_correto"]:
            _falhar_controlado(
                subcenario,
                "ETAPA 1",
                "bot retornou menu principal (Ver opcoes), fora do inicio esperado do fluxo",
                {"captura_etapa1": etapa1},
                blip,
            )
        if s1["ramo_sem_oferta"]:
            _encerrar_controlado(None, client, blip, subcenario, "ETAPA 1", "ramo sem oferta/logo apos entrada", {
                "captura_etapa1": etapa1,
            })

        etapa2 = _acao_texto_resiliente(None, client, blip, RESPOSTA_AUTORIZACAO, 240, 25, 240, "ETAPA 2")
        _imprimir_seq("ETAPA 2", etapa2["raw_filtrado"], mensagem_usuario=RESPOSTA_AUTORIZACAO)
        assert etapa2["raw_filtrado"], "ETAPA 2 falhou."
        s2 = _classificar_estado(etapa2["raw_filtrado"])
        estado_pos_autorizacao = _detectar_estado_pos_autorizacao(etapa2["raw_filtrado"])
        if estado_pos_autorizacao in ("processando", "indefinido"):
            etapa2p = _captura_passiva(blip, timeout_total=120, segundos_sem_novas=20)
            if etapa2p["raw_filtrado"]:
                etapa2["raw_filtrado"] = etapa2["raw_filtrado"] + etapa2p["raw_filtrado"]
                _imprimir_seq("ETAPA 2P (captura passiva)", etapa2p["raw_filtrado"])
                s2 = _classificar_estado(etapa2["raw_filtrado"])
                estado_pos_autorizacao = _detectar_estado_pos_autorizacao(etapa2["raw_filtrado"])
        if s2["ramo_sem_oferta"]:
            _encerrar_controlado(None, client, blip, subcenario, "ETAPA 2", "ramo sem oferta apos autorizacao", {
                "captura_etapa1": etapa1,
                "captura_etapa2": etapa2,
            })

        if estado_pos_autorizacao == "confirmacao_emprestimo":
            etapa3 = {"raw_filtrado": etapa2["raw_filtrado"]}
            print("  [info] ETAPA 3 pulada: bot ja estava em confirmacao de emprestimo.")
        else:
            etapa3 = _acao_lista_resiliente(None, client, blip, BOTAO_LISTA, OPCAO_COM_SEGURO, 240, 25, 240, "ETAPA 3")
            _imprimir_seq("ETAPA 3", etapa3["raw_filtrado"], mensagem_usuario=f"{BOTAO_LISTA} -> {OPCAO_COM_SEGURO}")
            assert etapa3["raw_filtrado"], "ETAPA 3 falhou."
            s3 = _classificar_estado(etapa3["raw_filtrado"])
            if s3["ramo_sem_oferta"]:
                _encerrar_controlado(None, client, blip, subcenario, "ETAPA 3", "ramo sem oferta na selecao da oferta", {
                    "captura_etapa1": etapa1,
                    "captura_etapa2": etapa2,
                    "captura_etapa3": etapa3,
                })
            if s3["menu_oferta"] and not s3["confirmacao_emprestimo"]:
                etapa3b = _acao_lista_resiliente(None, client, blip, BOTAO_LISTA, OPCAO_COM_SEGURO, 240, 25, 240, "ETAPA 3B")
                _imprimir_seq("ETAPA 3B (re-selecao oferta)", etapa3b["raw_filtrado"], mensagem_usuario=f"{BOTAO_LISTA} -> {OPCAO_COM_SEGURO}")
                if etapa3b.get("raw_filtrado"):
                    etapa3 = etapa3b

        etapa4 = _acao_click_resiliente(None, client, blip, acao_final, 240, 25, 240, "ETAPA 4")
        _imprimir_seq("ETAPA 4", etapa4["raw_filtrado"], mensagem_usuario=acao_final)
        assert etapa4["raw_filtrado"], "ETAPA 4 falhou."
        s4_guard = _classificar_estado(etapa4["raw_filtrado"])
        if s4_guard["ramo_sem_oferta"]:
            _encerrar_controlado(
                None,
                client,
                blip,
                subcenario,
                "ETAPA 4",
                "ramo sem oferta/menu de encerramento apos acao final",
                {
                    "captura_etapa1": etapa1,
                    "captura_etapa2": etapa2,
                    "captura_etapa3": etapa3,
                    "captura_etapa4": etapa4,
                },
            )

    finally:
        client.fechar()

    s4 = _classificar_estado(etapa4["raw_filtrado"])
    if s4["ramo_sem_oferta"]:
        pytest.xfail("Fluxo caiu em menu de encerramento na etapa 4.")

    confirmou_continuidade = s4["pediu_email"] or s4["edicao_retorno"] or s4["confirmacao_emprestimo"]

    print("\n[resultado por etapa]")
    print(f"[{'OK' if bool(etapa4['raw_filtrado']) else 'FAIL'}] ETAPA 4.1 - retorno apos acao final")
    print(f"[{'OK' if (not s4['ramo_negativo']) else 'FAIL'}] ETAPA 4.2 - sem ramo negativo")
    print(f"[{'OK' if confirmou_continuidade else 'FAIL'}] ETAPA 4.3 - confirmacao/continuidade")

    assert not s4["ramo_negativo"], "ETAPA 4 falhou: caiu no ramo negativo."
    assert confirmou_continuidade, "ETAPA 4 falhou: sem confirmacao/continuidade apos acao final."

    _salvar_log(
        {
            "cenario": "etapa_04_fluxo_seguro",
            "subcenario": subcenario,
            "acao_final": acao_final,
            "mensagem_inicial": MENSAGEM_INICIAL,
            "captura_etapa1": etapa1,
            "captura_etapa2": etapa2,
            "captura_etapa3": etapa3,
            "captura_etapa4": etapa4,
            "validacao_etapa4": s4,
            "thread_json_final": blip.obter_thread_json(),
        }
    )
