"""
Executor de fluxos de teste.
Suporta Blip API (retorno no terminal) ou WhatsApp.
Substitui placeholders {{cpf}}, {{cpf_0}}, {{cep}}, etc. pelos dados de dados_teste.json.
"""
import json
from pathlib import Path

from config import (
    BLIP_KEY,
    BLIP_BOT_ID,
    BLIP_USER_ID,
    BLIP_USER_DOMAIN,
    USE_WHATSAPP,
    WHATSAPP_NUMERO_ALVO,
    WHATSAPP_MEU_NUMERO,
    DELAY_AGUARDAR_OFERTAS,
    MENSAGENS_FINALIZAR,
    MENSAGENS_INTERROMPER,
)


def _carregar_dados_teste() -> dict:
    """Carrega dados fixos e array de CPFs."""
    path = Path(__file__).parent / "dados_teste.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _substituir_placeholders(texto: str, dados: dict, indice_cpf: int = 0) -> str:
    """Substitui {{cpf}}, {{cpf_0}}, {{cep}}, etc. pelos valores."""
    if not dados or not texto:
        return texto

    cpfs = dados.get("cpfs", [])
    substituicoes = {
        "{{cpf}}": cpfs[indice_cpf] if indice_cpf < len(cpfs) else cpfs[0] if cpfs else "{{cpf}}",
        "{{cep}}": dados.get("cep", "{{cep}}"),
        "{{numero}}": dados.get("numero_casa", "{{numero}}"),
        "{{banco}}": dados.get("banco", "{{banco}}"),
        "{{agencia}}": dados.get("agencia", "{{agencia}}"),
    }
    # {{cpf_0}}, {{cpf_1}}, etc.
    for i, cpf in enumerate(cpfs):
        substituicoes[f"{{{{cpf_{i}}}}}"] = cpf

    resultado = texto
    for placeholder, valor in substituicoes.items():
        resultado = resultado.replace(placeholder, str(valor))
    return resultado


def _get_client():
    """Retorna o cliente apropriado (Blip API ou WhatsApp/Selenium)."""
    if USE_WHATSAPP and WHATSAPP_NUMERO_ALVO:
        from whatsapp_client import WhatsAppClient
        return WhatsAppClient()
    if BLIP_KEY and BLIP_BOT_ID and BLIP_USER_ID:
        from blip_client import BlipClient
        return BlipClient()
    if WHATSAPP_NUMERO_ALVO:
        from whatsapp_client import WhatsAppClient
        return WhatsAppClient()
    raise RuntimeError(
        "Configure no .env:\n"
        "  Selenium: USE_WHATSAPP=true e WHATSAPP_NUMERO_ALVO=número_do_bot\n"
        "  Ou Blip API: BLIP_KEY, BLIP_BOT_ID, BLIP_USER_ID"
    )


def carregar_fluxo(arquivo: str = "flows/fluxo_agendar.json") -> dict:
    """Carrega definição do fluxo de um arquivo JSON."""
    path = Path(__file__).parent / arquivo
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _obter_thread_blip_para_log():
    """Obtém JSON do thread via Blip API (para log, mesmo em modo Selenium)."""
    if not (BLIP_KEY and BLIP_BOT_ID):
        return None
    try:
        from blip_client import BlipClient
        user_id = WHATSAPP_MEU_NUMERO if (USE_WHATSAPP and WHATSAPP_MEU_NUMERO) else BLIP_USER_ID
        if not user_id:
            return None
        blip = BlipClient()
        blip.user_identity = user_id.replace("+", "").replace(" ", "")
        if "@" not in blip.user_identity:
            domain = BLIP_USER_DOMAIN or "wa.gw.msging.net"
            blip.user_identity = f"{blip.user_identity}@{domain}"
        return blip.obter_thread_json()
    except Exception:
        return None


def executar_fluxo(fluxo: dict, cliente=None, exibir_no_terminal: bool = True, indice_cpf: int = 0, rotulo: str = "") -> list:
    """
    Executa um fluxo de mensagens.
    Substitui {{cpf}}, {{cep}}, etc. pelos dados de dados_teste.json.
    indice_cpf: qual CPF do array usar (0 = primeiro, 1 = segundo, etc.)
    """
    cliente = cliente or _get_client()
    dados = _carregar_dados_teste()
    resultados = []
    logger = None
    try:
        from flow_logger import FlowLogger
        logger = FlowLogger(fluxo.get("nome", "fluxo").replace(" ", "_")[:30])
    except Exception:
        pass

    # Reset de variáveis (Blip Builder) antes de iniciar
    if exibir_no_terminal and BLIP_KEY and BLIP_BOT_ID:
        try:
            from blip_client import BlipClient, _print_lock
            # Usa WHATSAPP_MEU_NUMERO quando em modo Selenium (seu número = quem envia)
            user_id = WHATSAPP_MEU_NUMERO if (USE_WHATSAPP and WHATSAPP_MEU_NUMERO) else BLIP_USER_ID
            if user_id:
                blip = BlipClient()
                blip.user_identity = user_id.replace("+", "").replace(" ", "")
                if "@" not in blip.user_identity:
                    domain = BLIP_USER_DOMAIN or "wa.gw.msging.net"
                    blip.user_identity = f"{blip.user_identity}@{domain}"
                rotulo_reset = (rotulo or fluxo.get("nome", "fluxo"))[:25] if exibir_no_terminal else ""
                if exibir_no_terminal:
                    with _print_lock:
                        print(f"  [Reset: limpando variáveis de {blip.user_identity}]")
                ok = blip.resetar_estado_usuario(exibir_log=True, rotulo=rotulo_reset)
                if logger:
                    logger.log_reset(blip.user_identity, -1, ok)
                if not ok and exibir_no_terminal:
                    from blip_client import _print_lock
                    with _print_lock:
                        print("  [Reset: verifique BLIP_USER_ID ou WHATSAPP_MEU_NUMERO = seu número no WhatsApp]")
        except Exception as e:
            if exibir_no_terminal:
                from blip_client import _print_lock
                with _print_lock:
                    print(f"  [Reset: erro - {e}]")
            if logger:
                logger.log_reset("?", 0, False)

    passos = fluxo.get("passos", fluxo.get("steps", []))
    delay_padrao = fluxo.get("delay_padrao", 3)
    # Mensagens que finalizam o fluxo (fluxo pode sobrescrever o config global)
    mensagens_finalizar = fluxo.get("mensagens_finalizacao", fluxo.get("mensagens_finalizar", MENSAGENS_FINALIZAR))
    if isinstance(mensagens_finalizar, str):
        mensagens_finalizar = [s.strip() for s in mensagens_finalizar.split("|") if s.strip()]
    elif not mensagens_finalizar:
        mensagens_finalizar = MENSAGENS_FINALIZAR
    # Mensagens que interrompem (bot não pode continuar - ex: fora do horário)
    mensagens_interromper = fluxo.get("mensagens_interromper", MENSAGENS_INTERROMPER)
    if isinstance(mensagens_interromper, str):
        mensagens_interromper = [s.strip() for s in mensagens_interromper.split("|") if s.strip()]
    elif not mensagens_interromper:
        mensagens_interromper = MENSAGENS_INTERROMPER

    for i, passo in enumerate(passos):
        mensagem = passo.get("mensagem", passo.get("message", passo.get("texto", "")))
        mensagem = _substituir_placeholders(mensagem, dados, indice_cpf)
        delay = passo.get("delay", passo.get("aguardar", delay_padrao))
        resposta_esperada = passo.get("resposta_esperada", None)
        if passo.get("aguardar_oferta"):
            delay = max(delay, DELAY_AGUARDAR_OFERTAS)
            if exibir_no_terminal:
                print(f"  [Aguardando API de ofertas ({int(delay)}s)...]")

        try:
            if exibir_no_terminal:
                print(f"\n  → Enviado: {mensagem}")
            resultado_bot = cliente.enviar_e_aguardar(mensagem, delay=delay, exibir_resposta=exibir_no_terminal)

            # Compatibilidade: resultado pode ser dict (BlipClient novo) ou str (WhatsApp/antigo)
            if isinstance(resultado_bot, dict):
                resposta = resultado_bot.get("texto", "")
                mensagens_bot = resultado_bot.get("mensagens", [])
                raw_msgs = resultado_bot.get("raw", [])
            else:
                resposta = resultado_bot or ""
                mensagens_bot = [resposta] if resposta else []
                raw_msgs = []

            raw_blip = _obter_thread_blip_para_log() if logger else None

            status_passo = "ok"
            resp_lower = resposta.lower()

            # 1. Validar resposta_esperada (se definida no passo)
            if status_passo == "ok" and resposta_esperada and resposta:
                termos = resposta_esperada if isinstance(resposta_esperada, list) else [resposta_esperada]
                encontrou = any(t.lower() in resp_lower for t in termos)
                if not encontrou:
                    status_passo = "resposta_inesperada"
                    if exibir_no_terminal:
                        print(f"\n  [PAROU: resposta do bot não contém '{resposta_esperada}']")
                        print(f"  [Esperado: {resposta_esperada}]")
                        print(f"  [Recebido ({len(mensagens_bot)} msg):]")
                        for j, msg in enumerate(mensagens_bot):
                            print(f"    {j+1}. {msg[:200]}")

            # 2. Verificar mensagens de interrupção (ex: fora do horário)
            if status_passo == "ok" and resposta and mensagens_interromper:
                for termo in mensagens_interromper:
                    if termo.lower() in resp_lower:
                        status_passo = "interrompido"
                        if exibir_no_terminal:
                            print(f"\n  [Fluxo interrompido: bot retornou resposta inesperada (ex: fora do horário)]")
                            print(f"  [Não enviando mais mensagens - valide o retorno antes de continuar]")
                        break

            # 3. Verificar mensagens de finalização
            if status_passo == "ok" and mensagens_finalizar and resposta:
                for termo in mensagens_finalizar:
                    if termo.lower() in resp_lower:
                        status_passo = "finalizado"
                        if exibir_no_terminal:
                            print(f"\n  [Fluxo finalizado: bot enviou mensagem de conclusão]")
                        break

            resultados.append({
                "passo": i + 1,
                "mensagem": mensagem,
                "resposta": resposta,
                "mensagens_bot": mensagens_bot,
                "raw": raw_msgs,
                "status": status_passo,
            })
            if logger:
                logger.log_passo(i + 1, mensagem, resposta, status_passo, raw_blip_json=raw_blip)

            if status_passo in ("finalizado", "interrompido", "resposta_inesperada"):
                if logger:
                    path = logger.salvar()
                    if exibir_no_terminal:
                        print(f"\n  [Log salvo: {path}]")
                return resultados
        except Exception as e:
            if exibir_no_terminal:
                print(f"    ✗ Erro: {e}")
            resultados.append({
                "passo": i + 1,
                "mensagem": mensagem,
                "status": "erro",
                "erro": str(e),
            })
            if logger:
                logger.log_passo(i + 1, mensagem, "", "erro", erro=str(e))
            raise

    if logger:
        path = logger.salvar()
        if exibir_no_terminal:
            print(f"\n  [Log salvo: {path}]")
    return resultados
