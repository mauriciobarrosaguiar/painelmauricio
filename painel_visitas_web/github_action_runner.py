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


STATUS_REL = "data/status_atualizacao.json"
COMMANDS_REL = "data/comandos_remotos.json"
DATA = ROOT / "data"
TZ_BR = ZoneInfo("America/Sao_Paulo")
ACTION_STATUS_KEYS = {
    "atualizar_bussola": "bussola",
    "atualizar_mercadofarma": "mercadofarma",
    "atualizar_mercado_farma": "mercadofarma",
    "limpar_pedido_mf": "comandos",
    "clear_pedido_mf": "comandos",
    "enviar_pedido_mf": "comandos",
    "gerar_pedido_mercado_farma": "comandos",
}
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


def _candidate_json_paths(rel_path: str) -> list[Path]:
    rel = Path(rel_path)
    paths = [ROOT.parent / rel]
    if rel.parts and rel.parts[0] == "data":
        paths.append(DATA / rel.name)
    uniq: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            uniq.append(path)
            seen.add(key)
    return uniq


def _load_json_local(rel_path: str, default):
    for path in _candidate_json_paths(rel_path):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return default


def _save_json_local(rel_path: str, data: dict) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2)
    for path in _candidate_json_paths(rel_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _load_status_local() -> dict:
    return _load_json_local(
        STATUS_REL,
        {
            "bussola": {},
            "mercadofarma": {},
            "github_actions": {},
            "comandos": {"ultimo_resultado": ""},
        },
    )


def _save_status_local(status: dict) -> None:
    _save_json_local(STATUS_REL, status)


def _load_commands_local() -> dict:
    return _load_json_local(COMMANDS_REL, {"commands": []})


def _save_commands_local(cmds: dict) -> None:
    _save_json_local(COMMANDS_REL, cmds)


def _bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "sim", "yes", "y", "on"}


def _canon(acao: str | None) -> str:
    raw = str(acao or "").strip()
    return ALIASES.get(raw, raw)


def _status_key(acao: str) -> str:
    return ACTION_STATUS_KEYS.get(_canon(acao), "comandos")


def _empty_progress() -> dict[str, int]:
    return {"atual": 0, "total": 0, "percentual": 0}


def _touch_status_block(bloco: dict | None = None) -> dict:
    base = dict(bloco or {})
    base.setdefault("ultimo_sucesso", "")
    base.setdefault("status", "nunca")
    base.setdefault("mensagem", "")
    base.setdefault("atualizado_em", "")
    base.setdefault("ultimo_comando_id", "")
    base.setdefault("etapa_atual", "")
    base.setdefault("erro", "")
    base.setdefault("resumo", {})
    base.setdefault("eventos", [])
    base.setdefault("progresso", _empty_progress())
    return base


def _event(texto: str, nivel: str = "info") -> dict[str, str]:
    return {"quando": now(), "texto": str(texto or "").strip(), "nivel": nivel}


def _progress_dict(atual=None, total=None) -> dict[str, int]:
    current = int(atual or 0)
    maximum = int(total or 0)
    percent = int((current / maximum) * 100) if maximum > 0 else 0
    return {"atual": current, "total": maximum, "percentual": percent}


def _update_command_local(
    command_id: str | None,
    *,
    status_text: str,
    mensagem: str,
    etapa: str = "",
    atual=None,
    total=None,
    erro: str = "",
    resumo: dict | None = None,
    nivel: str = "info",
) -> None:
    if not command_id:
        return
    data = _load_commands_local()
    cmds = data.get("commands", [])
    for cmd in reversed(cmds):
        if cmd.get("id") != command_id:
            continue
        cmd["status"] = status_text
        cmd["mensagem"] = mensagem
        cmd["atualizado_em"] = now()
        if etapa:
            cmd["etapa_atual"] = etapa
        if erro:
            cmd["erro"] = erro
        if resumo:
            cmd["resumo"] = resumo
        if atual is not None or total is not None:
            cmd["progresso"] = _progress_dict(atual, total)
        eventos = list(cmd.get("eventos", []))
        if mensagem:
            eventos.append(_event(mensagem, nivel=nivel))
        cmd["eventos"] = eventos[-40:]
        break
    data["commands"] = cmds[-100:]
    _save_commands_local(data)


def _update_status_local(
    chave: str,
    *,
    command_id: str | None,
    status_text: str,
    mensagem: str,
    etapa: str = "",
    atual=None,
    total=None,
    erro: str = "",
    resumo: dict | None = None,
    nivel: str = "info",
    ultimo_resultado: str = "",
    success_path: Path | None = None,
) -> None:
    status = _load_status_local()
    bloco = _touch_status_block(status.get(chave))
    bloco["status"] = status_text
    bloco["mensagem"] = mensagem
    bloco["atualizado_em"] = now()
    if command_id:
        bloco["ultimo_comando_id"] = command_id
    if etapa:
        bloco["etapa_atual"] = etapa
    if erro:
        bloco["erro"] = erro
    elif status_text != "erro":
        bloco["erro"] = ""
    if resumo:
        bloco["resumo"] = resumo
    if atual is not None or total is not None:
        bloco["progresso"] = _progress_dict(atual, total)
    if mensagem:
        eventos = list(bloco.get("eventos", []))
        eventos.append(_event(mensagem, nivel=nivel))
        bloco["eventos"] = eventos[-40:]
    if status_text == "ok":
        bloco["ultimo_sucesso"] = fmt_mtime(success_path) if success_path and success_path.exists() else now()
    if chave == "comandos" and ultimo_resultado:
        bloco["ultimo_resultado"] = ultimo_resultado
    status[chave] = bloco

    gh = _touch_status_block(status.get("github_actions"))
    gh["status"] = status_text
    gh["mensagem"] = mensagem
    gh["atualizado_em"] = now()
    status["github_actions"] = gh
    _save_status_local(status)


def _status_callback(chave: str, command_id: str | None, ultimo_resultado: str = "", success_path: Path | None = None):
    def callback(
        *,
        status: str = "executando",
        mensagem: str = "",
        etapa: str = "",
        atual=None,
        total=None,
        erro: str = "",
        resumo: dict | None = None,
        nivel: str = "info",
    ):
        _update_command_local(
            command_id,
            status_text=status,
            mensagem=mensagem,
            etapa=etapa,
            atual=atual,
            total=total,
            erro=erro,
            resumo=resumo,
            nivel=nivel,
        )
        _update_status_local(
            chave,
            command_id=command_id,
            status_text=status,
            mensagem=mensagem,
            etapa=etapa,
            atual=atual,
            total=total,
            erro=erro,
            resumo=resumo,
            nivel=nivel,
            ultimo_resultado=ultimo_resultado if status == "ok" else "",
            success_path=success_path if status == "ok" else None,
        )

    return callback


def _produtos_df() -> pd.DataFrame:
    path = DATA / "PRODUTOS COM EAN - POR LANCAMENTOS-PRIORITARIOS-LINHA.xlsx"
    if path.exists():
        return pd.read_excel(path)
    return pd.DataFrame()


def _load_pedido_payload() -> list[dict]:
    path = DATA / "pedido_payload.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("cart_items", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _resolve_from_command_id(command_id: str) -> tuple[str, dict]:
    data = _load_commands_local()
    cmds = data.get("commands", [])
    for cmd in reversed(cmds):
        if cmd.get("id") == command_id:
            return _canon(cmd.get("acao")), cmd.get("params", {}) or {}
    raise ValueError(f"Comando nao encontrado: {command_id}")


def execute_direct(acao: str, headless: bool = True, cnpj: str = "", cupom: str = "", command_id: str | None = None):
    acao = _canon(acao)
    chave = _status_key(acao)
    creds = load_creds()
    bussola_login = os.getenv("BUSSOLA_LOGIN") or creds.bussola_login
    bussola_senha = os.getenv("BUSSOLA_SENHA") or creds.bussola_senha
    mf_login = os.getenv("MERCADOFARMA_LOGIN") or creds.mercado_login
    mf_senha = os.getenv("MERCADOFARMA_SENHA") or creds.mercado_senha
    mf_cnpj = cnpj or os.getenv("MERCADOFARMA_CNPJ") or creds.mercado_cnpj

    success_path: Path | None = None
    final_result = {
        "atualizar_bussola": "Bussola atualizado.",
        "atualizar_mercadofarma": "Mercado Farma atualizado.",
        "limpar_pedido_mf": "Pedido MF limpo.",
        "enviar_pedido_mf": "Pedido enviado ao Mercado Farma.",
    }.get(acao, "Acao concluida.")
    callback = _status_callback(chave, command_id, ultimo_resultado=final_result, success_path=success_path)
    callback(status="executando", mensagem="Workflow iniciado.", etapa="Preparacao")

    if acao == "atualizar_bussola":
        success_path = DATA / "Pedidos.xlsx"
        callback = _status_callback(chave, command_id, ultimo_resultado=final_result, success_path=success_path)
        callback(status="executando", mensagem="Iniciando coleta do Bussola.", etapa="Preparacao", atual=1, total=5)
        run_bussola_download(bussola_login, bussola_senha, success_path, headless=headless, status_cb=callback)
        callback(status="ok", mensagem="Bussola atualizado.", etapa="Concluido", atual=5, total=5, resumo={"arquivo": success_path.name})
        return True, final_result

    if acao == "atualizar_mercadofarma":
        success_path = DATA / "Estoque_preco_distribuidora.xlsx"
        callback = _status_callback(chave, command_id, ultimo_resultado=final_result, success_path=success_path)
        callback(status="executando", mensagem="Iniciando extracao do Mercado Farma.", etapa="Preparacao", atual=1, total=4)
        run_mercadofarma_inventory(mf_login, mf_senha, mf_cnpj, _produtos_df(), success_path, headless=headless, status_cb=callback)
        callback(status="ok", mensagem="Mercado Farma atualizado.", etapa="Concluido", atual=4, total=4, resumo={"arquivo": success_path.name})
        return True, final_result

    if acao == "limpar_pedido_mf":
        callback = _status_callback(chave, command_id, ultimo_resultado=final_result, success_path=None)
        clear_mercadofarma_mass_order(mf_login, mf_senha, mf_cnpj, headless=headless, status_cb=callback)
        callback(status="ok", mensagem="Pedido MF limpo.", etapa="Concluido", atual=3, total=3, resumo={"cnpj": mf_cnpj})
        return True, final_result

    if acao == "enviar_pedido_mf":
        cart_items = _load_pedido_payload()
        if not cart_items:
            raise ValueError("Nenhum item encontrado em data/pedido_payload.json")
        callback = _status_callback(chave, command_id, ultimo_resultado=final_result, success_path=None)
        callback(status="executando", mensagem=f"Pedido carregado com {len(cart_items)} item(ns).", etapa="Preparacao", atual=1, total=len(cart_items) + 5)
        run_mercadofarma_mass_order(mf_login, mf_senha, cart_items, headless=headless, cupom=cupom, status_cb=callback)
        callback(status="ok", mensagem="Pedido enviado ao Mercado Farma.", etapa="Concluido", atual=len(cart_items) + 5, total=len(cart_items) + 5, resumo={"itens": len(cart_items), "cnpj": mf_cnpj})
        return True, final_result

    raise ValueError(f"Acao desconhecida: {acao}")


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

        ok, msg = execute_direct(acao, headless=headless, cnpj=cnpj, cupom=cupom, command_id=command_id or None)
        print(json.dumps({"ok": ok, "mensagem": msg}, ensure_ascii=False))
    except Exception as exc:
        chave = _status_key(args.acao or "comandos")
        callback = _status_callback(chave, command_id or None)
        callback(status="erro", mensagem=str(exc), etapa="Falha", erro=str(exc), nivel="error")
        raise


if __name__ == "__main__":
    main()
