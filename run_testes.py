#!/usr/bin/env python
"""
Menu simples para executar suites de teste do bot.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _print_menu() -> None:
    print("\n=== Menu de Testes ===")
    print("1) Fluxo feliz - com seguro")
    print("2) Fluxo feliz - sem seguro")
    print("3) Etapa 1 - mensagem inicial")
    print("4) Etapa 2 - termo/autorizacao")
    print("5) Etapa 3 - escolha de oferta (duas opcoes)")
    print("6) Fluxo completo atual (etapa 2 + etapa 3)")
    print("7) Fluxo seguro sem edicao E2E")
    print("8) Fluxo seguro com edicao (Alterar valor)")
    print("9) Etapa 5 - dados pessoais ate dados bancarios")
    print("10) Etapa 6 - menu banco/agencia/conta")
    print("0) Sair")


def _ask_yes_no(label: str, default: bool) -> bool:
    hint = "S/n" if default else "s/N"
    val = input(f"{label} [{hint}]: ").strip().lower()
    if not val:
        return default
    return val in ("s", "sim", "y", "yes")


def _ask_optional(label: str) -> str:
    return input(f"{label} (Enter para padrão): ").strip()


def _normalizar_cpf(cpf: str) -> str:
    if not cpf:
        return ""
    return re.sub(r"\D+", "", cpf)


def _build_pytest_args(choice: str) -> list[str]:
    if choice == "1":
        return [
            "tests/test_etapa_03_escolha_oferta.py",
            "-m",
            "integration and manual_reset",
            "-k",
            "com_seguro",
        ]
    if choice == "2":
        return [
            "tests/test_etapa_03_escolha_oferta.py",
            "-m",
            "integration and manual_reset",
            "-k",
            "sem_seguro",
        ]
    if choice == "3":
        return [
            "tests/test_etapa_inicial.py",
            "-m",
            "integration",
        ]
    if choice == "4":
        return [
            "tests/test_etapa_02_termo_autorizacao.py",
            "-m",
            "integration and etapa2 and manual_reset",
        ]
    if choice == "5":
        return [
            "tests/test_etapa_03_escolha_oferta.py",
            "-m",
            "integration and manual_reset",
        ]
    if choice == "6":
        return [
            "tests/test_etapa_02_termo_autorizacao.py",
            "tests/test_etapa_03_escolha_oferta.py",
            "-m",
            "integration and manual_reset",
        ]
    if choice == "7":
        return [
            "tests/fluxoSeguroSemEdicaoE2E.py",
            "-m",
            "integration and manual_reset",
        ]
    if choice == "8":
        return [
            "tests/test_etapa_04_fluxo_seguro.py",
            "-m",
            "integration and manual_reset",
            "-k",
            "com_edicao",
        ]
    if choice == "9":
        return [
            "tests/test_etapa_05_dados_pessoais.py",
            "-m",
            "integration and manual_reset",
        ]
    if choice == "10":
        return [
            "tests/test_etapa_06_dados_bancarios.py",
            "-m",
            "integration and manual_reset",
        ]
    raise ValueError("Opcao invalida.")


def _registrar_erro_runner(choice: str, exc: Exception) -> str:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"erro_runner_menu_{choice}_{ts}.log"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"timestamp={datetime.now().isoformat()}\n")
        f.write(f"choice={choice}\n")
        f.write(f"python={sys.executable}\n")
        f.write(f"exception={repr(exc)}\n\n")
        f.write(traceback.format_exc())
    return str(path)


def _executar_uma_opcao(choice: str) -> int:
    args = _build_pytest_args(choice)
    gerar_html = _ask_yes_no("Gerar relatorio HTML?", True)
    pausar_reset = _ask_yes_no("Pausar para reset manual?", True)
    cpf = _normalizar_cpf(_ask_optional("CPF para mensagem inicial"))
    codigo_solicitacao = _ask_optional("Codigo de solicitacao para mensagem inicial")

    env = os.environ.copy()
    env["PAUSAR_PARA_RESET_MANUAL"] = "true" if pausar_reset else "false"
    if cpf:
        env["TESTE_CPF"] = cpf
    else:
        env.pop("TESTE_CPF", None)
    if codigo_solicitacao:
        env["TESTE_CODIGO_SOLICITACAO"] = codigo_solicitacao
    else:
        env.pop("TESTE_CODIGO_SOLICITACAO", None)

    cmd = [sys.executable, "-m", "pytest"] + args + ["--capture=tee-sys"]
    if gerar_html:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = f"logs/report_menu_{choice}_{ts}.html"
        cmd.extend(["--html", html_path, "--self-contained-html"])
        print(f"Relatorio HTML: {html_path}")

    print("\nExecutando comando:")
    print(" ".join(cmd))
    print()

    proc = subprocess.run(cmd, env=env)
    return proc.returncode


def main() -> int:
    while True:
        _print_menu()
        choice = input("Escolha uma opcao: ").strip()
        if choice == "0":
            print("Saindo.")
            return 0

        try:
            codigo = _executar_uma_opcao(choice)
            if codigo == 0:
                print("\n[menu] Execucao finalizada com sucesso.")
            else:
                print(f"\n[menu] Execucao finalizada com falha (exit code {codigo}).")
        except ValueError as exc:
            print(str(exc))
        except Exception as exc:
            log_path = _registrar_erro_runner(choice, exc)
            print(f"\n[erro] Falha inesperada no runner. Log salvo em: {log_path}")

        input("\nPressione Enter para voltar ao menu...")


if __name__ == "__main__":
    raise SystemExit(main())
