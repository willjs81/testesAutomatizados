"""
Cliente para enviar mensagens via API da Blip e obter as respostas do bot.
Exibe o retorno da API diretamente no terminal - sem WhatsApp, sem navegador.
"""
import time
import uuid
import threading
import requests

# Lock para modo multithread (evita output embaralhado)
_print_lock = threading.Lock()

from config import (
    BLIP_KEY,
    BLIP_BOT_ID,
    BLIP_USER_ID,
    BLIP_USER_DOMAIN,
    DELAY_ENTRE_MENSAGENS,
    DELAY_APOS_RESET,
    DEBUG_BLIP,
)


class BlipClient:
    """Cliente Blip API - envia mensagem e obtém resposta do bot."""

    def __init__(self):
        self.base_url = f"https://{BLIP_BOT_ID}.http.msging.net"
        self.base_url_central = "https://msging.net"
        key = BLIP_KEY.strip()
        if not key.upper().startswith("KEY "):
            key = f"Key {key}"
        self.headers = {
            "Authorization": key,
            "Content-Type": "application/json",
        }
        self.user_identity = BLIP_USER_ID.replace("+", "").replace(" ", "")
        if "@" not in self.user_identity:
            domain = BLIP_USER_DOMAIN or "wa.gw.msging.net"
            self.user_identity = f"{self.user_identity}@{domain}"
        self.bot_identity = f"{BLIP_BOT_ID}@msging.net"
        self.delay = DELAY_ENTRE_MENSAGENS

        if DEBUG_BLIP:
            print(f"[DEBUG] Base URL: {self.base_url}")
            print(f"[DEBUG] Central URL: {self.base_url_central}")
            print(f"[DEBUG] User: {self.user_identity} | Bot: {self.bot_identity}")

    def _extrair_chaves_contexto(self, data: dict) -> list:
        """Extrai lista de chaves do resource (suporta diferentes formatos da API)."""
        resource = data.get("resource")
        if resource is None:
            return []
        if isinstance(resource, list):
            return [x if isinstance(x, str) else x.get("id", x.get("name", str(x))) for x in resource]
        items = resource.get("items", [])
        if not items:
            if "item" in resource:
                return [resource["item"]] if resource["item"] else []
            return []
        # Items podem ser strings ou objetos com id/name
        chaves = []
        for x in items:
            if isinstance(x, str):
                chaves.append(x)
            elif isinstance(x, dict):
                chaves.append(x.get("id") or x.get("name") or x.get("key") or str(x))
            else:
                chaves.append(str(x))
        return chaves

    def obter_contexto_atual(self, destino: str = "postmaster@msging.net", url: str = None) -> list:
        """GET do contexto atual - retorna lista de variáveis.
        Usa withContextValues=true para pegar variáveis ocultas (ex: current-flow-id)."""
        base = url or f"{self.base_url}/commands"
        all_keys = set()

        for with_values in (True, False):
            qs = "?withContextValues=true&$take=1000" if with_values else "?$take=1000"
            try:
                resp = requests.post(
                    base,
                    headers=self.headers,
                    json={
                        "id": str(uuid.uuid4()),
                        "method": "get",
                        "uri": f"/contexts/{self.user_identity}{qs}",
                        "to": destino,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                all_keys.update(self._extrair_chaves_contexto(resp.json()))
            except Exception:
                pass

        return list(all_keys)

    def _deletar_todas_variaveis(self, destino: str, prefixo: str = "  ", url: str = None) -> int:
        """GET + DELETE em loop até zerar. Retorna qtd total removidas."""
        base = url or f"{self.base_url}/commands"
        total_removidas = 0

        for _ in range(10):
            variaveis = self.obter_contexto_atual(destino, url=base)
            if not variaveis:
                break

            if DEBUG_BLIP and total_removidas == 0:
                with _print_lock:
                    print(f"{prefixo}[DEBUG] {destino}: {len(variaveis)} variável(is)")
                    for v in variaveis[:10]:
                        print(f"{prefixo}  - {v}")
                    if len(variaveis) > 10:
                        print(f"{prefixo}  ... +{len(variaveis) - 10}")

            for key in variaveis:
                try:
                    r = requests.post(
                        base,
                        headers=self.headers,
                        json={
                            "id": str(uuid.uuid4()),
                            "method": "delete",
                            "uri": f"/contexts/{self.user_identity}/{key}",
                            "to": destino,
                        },
                        timeout=30,
                    )
                    r.raise_for_status()
                    if r.json().get("status") != "failure":
                        total_removidas += 1
                except Exception:
                    pass

            time.sleep(0.3)

        return total_removidas

    def resetar_estado_usuario(self, exibir_log: bool = False, rotulo: str = "") -> bool:
        """
        Limpa TODAS as variáveis de contexto do usuário usando dois endpoints:
        1. Central (msging.net) - acessa variáveis de sub-bots via Router
        2. Bot-specific - acessa variáveis diretas do Router
        Ambos enviam para postmaster@msging.net (loop até zerar).
        """
        prefixo = f"[{rotulo}] " if rotulo else "  "
        url_central = f"{self.base_url_central}/commands"
        url_bot = f"{self.base_url}/commands"

        try:
            keys_removidas = 0

            # 1. Endpoint CENTRAL (acessa variáveis de sub-bots)
            keys_removidas += self._deletar_todas_variaveis(
                "postmaster@msging.net", prefixo, url=url_central)

            # 2. Endpoint BOT-SPECIFIC (variáveis do Router)
            keys_removidas += self._deletar_todas_variaveis(
                "postmaster@msging.net", prefixo, url=url_bot)

            if exibir_log:
                with _print_lock:
                    print(f"{prefixo}[Reset: {keys_removidas} variável(is) removida(s)]")

            time.sleep(DELAY_APOS_RESET)

            # Verificação
            if exibir_log:
                rest1 = self.obter_contexto_atual("postmaster@msging.net", url=url_central)
                rest2 = self.obter_contexto_atual("postmaster@msging.net", url=url_bot)
                with _print_lock:
                    if rest1 or rest2:
                        print(f"{prefixo}[AVISO] Restantes: central={len(rest1)} bot={len(rest2)}")
                    else:
                        print(f"{prefixo}[Reset: OK - contexto limpo]")

            return True
        except Exception as e:
            if exibir_log:
                with _print_lock:
                    print(f"{prefixo}[Reset: falhou - {e}]")
            return False

    def _enviar_mensagem(self, texto: str) -> None:
        """Envia mensagem para o bot (simulando o usuário)."""
        payload = {
            "id": str(uuid.uuid4()),
            "from": self.user_identity,
            "to": self.bot_identity,
            "type": "text/plain",
            "content": texto,
        }
        url = f"{self.base_url}/messages"
        if DEBUG_BLIP:
            print(f"[DEBUG] POST {url}")
        response = requests.post(url, headers=self.headers, json=payload, timeout=30)
        response.raise_for_status()

    def _obter_items_thread(self):
        """Obtém os itens do thread (histórico de mensagens)."""
        data = self.obter_thread_json()
        return data.get("resource", {}).get("items", [])

    def obter_thread_json(self) -> dict:
        """Retorna o JSON completo do thread (para log)."""
        payload = {
            "id": str(uuid.uuid4()),
            "method": "get",
            "uri": f"/threads/{self.user_identity}?refreshExpiredMedia=true",
        }
        response = requests.post(
            f"{self.base_url}/commands",
            headers=self.headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _extrair_texto_conteudo(self, content) -> str:
        """Extrai texto de content (string, dict, ou mensagem interativa WhatsApp)."""
        if isinstance(content, str):
            return content
        if not isinstance(content, dict):
            return str(content)
        # Texto direto
        if "text" in content:
            return content["text"]
        # Mensagem interativa WhatsApp (cta_url, list, etc.)
        interactive = content.get("interactive", {})
        body = interactive.get("body", {})
        if isinstance(body, dict) and "text" in body:
            return body["text"]
        if isinstance(body, str):
            return body
        return str(content)[:500]

    def _extrair_ultima_resposta(self, items: list) -> str:
        """Extrai o texto da última mensagem SENT (do bot)."""
        if not items:
            return "[sem mensagens no histórico]"
        for item in reversed(items):
            direction = (item.get("direction") or "").lower()
            if direction == "sent":
                content = item.get("content", "")
                return self._extrair_texto_conteudo(content)
        return "[resposta do bot não encontrada]"

    def _extrair_novas_respostas(self, items: list, ultimo_id_antes: str) -> list:
        """Extrai TODAS as mensagens SENT (do bot) que vieram DEPOIS de ultimo_id_antes.
        Trata items em qualquer ordem (cronológica ou reversa).
        Retorna lista de dicts: [{"texto": str, "raw": dict}, ...]"""
        if ultimo_id_antes is None:
            msgs = [it for it in items if (it.get("direction") or "").lower() == "sent"]
            if msgs:
                last = msgs[-1] if msgs else items[-1]
                return [{
                    "texto": self._extrair_texto_conteudo(last.get("content", "")),
                    "raw": last,
                }]
            return []

        # Encontrar posição do marco (ultimo_id_antes) nos items
        marco_idx = None
        for idx, item in enumerate(items):
            if item.get("id") == ultimo_id_antes:
                marco_idx = idx
                break

        if marco_idx is None:
            # Marco não encontrado - items pode estar em ordem reversa
            # Tenta buscar invertendo
            for idx, item in enumerate(reversed(items)):
                if item.get("id") == ultimo_id_antes:
                    marco_idx = len(items) - 1 - idx
                    break

        if marco_idx is None:
            # Não encontrou o marco - retorna todas as SENT como fallback
            respostas = []
            for item in items:
                if (item.get("direction") or "").lower() == "sent":
                    respostas.append({
                        "texto": self._extrair_texto_conteudo(item.get("content", "")),
                        "raw": item,
                    })
            return respostas

        # Pega tudo depois do marco
        respostas = []
        for item in items[marco_idx + 1:]:
            if (item.get("direction") or "").lower() == "sent":
                respostas.append({
                    "texto": self._extrair_texto_conteudo(item.get("content", "")),
                    "raw": item,
                })

        # Se não achou nada depois, items pode estar em ordem reversa (mais recente primeiro)
        if not respostas:
            for item in items[:marco_idx]:
                if (item.get("direction") or "").lower() == "sent":
                    respostas.append({
                        "texto": self._extrair_texto_conteudo(item.get("content", "")),
                        "raw": item,
                    })
            respostas.reverse()

        return respostas

    def _ultimo_id_bot(self, items: list):
        """Retorna o ID da última mensagem SENT (do bot), ou None."""
        for item in reversed(items):
            if (item.get("direction") or "").lower() == "sent":
                return item.get("id")
        return None

    def _exibir_resposta(self, texto: str, prefixo: str = "    ← Blip API: ") -> None:
        """Exibe a resposta completa, quebrando em linhas longas."""
        if not texto:
            return
        # Exibe texto completo, quebrando em linhas de até 120 chars
        linhas = []
        for linha in texto.replace("\r", "").split("\n"):
            while len(linha) > 120:
                linhas.append(linha[:120])
                linha = linha[120:]
            linhas.append(linha)
        for i, ln in enumerate(linhas):
            p = prefixo if i == 0 else " " * len(prefixo)
            print(f"{p}{ln}")

    def enviar_e_aguardar(
        self,
        texto: str,
        delay: float = None,
        exibir_resposta: bool = True,
        timeout_resposta: float = 90,
    ) -> dict:
        """
        Envia mensagem, aguarda a resposta do bot (polling) e retorna.
        Retorna dict com:
          - "texto": texto completo (todas as msgs do bot concatenadas)
          - "mensagens": lista de textos individuais do bot
          - "raw": lista de dicts raw de cada mensagem
        """
        # ID da última mensagem do bot antes de enviar
        try:
            items = self._obter_items_thread()
            ultimo_id_antes = self._ultimo_id_bot(items)
        except Exception:
            ultimo_id_antes = None

        self._enviar_mensagem(texto)

        delay_inicial = delay if delay is not None else self.delay
        time.sleep(delay_inicial)

        poll_interval = 2.0
        elapsed = delay_inicial

        while elapsed < timeout_resposta:
            time.sleep(poll_interval)
            elapsed += poll_interval

            try:
                items = self._obter_items_thread()
                ultimo_id_agora = self._ultimo_id_bot(items)

                if ultimo_id_agora and ultimo_id_agora != ultimo_id_antes:
                    # Bot respondeu. Esperar mais mensagens: poll até estabilizar.
                    for _ in range(3):
                        time.sleep(3)
                        items2 = self._obter_items_thread()
                        id2 = self._ultimo_id_bot(items2)
                        if id2 == ultimo_id_agora:
                            break
                        items = items2
                        ultimo_id_agora = id2
                    else:
                        items = self._obter_items_thread()

                    return self._montar_resultado(items, ultimo_id_antes, exibir_resposta)
            except Exception:
                pass

        # Timeout
        try:
            items = self._obter_items_thread()
            resultado = self._montar_resultado(items, ultimo_id_antes, False)
            if exibir_resposta:
                print("    ← Blip API (timeout):")
                self._exibir_resposta(resultado["texto"], prefixo="      ")
            resultado["timeout"] = True
            return resultado
        except Exception:
            return {
                "texto": "[timeout aguardando resposta do bot]",
                "mensagens": [],
                "raw": [],
                "timeout": True,
            }

    def _montar_resultado(self, items: list, ultimo_id_antes: str, exibir: bool) -> dict:
        """Monta o dict de resultado com todas as mensagens do bot."""
        novas = self._extrair_novas_respostas(items, ultimo_id_antes)

        if novas:
            textos = [r["texto"] for r in novas]
            texto_completo = "\n\n".join(textos)
            raw_list = [r["raw"] for r in novas]
        else:
            texto_completo = self._extrair_ultima_resposta(items)
            textos = [texto_completo] if texto_completo else []
            raw_list = []

        if exibir and texto_completo:
            for i, txt in enumerate(textos):
                prefixo = "    ← Bot: " if i == 0 else "    ← Bot: "
                self._exibir_resposta(txt, prefixo=prefixo)

        return {
            "texto": texto_completo,
            "mensagens": textos,
            "raw": raw_list,
        }
