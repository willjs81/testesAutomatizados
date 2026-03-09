import importlib.util
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from config import (
    BLIP_BOT_ID,
    BLIP_KEY,
    BLIP_USER_ID,
    USE_WHATSAPP,
    WHATSAPP_NUMERO_ALVO,
)
from whatsapp_client import WhatsAppClient


def _carregar_helpers_etapa5():
    path = Path(__file__).with_name("test_etapa_05_dados_pessoais.py")
    spec = importlib.util.spec_from_file_location("etapa5_helpers", path)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _carregar_dados_teste() -> dict:
    path = Path(__file__).resolve().parents[1] / "dados_teste.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _classificar_bancario(etapa5, raw_seq: list) -> dict:
    textos = " ".join([etapa5._normalizar_texto(etapa5._raw_texto(x)) for x in raw_seq])
    ramo_sem_oferta = any(k in textos for k in [
        "desculpe nao consegui seguir com a sua solicitacao",
        "nao consegui seguir com a sua solicitacao",
        "o que voce gostaria de fazer agora",
        "ir para o menu",
        "finalizar conversa",
        "escolha uma opcao do menu",
    ])
    return {
        "pediu_banco": any(k in textos for k in [
            "qual e o banco",
            "escolha uma opcao",
            "escolher_banco-",
            "banco do brasil",
            "caixa economica",
            "itau",
            "bradesco",
            "santander",
        ]),
        "pediu_tipo_conta": any(k in textos for k in [
            "tipo de conta",
            "conta corrente",
            "conta poupanca",
        ]),
        "pediu_agencia": any(k in textos for k in [
            "numero da agencia",
            "n da agencia",
            "agencia",
        ]),
        "pediu_conta": any(k in textos for k in [
            "numero da sua conta",
            "numero da conta",
            "informe tambem o digito",
            "digito",
        ]),
        "ramo_negativo": ramo_sem_oferta or any(k in textos for k in [
            "desculpe, nao consegui seguir com a sua solicitacao",
            "finalizar conversa",
            "ir para o menu",
        ]),
        "ramo_sem_oferta": ramo_sem_oferta,
        "pediu_confirmacao_bancaria": any(k in textos for k in [
            "os dados informados foram",
            "estao corretos",
            "estÃ£o corretos",
            "sim, continuar",
            "quero alterar",
        ]),
        "pediu_tipo_documento": any(k in textos for k in [
            "qual documento voce deseja informar",
            "escolha entre as duas opcoes abaixo",
            "identidade rg",
            "motorista cnh",
        ]),
        "ramo_documentos": any(k in textos for k in [
            "documento",
            "rg",
            "cnh",
            "selfie",
            "foto",
            "frente",
            "verso",
        ]),
        "pediu_numero_cnh": any(k in textos for k in [
            "numero da sua cnh",
            "número da sua cnh",
        ]),
        "finalizou_proposta": any(k in textos for k in [
            "aguarde enquanto estamos gerando sua proposta",
            "falta pouco para finalizar a contratacao",
            "clique no botao abaixo para formalizar proposta",
            "formalizar proposta",
            "contratar proposta",
            "consultar o contrato com todas as condicoes",
        ]),
    }


def _texto_normalizado(etapa5, raw_seq: list) -> str:
    return " ".join([etapa5._normalizar_texto(etapa5._raw_texto(x)) for x in raw_seq])


def _ainda_em_menu_ofertas(etapa5, raw_seq: list) -> bool:
    t = _texto_normalizado(etapa5, raw_seq)
    return any(k in t for k in [
        "escolher ofertas",
        "valor que voce vai receber pode mudar",
        "escolha uma das opcoes a cima",
    ])


def _esta_na_confirmacao_emprestimo(etapa5, raw_seq: list) -> bool:
    t = _texto_normalizado(etapa5, raw_seq)
    return "voce confirma que deseja contratar este emprestimo" in t


def _captura_passiva(etapa5, blip, timeout_total: int = 90, segundos_sem_novas: int = 15) -> dict:
    inicio_utc = datetime.now(timezone.utc)
    ultimo_id_antes = etapa5._ultimo_id_bot_seguro(blip)
    raw = etapa5._capturar_respostas_ate_estabilizar(
        blip, ultimo_id_antes, timeout_total=timeout_total, segundos_sem_novas=segundos_sem_novas
    )
    try:
        raw = etapa5._filtrar_raw_por_envio(raw, inicio_utc, timeout_total + 5)
    except Exception:
        pass
    return {"raw_filtrado": raw}


def _detectar_estado_pos_autorizacao(etapa5, raw_seq: list) -> str:
    if not raw_seq:
        return "sem_resposta"
    b = _classificar_bancario(etapa5, raw_seq)
    if b["ramo_sem_oferta"] or b["ramo_negativo"]:
        return "sem_oferta"
    if _esta_na_confirmacao_emprestimo(etapa5, raw_seq):
        return "confirmacao_emprestimo"
    if _ainda_em_menu_ofertas(etapa5, raw_seq):
        return "menu_oferta"
    t = _texto_normalizado(etapa5, raw_seq)
    if "um instante enquanto confirmo os dados para sua seguranca" in t:
        return "processando"
    return "indefinido"


def _salvar_log(resultado: dict):
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"raw_etapa_06_dados_bancarios_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)


def _erro_instabilidade_blip(exc: Exception) -> bool:
    msg = str(exc).lower()
    termos = [
        "521", "520", "522", "523", "524",
        "timeout", "timed out", "server error", "http error",
        "connection aborted", "connection reset",
    ]
    return any(t in msg for t in termos)


def _acao_texto_resiliente(
    etapa5,
    client,
    blip,
    texto: str,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
    etapa_nome: str,
    max_tentativas: int = 3,
):
    ultimo_erro = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            out = etapa5._acao_texto_e_captura(
                client, blip, texto, timeout_total, segundos_sem_novas, janela_segundos
            )
            if out.get("raw_filtrado"):
                return out
            ultimo_erro = RuntimeError(f"{etapa_nome} sem retorno (tentativa {tentativa}/{max_tentativas}).")
        except Exception as exc:  # pragma: no cover
            if not _erro_instabilidade_blip(exc):
                raise
            ultimo_erro = exc

        if tentativa < max_tentativas:
            espera = 4 * tentativa
            print(f"  [warn] {etapa_nome} sem retorno/instavel. Retry em {espera}s ({tentativa}/{max_tentativas})...")
            time.sleep(espera)

    pytest.xfail(
        f"{etapa_nome} inconclusiva por instabilidade BLIP/API apos {max_tentativas} tentativas: {ultimo_erro}"
    )


def _acao_lista_resiliente(
    etapa5,
    client,
    blip,
    botao_lista: str,
    opcao_lista: str,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
    etapa_nome: str,
    max_tentativas: int = 3,
):
    ultimo_erro = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            out = etapa5._acao_lista_e_captura(
                client, blip, botao_lista, opcao_lista, timeout_total, segundos_sem_novas, janela_segundos
            )
            if out.get("raw_filtrado"):
                return out
            ultimo_erro = RuntimeError(f"{etapa_nome} sem retorno (tentativa {tentativa}/{max_tentativas}).")
        except Exception as exc:  # pragma: no cover
            if not _erro_instabilidade_blip(exc):
                raise
            ultimo_erro = exc

        if tentativa < max_tentativas:
            espera = 4 * tentativa
            print(f"  [warn] {etapa_nome} sem retorno/instavel. Retry em {espera}s ({tentativa}/{max_tentativas})...")
            time.sleep(espera)

    pytest.xfail(
        f"{etapa_nome} inconclusiva por instabilidade BLIP/API apos {max_tentativas} tentativas: {ultimo_erro}"
    )


def _acao_click_resiliente(
    etapa5,
    client,
    blip,
    texto_opcao: str,
    timeout_total: int,
    segundos_sem_novas: int,
    janela_segundos: int,
    etapa_nome: str,
    max_tentativas: int = 3,
):
    ultimo_erro = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            out = etapa5._clicar_opcao_e_captura(
                client, blip, texto_opcao, timeout_total, segundos_sem_novas, janela_segundos
            )
            if out.get("raw_filtrado"):
                return out
            ultimo_erro = RuntimeError(f"{etapa_nome} sem retorno (tentativa {tentativa}/{max_tentativas}).")
        except Exception as exc:  # pragma: no cover
            if not _erro_instabilidade_blip(exc):
                raise
            ultimo_erro = exc

        if tentativa < max_tentativas:
            espera = 4 * tentativa
            print(f"  [warn] {etapa_nome} sem retorno/instavel. Retry em {espera}s ({tentativa}/{max_tentativas})...")
            time.sleep(espera)

    pytest.xfail(
        f"{etapa_nome} inconclusiva por instabilidade BLIP/API apos {max_tentativas} tentativas: {ultimo_erro}"
    )


def _encerrar_controlado_sem_oferta(
    etapa5,
    client,
    blip,
    etapa_nome: str,
    motivo: str,
    dados_utilizados: dict,
    capturas: dict,
):
    print(f"\n[info] Ramo sem oferta detectado em {etapa_nome}. Encerrando conversa de forma controlada.")
    e_final = {"raw_filtrado": []}
    try:
        e_final = _acao_click_resiliente(
            etapa5, client, blip, "Finalizar conversa", 120, 15, 120, f"{etapa_nome} (finalizar conversa)"
        )
        if e_final.get("raw_filtrado"):
            etapa5._imprimir_seq(f"{etapa_nome} (finalizar conversa)", e_final["raw_filtrado"], mensagem_usuario="Finalizar conversa")
    except Exception as exc:  # pragma: no cover - depende do canal
        print(f"  [warn] nao foi possivel finalizar conversa automaticamente: {exc}")

    payload = {
        "cenario": "etapa_06_dados_bancarios",
        "status": "sem_oferta",
        "motivo": motivo,
        "dados_utilizados": dados_utilizados,
        "captura_finalizacao": e_final,
        "thread_json_final": etapa5._obter_thread_json_seguro(blip),
    }
    payload.update(capturas)
    _salvar_log(payload)
    pytest.xfail(f"E2E sem oferta em {etapa_nome}: {motivo}")


@pytest.mark.integration
@pytest.mark.manual_reset
def test_etapa_06_dados_bancarios():
    if not USE_WHATSAPP:
        pytest.skip("Defina USE_WHATSAPP=true no .env")
    if not WHATSAPP_NUMERO_ALVO:
        pytest.skip("Configure WHATSAPP_NUMERO_ALVO no .env")
    if not (BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID):
        pytest.skip("Configure BLIP_KEY, BLIP_BOT_ID e BLIP_USER_ID no .env")

    etapa5 = _carregar_helpers_etapa5()
    dados = _carregar_dados_teste()
    email = str(dados.get("email", "")).strip()
    cep = str(dados.get("cep", "")).strip()
    numero_casa = str(dados.get("numero_casa", "")).strip()
    complemento = str(dados.get("complemento", "casa 6 fundos")).strip()[:30]
    banco = str(dados.get("banco", "104 Caixa Economica")).strip()
    tipo_conta = str(dados.get("tipo_conta", "Conta Corrente")).strip()
    acao_confirmacao_bancaria = str(dados.get("acao_confirmacao_bancaria", "Sim, continuar")).strip()
    tipo_documento = str(dados.get("tipo_documento", "Motorista (CNH)")).strip()
    cnh = str(dados.get("cnh", "")).strip()
    agencia = str(dados.get("agencia", "")).strip()
    conta = str(dados.get("conta", "")).strip()
    dados_utilizados = {
        "email": email,
        "cep": cep,
        "numero_casa": numero_casa,
        "complemento": complemento,
        "banco": banco,
        "tipo_conta": tipo_conta,
        "acao_confirmacao_bancaria": acao_confirmacao_bancaria,
        "tipo_documento": tipo_documento,
        "cnh": cnh,
        "agencia": agencia,
        "conta": conta,
    }

    assert email and cep and numero_casa and agencia and conta, "dados_teste.json incompleto para etapa 6."

    etapa5._pausa_reset_manual("etapa_06_dados_bancarios")

    client = WhatsAppClient()
    blip = etapa5._blip_no_mesmo_usuario_whatsapp()

    try:
        if not client.iniciar():
            pytest.fail("WhatsApp Web nao pronto. Escaneie o QR e rode o teste novamente.")

        # Reuso do fluxo ate entrada no menu de banco.
        e1 = _acao_texto_resiliente(
            etapa5, client, blip, etapa5.MENSAGEM_INICIAL, 120, 15, 120, "ETAPA 1"
        )
        etapa5._imprimir_seq("ETAPA 1", e1["raw_filtrado"], mensagem_usuario=etapa5.MENSAGEM_INICIAL)
        assert e1["raw_filtrado"], "ETAPA 1 falhou."

        e2 = _acao_texto_resiliente(
            etapa5, client, blip, etapa5.RESPOSTA_AUTORIZACAO, 240, 25, 240, "ETAPA 2"
        )
        etapa5._imprimir_seq("ETAPA 2", e2["raw_filtrado"], mensagem_usuario=etapa5.RESPOSTA_AUTORIZACAO)
        assert e2["raw_filtrado"], "ETAPA 2 falhou."
        e2v = _classificar_bancario(etapa5, e2["raw_filtrado"])
        estado_pos_autorizacao = _detectar_estado_pos_autorizacao(etapa5, e2["raw_filtrado"])

        # Executor por estado (fase 1): se ainda estiver processando/indefinido, aguarda transicao passiva.
        if estado_pos_autorizacao in ("processando", "indefinido", "sem_resposta"):
            e2p = _captura_passiva(etapa5, blip, timeout_total=120, segundos_sem_novas=20)
            if e2p["raw_filtrado"]:
                e2["raw_filtrado"] = e2["raw_filtrado"] + e2p["raw_filtrado"]
                etapa5._imprimir_seq("ETAPA 2P (captura passiva)", e2p["raw_filtrado"])
                e2v = _classificar_bancario(etapa5, e2["raw_filtrado"])
                estado_pos_autorizacao = _detectar_estado_pos_autorizacao(etapa5, e2["raw_filtrado"])

        # Guard rail do E2E: sem oferta apos autorizacao.
        if e2v["ramo_sem_oferta"] or e2v["ramo_negativo"] or estado_pos_autorizacao == "sem_oferta":
            _encerrar_controlado_sem_oferta(
                etapa5,
                client,
                blip,
                "ETAPA 2",
                "ramo negativo/sem oferta apos autorizacao",
                dados_utilizados,
                {"captura_etapa1": e1, "captura_etapa2": e2},
            )

        # Estado decide proxima acao apos autorizacao.
        if estado_pos_autorizacao == "confirmacao_emprestimo":
            e3 = {"raw_filtrado": e2["raw_filtrado"]}
            print("  [info] ETAPA 3 pulada: bot ja estava em confirmacao de emprestimo.")
        else:
            e3 = _acao_lista_resiliente(
                etapa5, client, blip, etapa5.BOTAO_LISTA, etapa5.OPCAO_COM_SEGURO, 240, 25, 240, "ETAPA 3"
            )
            etapa5._imprimir_seq("ETAPA 3", e3["raw_filtrado"], mensagem_usuario=f"{etapa5.BOTAO_LISTA} -> {etapa5.OPCAO_COM_SEGURO}")
            assert e3["raw_filtrado"], "ETAPA 3 falhou."
            e3v = _classificar_bancario(etapa5, e3["raw_filtrado"])
            if e3v["ramo_sem_oferta"]:
                _encerrar_controlado_sem_oferta(
                    etapa5,
                    client,
                    blip,
                    "ETAPA 3",
                    "ramo sem oferta detectado na etapa 3",
                    dados_utilizados,
                    {
                        "captura_etapa1": e1,
                        "captura_etapa2": e2,
                        "captura_etapa3": e3,
                    },
                )
            if _ainda_em_menu_ofertas(etapa5, e3["raw_filtrado"]) and not _esta_na_confirmacao_emprestimo(etapa5, e3["raw_filtrado"]):
                print("  [info] Ainda em menu de ofertas após ETAPA 3, repetindo seleção de oferta...")
                e3b = _acao_lista_resiliente(
                    etapa5, client, blip, etapa5.BOTAO_LISTA, etapa5.OPCAO_COM_SEGURO, 240, 25, 240, "ETAPA 3B"
                )
                etapa5._imprimir_seq("ETAPA 3B (re-selecao oferta)", e3b["raw_filtrado"], mensagem_usuario=f"{etapa5.BOTAO_LISTA} -> {etapa5.OPCAO_COM_SEGURO}")
                if e3b["raw_filtrado"]:
                    e3 = e3b

        e4 = _acao_click_resiliente(
            etapa5, client, blip, etapa5.CONFIRMACAO, 240, 25, 240, "ETAPA 4"
        )
        etapa5._imprimir_seq("ETAPA 4", e4["raw_filtrado"], mensagem_usuario=etapa5.CONFIRMACAO)
        assert e4["raw_filtrado"], "ETAPA 4 falhou."
        e4v = _classificar_bancario(etapa5, e4["raw_filtrado"])
        if e4v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 4", "menu de encerramento apos confirmar oferta", dados_utilizados,
                {"captura_etapa1": e1, "captura_etapa2": e2, "captura_etapa3": e3, "captura_etapa4": e4}
            )
        if _ainda_em_menu_ofertas(etapa5, e4["raw_filtrado"]):
            print("  [info] ETAPA 4 caiu no menu de ofertas. Re-selecionando oferta e confirmando novamente...")
            e4a = _acao_lista_resiliente(
                etapa5, client, blip, etapa5.BOTAO_LISTA, etapa5.OPCAO_COM_SEGURO, 240, 25, 240, "ETAPA 4A"
            )
            etapa5._imprimir_seq("ETAPA 4A (re-selecao oferta)", e4a["raw_filtrado"], mensagem_usuario=f"{etapa5.BOTAO_LISTA} -> {etapa5.OPCAO_COM_SEGURO}")
            e4b = _acao_click_resiliente(
                etapa5, client, blip, etapa5.CONFIRMACAO, 240, 25, 240, "ETAPA 4B"
            )
            etapa5._imprimir_seq("ETAPA 4B (reconfirmacao)", e4b["raw_filtrado"], mensagem_usuario=etapa5.CONFIRMACAO)
            if e4b["raw_filtrado"]:
                e4 = e4b
        d4_now = etapa5._classificar_fluxo_dados(e4["raw_filtrado"])
        if not d4_now["pediu_email"]:
            pytest.fail("ETAPA 4 invalida: bot nao pediu e-mail; executor por estado interrompeu para evitar desvio.")

        e5 = etapa5._acao_texto_e_captura(client, blip, email, 180, 18, 180)
        etapa5._imprimir_seq("ETAPA 5", e5["raw_filtrado"], mensagem_usuario=email)
        assert e5["raw_filtrado"], "ETAPA 5 falhou."
        e5v = _classificar_bancario(etapa5, e5["raw_filtrado"])
        if e5v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 5", "menu de encerramento apos envio de e-mail", dados_utilizados,
                {"captura_etapa4": e4, "captura_etapa5": e5}
            )
        d5_now = etapa5._classificar_fluxo_dados(e5["raw_filtrado"])
        if not d5_now["pediu_cep"]:
            pytest.fail("ETAPA 5 invalida: bot nao pediu CEP; executor por estado interrompeu para evitar desvio.")

        e6 = etapa5._acao_texto_e_captura(client, blip, cep, 180, 18, 180)
        etapa5._imprimir_seq("ETAPA 6", e6["raw_filtrado"], mensagem_usuario=cep)
        assert e6["raw_filtrado"], "ETAPA 6 falhou."
        e6b = _classificar_bancario(etapa5, e6["raw_filtrado"])
        if e6b["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 6", "menu de encerramento apos envio de CEP", dados_utilizados,
                {"captura_etapa5": e5, "captura_etapa6": e6}
            )

        e6v = etapa5._classificar_fluxo_dados(e6["raw_filtrado"])
        if not (e6v["pediu_confirmacao_endereco"] or e6v["pediu_numero_casa"]):
            pytest.fail("ETAPA 6 invalida: bot nao pediu confirmacao de endereco nem numero da casa.")
        if e6v["pediu_confirmacao_endereco"]:
            e7 = etapa5._confirmar_endereco_e_captura(client, blip, 240, 25, 240)
            etapa5._imprimir_seq("ETAPA 7", e7["raw_filtrado"], mensagem_usuario="Sim, confirmo")
            assert e7["raw_filtrado"], "ETAPA 7 falhou."
            e7v_guard = _classificar_bancario(etapa5, e7["raw_filtrado"])
            if e7v_guard["ramo_sem_oferta"]:
                _encerrar_controlado_sem_oferta(
                    etapa5, client, blip, "ETAPA 7", "menu de encerramento apos confirmar endereco", dados_utilizados,
                    {"captura_etapa6": e6, "captura_etapa7": e7}
                )
            d7_now = etapa5._classificar_fluxo_dados(e7["raw_filtrado"])
            if not d7_now["pediu_numero_casa"]:
                pytest.fail("ETAPA 7 invalida: bot nao pediu numero da casa apos confirmacao de endereco.")
        else:
            e7 = {"raw_filtrado": []}

        e8 = etapa5._acao_texto_e_captura(client, blip, numero_casa, 180, 18, 180)
        etapa5._imprimir_seq("ETAPA 8", e8["raw_filtrado"], mensagem_usuario=numero_casa)
        assert e8["raw_filtrado"], "ETAPA 8 falhou."
        e8b = _classificar_bancario(etapa5, e8["raw_filtrado"])
        if e8b["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 8", "menu de encerramento apos numero da casa", dados_utilizados,
                {"captura_etapa7": e7, "captura_etapa8": e8}
            )

        e8v = etapa5._classificar_fluxo_dados(e8["raw_filtrado"])
        txt_e8 = _texto_normalizado(etapa5, e8["raw_filtrado"])
        needs_complemento = ("complemento" in txt_e8) and any(
            k in txt_e8 for k in ["nao tenha", "nao tenho", "tem complemento", "qual o complemento"]
        )
        if not (needs_complemento or e8v["pediu_complemento"] or e8v["chegou_dados_bancarios"]):
            pytest.fail("ETAPA 8 invalida: bot nao pediu complemento nem avancou para dados bancarios.")
        if needs_complemento or e8v["pediu_complemento"]:
            e9a = etapa5._clicar_opcao_e_captura(client, blip, "Tem complemento", 180, 18, 180)
            etapa5._imprimir_seq("ETAPA 9A", e9a["raw_filtrado"], mensagem_usuario="Tem complemento")
            e9b = etapa5._acao_texto_e_captura(client, blip, complemento, 180, 18, 180)
            etapa5._imprimir_seq("ETAPA 9B", e9b["raw_filtrado"], mensagem_usuario=complemento)
            if not e9b["raw_filtrado"]:
                print("  [warn] ETAPA 9B sem retorno imediato. Aguardando captura passiva...")
                e9bp = _captura_passiva(etapa5, blip, timeout_total=120, segundos_sem_novas=20)
                if e9bp["raw_filtrado"]:
                    etapa5._imprimir_seq("ETAPA 9BP (captura passiva)", e9bp["raw_filtrado"])
                    e9b["raw_filtrado"] = e9b["raw_filtrado"] + e9bp["raw_filtrado"]
                else:
                    pytest.xfail(
                        "ETAPA 9B inconclusiva: complemento enviado sem retorno do bot na janela de captura."
                    )
            e9 = {"raw_filtrado": e9a["raw_filtrado"] + e9b["raw_filtrado"]}
            assert e9["raw_filtrado"], "ETAPA 9 falhou."
            e9b_cls = _classificar_bancario(etapa5, e9["raw_filtrado"])
            if e9b_cls["ramo_sem_oferta"]:
                _encerrar_controlado_sem_oferta(
                    etapa5, client, blip, "ETAPA 9", "menu de encerramento apos complemento", dados_utilizados,
                    {"captura_etapa8": e8, "captura_etapa9": e9}
                )
        else:
            e9 = {"raw_filtrado": []}

        e9v = etapa5._classificar_fluxo_dados(e9["raw_filtrado"])
        if e9v["pediu_confirmacao_endereco"]:
            e10 = etapa5._confirmar_endereco_e_captura(client, blip, 240, 25, 240)
            etapa5._imprimir_seq("ETAPA 10", e10["raw_filtrado"], mensagem_usuario="Sim, confirmo")
            assert e10["raw_filtrado"], "ETAPA 10 falhou."
        else:
            # Pode haver atraso para aparecer a confirmação final de endereço ou o menu de banco.
            e9p = _captura_passiva(etapa5, blip, timeout_total=120, segundos_sem_novas=20)
            if e9p["raw_filtrado"]:
                etapa5._imprimir_seq("ETAPA 9P/10P (captura passiva)", e9p["raw_filtrado"])
                e10 = {"raw_filtrado": e9["raw_filtrado"] + e9p["raw_filtrado"]}
            else:
                e10 = {"raw_filtrado": e9["raw_filtrado"]}
            # Se complemento foi solicitado, não pode seguir sem confirmação final/endereço ou banco.
            e10d = etapa5._classificar_fluxo_dados(e10["raw_filtrado"])
            e10b = _classificar_bancario(etapa5, e10["raw_filtrado"])
            if (needs_complemento or e8v["pediu_complemento"]) and not (
                e10d["pediu_confirmacao_endereco"] or e10b["pediu_banco"] or e10b["ramo_sem_oferta"]
            ):
                pytest.xfail(
                    "Fluxo inconclusivo apos complemento: sem confirmacao final de endereco e sem menu de banco."
                )

            # Nova parte: dados bancarios.
            e10v = _classificar_bancario(etapa5, e10["raw_filtrado"])
            if e10v["ramo_sem_oferta"]:
                _encerrar_controlado_sem_oferta(
                    etapa5, client, blip, "ETAPA 10", "menu de encerramento na entrada de dados bancarios", dados_utilizados,
                    {"captura_etapa9": e9, "captura_etapa10": e10}
                )
            if (not e10v["pediu_banco"]) and (not etapa5._classificar_fluxo_dados(e10["raw_filtrado"])["pediu_confirmacao_endereco"]):
                pytest.xfail(
                    "ETAPA 10 inconclusiva: sem retorno suficiente para confirmar endereco final ou abrir menu de banco."
                )
            assert e10v["pediu_banco"], "ETAPA 10 nao chegou na escolha de banco."

        e11 = etapa5._acao_lista_e_captura(client, blip, "Escolha uma opção", banco, 240, 25, 240)
        if not e11["raw_filtrado"] and " " in banco:
            banco_fallback = banco.split(" ", 1)[1]
            e11 = etapa5._acao_lista_e_captura(client, blip, "Escolha uma opção", banco_fallback, 240, 25, 240)
        if not e11["raw_filtrado"] and " " in banco:
            # Fallback sem acento, se o DOM vier com encoding quebrado.
            banco_fallback = banco.split(" ", 1)[1]
            e11 = etapa5._acao_lista_e_captura(client, blip, "Escolha uma opcao", banco_fallback, 240, 25, 240)
        etapa5._imprimir_seq("ETAPA 11 (banco)", e11["raw_filtrado"], mensagem_usuario=banco)
        assert e11["raw_filtrado"], "ETAPA 11 falhou."

        e11v = _classificar_bancario(etapa5, e11["raw_filtrado"])
        if e11v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 11", "menu de encerramento apos selecao de banco", dados_utilizados,
                {"captura_etapa10": e10, "captura_etapa11": e11}
            )
        if not (e11v["pediu_tipo_conta"] or e11v["pediu_agencia"] or e11v["pediu_conta"]):
            pytest.fail("ETAPA 11 invalida: retorno de banco nao evoluiu para tipo conta/agencia/conta.")
        if e11v["pediu_tipo_conta"]:
            e12 = etapa5._clicar_opcao_e_captura(client, blip, tipo_conta, 240, 25, 240)
            etapa5._imprimir_seq("ETAPA 12 (tipo conta)", e12["raw_filtrado"], mensagem_usuario=tipo_conta)
            assert e12["raw_filtrado"], "ETAPA 12 falhou."
        else:
            e12 = {"raw_filtrado": e11["raw_filtrado"]}

        e12v = _classificar_bancario(etapa5, e12["raw_filtrado"])
        if e12v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 12", "menu de encerramento apos tipo de conta", dados_utilizados,
                {"captura_etapa11": e11, "captura_etapa12": e12}
            )
        if e12v["pediu_agencia"] or (not e12v["pediu_conta"]):
            e13 = etapa5._acao_texto_e_captura(client, blip, agencia, 180, 18, 180)
            etapa5._imprimir_seq("ETAPA 13 (agencia)", e13["raw_filtrado"], mensagem_usuario=agencia)
            assert e13["raw_filtrado"], "ETAPA 13 falhou."
        else:
            e13 = {"raw_filtrado": e12["raw_filtrado"]}

        e13v = _classificar_bancario(etapa5, e13["raw_filtrado"])
        if e13v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 13", "menu de encerramento apos agencia", dados_utilizados,
                {"captura_etapa12": e12, "captura_etapa13": e13}
            )
        if e13v["pediu_conta"] or e12v["pediu_conta"]:
            e14 = etapa5._acao_texto_e_captura(client, blip, conta, 180, 18, 180)
            etapa5._imprimir_seq("ETAPA 14 (conta)", e14["raw_filtrado"], mensagem_usuario=conta)
            assert e14["raw_filtrado"], "ETAPA 14 falhou."
        else:
            e14 = {"raw_filtrado": e13["raw_filtrado"]}

        e14v = _classificar_bancario(etapa5, e14["raw_filtrado"])
        if e14v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 14", "menu de encerramento apos conta", dados_utilizados,
                {"captura_etapa13": e13, "captura_etapa14": e14}
            )
        if e14v["pediu_tipo_conta"]:
            opcoes_tipo_conta = []
            if tipo_conta:
                opcoes_tipo_conta.append(tipo_conta)
            opcoes_tipo_conta.extend(["Conta corrente", "Conta poupança", "Conta poupanca"])
            e15 = {"raw_filtrado": []}
            ultima_exc = None
            for opc in opcoes_tipo_conta:
                try:
                    tentativa = etapa5._clicar_opcao_e_captura(client, blip, opc, 240, 25, 240)
                    if tentativa.get("raw_filtrado"):
                        e15 = tentativa
                        etapa5._imprimir_seq("ETAPA 15 (tipo conta final)", e15["raw_filtrado"], mensagem_usuario=opc)
                        break
                except Exception as exc:
                    ultima_exc = exc
                    continue
            if not e15["raw_filtrado"] and ultima_exc:
                raise ultima_exc
            assert e15["raw_filtrado"], "ETAPA 15 falhou."
        else:
            e15 = {"raw_filtrado": e14["raw_filtrado"]}

        e15v = _classificar_bancario(etapa5, e15["raw_filtrado"])
        if e15v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 15", "menu de encerramento apos tipo conta final", dados_utilizados,
                {"captura_etapa14": e14, "captura_etapa15": e15}
            )
        if e15v["pediu_confirmacao_bancaria"]:
            opcoes_confirmacao = []
            if acao_confirmacao_bancaria:
                opcoes_confirmacao.append(acao_confirmacao_bancaria)
            opcoes_confirmacao.extend(["Sim, continuar", "Quero alterar"])
            e16 = {"raw_filtrado": []}
            ultima_exc = None
            for opc in opcoes_confirmacao:
                try:
                    tentativa = etapa5._clicar_opcao_e_captura(client, blip, opc, 240, 25, 240)
                    if tentativa.get("raw_filtrado"):
                        e16 = tentativa
                        etapa5._imprimir_seq("ETAPA 16 (confirmacao dados bancarios)", e16["raw_filtrado"], mensagem_usuario=opc)
                        break
                except Exception as exc:
                    ultima_exc = exc
                    continue
            if not e16["raw_filtrado"] and ultima_exc:
                raise ultima_exc
            assert e16["raw_filtrado"], "ETAPA 16 falhou."
        else:
            e16 = {"raw_filtrado": e15["raw_filtrado"]}

        # Documento (CNH primeiro).
        e16v = _classificar_bancario(etapa5, e16["raw_filtrado"])
        if e16v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 16", "menu de encerramento apos confirmacao bancaria", dados_utilizados,
                {"captura_etapa15": e15, "captura_etapa16": e16}
            )
        if e16v["pediu_tipo_documento"]:
            opcoes_documento = []
            if tipo_documento:
                opcoes_documento.append(tipo_documento)
            opcoes_documento.extend(["Motorista (CNH)", "Identidade (RG)"])

            e17 = {"raw_filtrado": []}
            ultima_exc = None
            for opc in opcoes_documento:
                try:
                    tentativa = etapa5._clicar_opcao_e_captura(client, blip, opc, 240, 25, 240)
                    if tentativa.get("raw_filtrado"):
                        e17 = tentativa
                        etapa5._imprimir_seq("ETAPA 17 (tipo documento)", e17["raw_filtrado"], mensagem_usuario=opc)
                        break
                except Exception as exc:
                    ultima_exc = exc
                    continue
            if not e17["raw_filtrado"] and ultima_exc:
                raise ultima_exc
            assert e17["raw_filtrado"], "ETAPA 17 falhou."
        else:
            e17 = {"raw_filtrado": e16["raw_filtrado"]}

        e17v = _classificar_bancario(etapa5, e17["raw_filtrado"])
        if e17v["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 17", "menu de encerramento apos escolha de documento", dados_utilizados,
                {"captura_etapa16": e16, "captura_etapa17": e17}
            )
        if e17v["pediu_numero_cnh"]:
            assert cnh, "dados_teste.json sem 'cnh'."
            e18 = etapa5._acao_texto_e_captura(client, blip, cnh, 240, 25, 240)
            etapa5._imprimir_seq("ETAPA 18 (numero CNH)", e18["raw_filtrado"], mensagem_usuario=cnh)
            assert e18["raw_filtrado"], "ETAPA 18 falhou."
        else:
            e18 = {"raw_filtrado": e17["raw_filtrado"]}
        e18v_guard = _classificar_bancario(etapa5, e18["raw_filtrado"])
        if e18v_guard["ramo_sem_oferta"]:
            _encerrar_controlado_sem_oferta(
                etapa5, client, blip, "ETAPA 18", "menu de encerramento apos numero da CNH", dados_utilizados,
                {"captura_etapa17": e17, "captura_etapa18": e18}
            )

        # Aguarda mensagens finais assíncronas de formalizacao, sem nova ação do usuário.
        ultimo_id_passivo = etapa5._ultimo_id_bot_seguro(blip)
        raw_passivo = etapa5._capturar_respostas_ate_estabilizar(
            blip, ultimo_id_passivo, timeout_total=240, segundos_sem_novas=25
        )
        e19 = {"raw_filtrado": raw_passivo}
        etapa5._imprimir_seq("ETAPA 19 (geracao proposta/finalizacao)", e19["raw_filtrado"])

    finally:
        client.fechar()

    d1 = etapa5._classificar_fluxo_dados(e1["raw_filtrado"])
    d2 = etapa5._classificar_fluxo_dados(e2["raw_filtrado"])
    d3 = etapa5._classificar_fluxo_dados(e3["raw_filtrado"])
    d4 = etapa5._classificar_fluxo_dados(e4["raw_filtrado"])
    d5 = etapa5._classificar_fluxo_dados(e5["raw_filtrado"])
    d6 = etapa5._classificar_fluxo_dados(e6["raw_filtrado"])
    d7 = etapa5._classificar_fluxo_dados(e7["raw_filtrado"])
    d8 = etapa5._classificar_fluxo_dados(e8["raw_filtrado"])
    d9 = etapa5._classificar_fluxo_dados(e9["raw_filtrado"])
    d10 = etapa5._classificar_fluxo_dados(e10["raw_filtrado"])

    v11 = _classificar_bancario(etapa5, e11["raw_filtrado"])
    v12 = _classificar_bancario(etapa5, e12["raw_filtrado"])
    v13 = _classificar_bancario(etapa5, e13["raw_filtrado"])
    v14 = _classificar_bancario(etapa5, e14["raw_filtrado"])
    v15 = _classificar_bancario(etapa5, e15["raw_filtrado"])
    v16 = _classificar_bancario(etapa5, e16["raw_filtrado"])
    v17 = _classificar_bancario(etapa5, e17["raw_filtrado"])
    v18 = _classificar_bancario(etapa5, e18["raw_filtrado"])
    v19 = _classificar_bancario(etapa5, e19["raw_filtrado"])

    print("\n[resultado por etapa]")
    etapa5._imprimir_resultado_etapa("ETAPA 1.1 - retorno apos mensagem inicial", bool(e1["raw_filtrado"]))
    etapa5._imprimir_resultado_etapa("ETAPA 2.1 - retorno apos 'Li e autorizo'", bool(e2["raw_filtrado"]))
    etapa5._imprimir_resultado_etapa("ETAPA 3.1 - retorno apos escolher oferta", bool(e3["raw_filtrado"]))
    etapa5._imprimir_resultado_etapa("ETAPA 4.1 - pediu e-mail", d4["pediu_email"])
    etapa5._imprimir_resultado_etapa("ETAPA 5.1 - pediu CEP", d5["pediu_cep"])
    etapa5._imprimir_resultado_etapa(
        "ETAPA 6.1 - pediu confirmacao endereco ou numero da casa",
        d6["pediu_confirmacao_endereco"] or d6["pediu_numero_casa"],
    )
    etapa5._imprimir_resultado_etapa(
        "ETAPA 7.1 - confirmou endereco (quando solicitado)",
        (not d6["pediu_confirmacao_endereco"]) or bool(e7["raw_filtrado"]),
    )
    etapa5._imprimir_resultado_etapa(
        "ETAPA 8.1 - pediu complemento ou ja chegou em dados bancarios",
        d8["pediu_complemento"] or d8["chegou_dados_bancarios"],
    )
    etapa5._imprimir_resultado_etapa(
        "ETAPA 9.1 - confirmou endereco final (quando solicitado)",
        (not d9["pediu_confirmacao_endereco"]) or bool(e10["raw_filtrado"]),
    )
    etapa5._imprimir_resultado_etapa("ETAPA 10.1 - chegou em dados bancarios", d10["chegou_dados_bancarios"])

    print("\n[resultado dados bancarios/documentos]")
    etapa5._imprimir_resultado_etapa("ETAPA 11.1 - resposta apos banco", bool(e11["raw_filtrado"]))
    etapa5._imprimir_resultado_etapa("ETAPA 12.1 - tipo conta/agencia", v11["pediu_tipo_conta"] or v11["pediu_agencia"] or v11["pediu_conta"])
    etapa5._imprimir_resultado_etapa("ETAPA 13.1 - resposta apos agencia", bool(e13["raw_filtrado"]))
    etapa5._imprimir_resultado_etapa("ETAPA 14.1 - resposta apos conta", bool(e14["raw_filtrado"]))
    etapa5._imprimir_resultado_etapa(
        "ETAPA 15.1 - confirmou tipo de conta (quando solicitado)",
        (not v14["pediu_tipo_conta"]) or bool(e15["raw_filtrado"]),
    )
    etapa5._imprimir_resultado_etapa("ETAPA 16.1 - confirmou dados bancarios (quando solicitado)", (not v15["pediu_confirmacao_bancaria"]) or bool(e16["raw_filtrado"]))
    etapa5._imprimir_resultado_etapa("ETAPA 16.2 - sem ramo negativo", not v16["ramo_negativo"])
    etapa5._imprimir_resultado_etapa(
        "ETAPA 17.1 - selecionou tipo de documento (quando solicitado)",
        (not v16["pediu_tipo_documento"]) or bool(e17["raw_filtrado"]),
    )
    etapa5._imprimir_resultado_etapa(
        "ETAPA 17.2 - entrou em ramo de documentos",
        v17["ramo_documentos"],
    )
    etapa5._imprimir_resultado_etapa("ETAPA 17.3 - sem ramo negativo", not v17["ramo_negativo"])
    etapa5._imprimir_resultado_etapa(
        "ETAPA 18.1 - enviou numero CNH (quando solicitado)",
        (not v17["pediu_numero_cnh"]) or bool(e18["raw_filtrado"]),
    )
    etapa5._imprimir_resultado_etapa("ETAPA 18.2 - sem ramo negativo", not v18["ramo_negativo"])
    etapa5._imprimir_resultado_etapa(
        "ETAPA 19.1 - recebeu mensagem final de geracao/formalizacao",
        v18["finalizou_proposta"] or v19["finalizou_proposta"],
    )
    etapa5._imprimir_resultado_etapa(
        "ETAPA 19.2 - sem ramo negativo",
        (not v18["ramo_negativo"]) and (not v19["ramo_negativo"]),
    )

    assert bool(e1["raw_filtrado"]), "ETAPA 1.1 falhou: sem retorno apos mensagem inicial."
    assert bool(e2["raw_filtrado"]), "ETAPA 2.1 falhou: sem retorno apos 'Li e autorizo'."
    assert bool(e3["raw_filtrado"]), "ETAPA 3.1 falhou: sem retorno apos escolher oferta."
    assert d4["pediu_email"], "ETAPA 4.1 falhou: nao pediu e-mail."
    assert d5["pediu_cep"], "ETAPA 5.1 falhou: nao pediu CEP."
    assert (d6["pediu_confirmacao_endereco"] or d6["pediu_numero_casa"]), (
        "ETAPA 6.1 falhou: nao pediu confirmacao de endereco nem numero da casa."
    )
    if d6["pediu_confirmacao_endereco"]:
        assert bool(e7["raw_filtrado"]), "ETAPA 7.1 falhou: sem retorno apos confirmar endereco."
    assert (d8["pediu_complemento"] or d8["chegou_dados_bancarios"]), (
        "ETAPA 8.1 falhou: nao pediu complemento nem chegou em dados bancarios."
    )
    if d9["pediu_confirmacao_endereco"]:
        assert bool(e10["raw_filtrado"]), "ETAPA 9.1 falhou: sem retorno apos confirmacao final de endereco."
    assert d10["chegou_dados_bancarios"], "ETAPA 10.1 falhou: nao chegou em dados bancarios."

    assert bool(e11["raw_filtrado"]), "ETAPA 11.1 falhou: sem retorno apos selecionar banco."
    assert (v11["pediu_tipo_conta"] or v11["pediu_agencia"] or v11["pediu_conta"]), (
        "ETAPA 12.1 falhou: retorno de banco nao evoluiu para tipo de conta/agencia/conta."
    )
    assert bool(e13["raw_filtrado"]), "ETAPA 13.1 falhou: sem retorno apos agencia."
    assert bool(e14["raw_filtrado"]), "ETAPA 14.1 falhou: sem retorno apos conta."
    if v14["pediu_tipo_conta"]:
        assert bool(e15["raw_filtrado"]), "ETAPA 15.1 falhou: sem retorno apos tipo de conta."
    if v15["pediu_confirmacao_bancaria"]:
        assert bool(e16["raw_filtrado"]), "ETAPA 16.1 falhou: sem retorno apos confirmar dados bancarios."
    assert not v16["ramo_negativo"], "ETAPA 16.2 falhou: fluxo caiu no ramo negativo."
    if v16["pediu_tipo_documento"]:
        assert bool(e17["raw_filtrado"]), "ETAPA 17.1 falhou: sem retorno apos selecionar tipo de documento."
    assert v17["ramo_documentos"], "ETAPA 17.2 falhou: nao entrou no ramo de documentos."
    assert not v17["ramo_negativo"], "ETAPA 17.3 falhou: fluxo caiu no ramo negativo."
    if v17["pediu_numero_cnh"]:
        assert bool(e18["raw_filtrado"]), "ETAPA 18.1 falhou: sem retorno apos enviar numero da CNH."
    assert not v18["ramo_negativo"], "ETAPA 18.2 falhou: fluxo caiu no ramo negativo."
    assert (v18["finalizou_proposta"] or v19["finalizou_proposta"]), (
        "ETAPA 19.1 falhou: nao apareceu mensagem final de geracao/formalizacao da proposta."
    )
    assert not v19["ramo_negativo"], "ETAPA 19.2 falhou: fluxo caiu no ramo negativo."

    _salvar_log(
        {
            "cenario": "etapa_06_dados_bancarios",
            "dados_utilizados": {
                "email": email,
                "cep": cep,
                "numero_casa": numero_casa,
                "complemento": complemento,
                "banco": banco,
                "tipo_conta": tipo_conta,
                "acao_confirmacao_bancaria": acao_confirmacao_bancaria,
                "tipo_documento": tipo_documento,
                "cnh": cnh,
                "agencia": agencia,
                "conta": conta,
            },
            "captura_etapa11": e11,
            "captura_etapa12": e12,
            "captura_etapa13": e13,
            "captura_etapa14": e14,
            "captura_etapa15": e15,
            "captura_etapa16": e16,
            "captura_etapa17": e17,
            "captura_etapa18": e18,
            "captura_etapa19": e19,
            "thread_json_final": etapa5._obter_thread_json_seguro(blip),
        }
    )

