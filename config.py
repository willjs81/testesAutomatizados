"""
Configurações para automação de testes do chatbot.
Suporta Blip API (retorno direto no terminal) ou WhatsApp.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Blip API (retorno direto no terminal, sem navegador) ---
BLIP_KEY = os.getenv("BLIP_KEY", "")  # Key base64 do portal Blip
BLIP_BOT_ID = os.getenv("BLIP_BOT_ID", "")  # Ex: labclinicadev
BLIP_USER_ID = os.getenv("BLIP_USER_ID", "")  # Número para simular (ex: 5511999999999)
# Flow ID (opcional): Builder > Configurações > Flow Identifier - para reset de stateid
BLIP_FLOW_ID = os.getenv("BLIP_FLOW_ID", "")

# Sub-bot(s): quando o fluxo roda em tunnel, o contexto fica nos sub-bots.
# Vários separados por vírgula: bmgcascatadev,bmgsystemoutdev,bmgcpfdev
# No log: #tunnel.originalFrom = "NOME@msging.net" -> use NOME
BLIP_SUB_BOT_ID = os.getenv("BLIP_SUB_BOT_ID", "").strip()
BLIP_SUB_BOT_IDS = [s.strip() for s in os.getenv("BLIP_SUB_BOT_IDS", "bmgcascatadev,bmgsystemoutdev,bmgcpfdev,bmglinkdev,bmgin100dev,bmgemprestimosdev").split(",") if s.strip()]
BLIP_SUB_BOT_KEY = os.getenv("BLIP_SUB_BOT_KEY", "").strip()

# Delay após reset (segundos) - tempo para o bot processar a limpeza (3s ajuda quando há muitos sub-bots)
DELAY_APOS_RESET = float(os.getenv("DELAY_APOS_RESET", "3"))

# Workers paralelos para DELETE no reset (0 = sequencial, 15 = ~10x mais rápido)
RESET_PARALLEL_WORKERS = int(os.getenv("RESET_PARALLEL_WORKERS", "15"))

# Repetir o reset N vezes
RESET_REPETIR = int(os.getenv("RESET_REPETIR", "2"))

# --- WhatsApp (Selenium - mensagens reais, aparecem no Beholder) ---
USE_WHATSAPP = os.getenv("USE_WHATSAPP", "").lower() in ("1", "true", "yes")
WHATSAPP_NUMERO_ALVO = os.getenv("WHATSAPP_NUMERO_ALVO", "")  # Número do BOT no WhatsApp
# Seu número no WhatsApp (para reset). Se vazio, usa BLIP_USER_ID
WHATSAPP_MEU_NUMERO = os.getenv("WHATSAPP_MEU_NUMERO", "").replace("+", "").replace(" ", "")
# Se Chrome crashar: USE_WHATSAPP_PROFILE=false (vai pedir QR toda vez)
USE_WHATSAPP_PROFILE = os.getenv("USE_WHATSAPP_PROFILE", "true").lower() in ("1", "true", "yes")

# Pausar antes de iniciar? false = executa direto sem pedir Enter
PAUSE_ANTES_INICIAR = os.getenv("PAUSE_ANTES_INICIAR", "true").lower() not in ("0", "false", "no")

# Delay entre mensagens (segundos)
DELAY_ENTRE_MENSAGENS = float(os.getenv("DELAY_ENTRE_MENSAGENS", "3"))

# Simulação de digitação humana (WhatsApp) - ms por caractere
DELAY_DIGITACAO_MS = int(os.getenv("DELAY_DIGITACAO_MS", "120"))
DELAY_ANTES_DIGITAR = float(os.getenv("DELAY_ANTES_DIGITAR", "0.8"))  # segundos antes de começar

# Delay após "Li e autorizo" (API de ofertas demora)
DELAY_AGUARDAR_OFERTAS = float(os.getenv("DELAY_AGUARDAR_OFERTAS", "60"))

# Domínio do usuário (padrão WhatsApp). Se não aparecer no Beholder, tente: 0mn.io
BLIP_USER_DOMAIN = os.getenv("BLIP_USER_DOMAIN", "wa.gw.msging.net")

# Debug: exibe URL e identidades usadas (para troubleshoot do Beholder)
DEBUG_BLIP = os.getenv("DEBUG_BLIP", "").lower() in ("1", "true", "yes")

# Mensagens do bot que indicam finalização (o fluxo para ao receber)
# Separadas por | ex: "Recebi aqui a sua solicitação|em breve retornei"
MENSAGENS_FINALIZAR = [s.strip() for s in os.getenv("MENSAGENS_FINALIZAR", "Recebi aqui a sua solicitação|em breve retornei").split("|") if s.strip()]

# Mensagens que indicam que o bot NÃO pode continuar - fluxo para e não envia mais mensagens
# Ex: fora do horário, serviço indisponível, etc.
MENSAGENS_INTERROMPER = [s.strip() for s in os.getenv("MENSAGENS_INTERROMPER", "fora do horário|horário permitido|6h às 22h|consulta ainda está fora").split("|") if s.strip()]
