"""
Logger de execução de fluxos.
Registra mensagens, respostas e JSON bruto da Blip em arquivo.
"""
import json
from datetime import datetime
from pathlib import Path


class FlowLogger:
    """Registra execução em arquivo com JSON da Blip."""

    def __init__(self, nome_fluxo: str = "fluxo"):
        self.nome_fluxo = nome_fluxo
        self.log_dir = Path(__file__).parent / "logs"
        self.log_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = self.log_dir / f"execucao_{nome_fluxo}_{ts}.json"
        self.entradas = []
        self.inicio = datetime.now().isoformat()

    def log_passo(
        self,
        passo: int,
        mensagem: str,
        resposta: str,
        status: str = "ok",
        raw_blip_json: dict = None,
        erro: str = None,
    ):
        """Registra um passo da execução."""
        entrada = {
            "timestamp": datetime.now().isoformat(),
            "passo": passo,
            "mensagem_enviada": mensagem,
            "resposta_recebida": resposta,
            "status": status,
        }
        if erro:
            entrada["erro"] = erro
        if raw_blip_json is not None:
            entrada["blip_thread_json"] = raw_blip_json
        self.entradas.append(entrada)

    def log_reset(self, user_identity: str, keys_removidas: int, sucesso: bool):
        """Registra o reset de variáveis."""
        self.entradas.insert(
            0,
            {
                "timestamp": datetime.now().isoformat(),
                "tipo": "reset",
                "user_identity": user_identity,
                "keys_removidas": keys_removidas,
                "sucesso": sucesso,
            }
        )

    def salvar(self):
        """Salva o log em arquivo JSON."""
        doc = {
            "fluxo": self.nome_fluxo,
            "inicio": self.inicio,
            "fim": datetime.now().isoformat(),
            "passos": self.entradas,
        }
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        return str(self.log_path)
