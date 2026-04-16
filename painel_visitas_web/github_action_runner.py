from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from services.integrations import (
    clear_mercadofarma_mass_order,
    load_creds,
    run_bussola_download,
    run_mercadofarma_inventory,
    run_mercadofarma_mass_order,
)
from services.repo_state import load_commands, load_status, save_commands, save_status


STATUS_REL = "data/status_atualizacao.json"
COMMANDS_REL = "data/comandos_remotos.json"


def _candidate_json_paths(rel_path: str) -> list[Path]:
    rel = Path(rel_path)
    paths = [ROOT.parent / rel]
    if rel.parts and rel.parts[0] == "data":
        paths.append(DATA / rel.name)
    uniq = []
    seen = set()
    for p in paths:
        s = str(p)
        if s not in seen:
            uniq.append(p)
            seen.add(s)
    return uniq


def _load_json_local(rel_path: str, default):
    for p in _candidate_json_paths(rel_path):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return default


def _save_json_local(rel_path: str, data: dict) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2)
    for p in _candidate_json_paths(rel_path):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _load_status_local() -> dict:
    return _load_json_local(
        STATUS_REL,
        {
            "bussola": {"ultimo_sucesso": "", "status": "nunca", "mensagem": ""},
            "mercadofarma": {"ultimo_sucesso": "", "status": "nunca", "mensagem": ""},
            "github_actions": {"status": "nunca", "mensagem": ""},
            "comandos": {"ultimo_resultado": "", "status": "nunca"},
        },
    )


def _save_status_local(status: dict) -> None:
    _save_json_local(STATUS_REL, status)


def _load_commands_local() -> dict:
    return _load_json_local(COMMANDS_REL, {"commands": []})


def _save_commands_local(cmds: dict) -> None:
    _save_json_local(COMMANDS_REL, cmds)

DATA = ROOT / "data"
TZ_BR = ZoneInfo("America/Sao_Paulo")

ALIASES = {
    "atualizar_bussola": "atualizar_bussola",
    "atualizar_mercadofarma": "atualizar_mercadofarma",
    "atualizar_mercado_farma": "atualizar_mercadofarma",
    "limpar_pedido_mf": "limpar_pedido_mf",
    "clear_pedido_mf": "limpar_pedido_mf",
    "enviar_pedido_mf": "enviar_pedido_mf",
    "gerar_pedido_mercado_farma": "enviar_pedido_mf",
}


def now() -> str:
    return datetime.now(TZ_BR).strftime("%d/%m/%Y %H:%M:%S")


def fmt_mtime(path: Path) -> str:
    dt = datetime.fromtimestamp(path.stat().st_mtime, tz=TZ_BR)
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def _bool(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "sim", "yes", "y", "on"}


def _canon(acao: str | None) -> str:
    raw = str(acao or "").strip()
    return ALIASES.get(raw, raw)


def _produtos_df() -> pd.DataFrame:
    path = DATA / "PRODUTOS COM EAN - POR LANCAMENTOS-PRIORITARIOS-LINHA.xlsx"
    if path.exists():
        return pd.read_excel(path)
    return pd.DataFrame()


def _load_pedido_payload() -> list[dict]:
    p = DATA / "pedido_payload.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("cart_items", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _update_status_key(chave: str, status_text: str, mensagem: str = "", arquivo_ref: Path | None = None, ultimo_resultado: str = ""):
    status = _load_status_local()
    status.setdefault(chave, {})
    ultimo_sucesso = status[chave].get("ultimo_sucesso", "")
    if status_text == "ok":
        if arquivo_ref is not None and arquivo_ref.exists():
            ultimo_sucesso = fmt_mtime(arquivo_ref)
        else:
            ultimo_sucesso = now()
    status[chave].update(
        {
            "ultimo_sucesso": ultimo_sucesso,
            "status": status_text,
            "mensagem": mensagem,
            "atualizado_em": now(),
        }
    )
    if chave == "comandos" and ultimo_resultado:
        status[chave]["ultimo_resultado"] = ultimo_resultado
    status.setdefault("github_actions", {})
    status["github_actions"].update(
        {
            "status": status_text,
            "mensagem": mensagem,
            "atualizado_em": now(),
        }
    )
    _save_status_local(status)


def _mark_command(command_id: str | None, status_text: str, mensagem: str):
    if not command_id:
        return
    data = _load_commands_local()
    cmds = data.get("commands", [])
    for cmd in reversed(cmds):
        if cmd.get("id") == command_id:
            cmd["status"] = status_text
            cmd["mensagem"] = mensagem
            cmd["atualizado_em"] = now()
            break
    data["commands"] = cmds[-100:]
    _save_commands_local(data)


def _resolve_from_command_id(command_id: str) -> tuple[str, dict]:
    data = _load_commands_local()
    cmds = data.get("commands", [])
    for cmd in reversed(cmds):
        if cmd.get("id") == command_id:
            return _canon(cmd.get("acao")), cmd.get("params", {}) or {}
    raise ValueError(f"Comando não encontrado: {command_id}")


def execute_direct(acao: str, headless: bool = True, cnpj: str = "", cupom: str = ""):
    acao = _canon(acao)
    creds = load_creds()
    bussola_login = os.getenv("BUSSOLA_LOGIN") or creds.bussola_login
    bussola_senha = os.getenv("BUSSOLA_SENHA") or creds.bussola_senha
    mf_login = os.getenv("MERCADOFARMA_LOGIN") or creds.mercado_login
    mf_senha = os.getenv("MERCADOFARMA_SENHA") or creds.mercado_senha
    mf_cnpj = cnpj or os.getenv("MERCADOFARMA_CNPJ") or creds.mercado_cnpj

    if acao == "atualizar_bussola":
        out = DATA / "Pedidos.xlsx"
        run_bussola_download(bussola_login, bussola_senha, out, headless=headless)
        _update_status_key("bussola", "ok", "Bússola atualizado pelo GitHub Actions.", arquivo_ref=out)
        return True, "Bússola atualizado."

    if acao == "atualizar_mercadofarma":
        out = DATA / "Estoque_preco_distribuidora.xlsx"
        run_mercadofarma_inventory(mf_login, mf_senha, mf_cnpj, _produtos_df(), out, headless=headless)
        _update_status_key("mercadofarma", "ok", "Mercado Farma atualizado pelo GitHub Actions.", arquivo_ref=out)
        return True, "Mercado Farma atualizado."

    if acao == "limpar_pedido_mf":
        clear_mercadofarma_mass_order(mf_login, mf_senha, mf_cnpj, headless=headless)
        _update_status_key("comandos", "ok", "Seleção anterior do Mercado Farma limpa.", ultimo_resultado="Pedido MF limpo")
        return True, "Pedido MF limpo."

    if acao == "enviar_pedido_mf":
        cart_items = _load_pedido_payload()
        if not cart_items:
            raise ValueError("Nenhum item encontrado em data/pedido_payload.json")
        run_mercadofarma_mass_order(mf_login, mf_senha, cart_items, headless=headless, cupom=cupom)
        _update_status_key("comandos", "ok", "Pedido enviado ao Mercado Farma pelo GitHub Actions.", ultimo_resultado="Pedido enviado ao Mercado Farma")
        return True, "Pedido enviado ao Mercado Farma."

    raise ValueError(f"Ação desconhecida: {acao}")


def main():
    parser = argparse.ArgumentParser(description="Runner do painel via GitHub Actions")
    parser.add_argument("command_id", nargs="?", help="ID opcional do comando salvo em data/comandos_remotos.json")
    parser.add_argument("--acao", default="")
    parser.add_argument("--headless", default="true")
    parser.add_argument("--cnpj", default="")
    parser.add_argument("--cupom", default="")
    args = parser.parse_args()

    command_id = args.command_id or ""

    try:
        if command_id and not args.acao:
            acao, params = _resolve_from_command_id(command_id)
            headless = _bool(params.get("headless", True))
            cnpj = str(params.get("cnpj", "") or "")
            cupom = str(params.get("cupom", "") or "")
        else:
            acao = _canon(args.acao)
            headless = _bool(args.headless)
            cnpj = args.cnpj
            cupom = args.cupom

        ok, msg = execute_direct(acao, headless=headless, cnpj=cnpj, cupom=cupom)
        _mark_command(command_id or None, "ok", msg)
        print(json.dumps({"ok": ok, "mensagem": msg}, ensure_ascii=False))
    except Exception as e:
        _mark_command(command_id or None, "erro", str(e))
        acao_falha = _canon(args.acao) if args.acao else "comandos"
        if acao_falha == "atualizar_bussola":
            _update_status_key("bussola", "erro", str(e))
        elif acao_falha == "atualizar_mercadofarma":
            _update_status_key("mercadofarma", "erro", str(e))
        else:
            _update_status_key("comandos", "erro", str(e))
        raise


if __name__ == "__main__":
    main()
