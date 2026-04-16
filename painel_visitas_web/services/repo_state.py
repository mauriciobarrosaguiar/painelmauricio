from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

try:
    import streamlit as st
except Exception:
    st = None

from config import BASE_DIR, DATA_DIR

try:
    from services.order_builder import save_generated_order
except Exception:
    def save_generated_order(cart_items: list[dict] | None, cupom: str = "", headless: bool = True) -> dict:
        payload = {
            "gerado_em": datetime.now(TZ_BR).isoformat(),
            "cupom": str(cupom or "").strip(),
            "headless": bool(headless),
            "cliente": {},
            "resumo": {
                "linhas": len(cart_items or []),
                "produtos": len(cart_items or []),
                "distribuidoras": len({str(item.get("Distribuidora", "")) for item in (cart_items or []) if item.get("Distribuidora")}),
                "total_estimado": 0.0,
            },
            "por_distribuidora": [],
            "cart_items": list(cart_items or []),
        }
        return {"payload": payload, "csv_bytes": b"", "txt_bytes": b""}

TZ_BR = ZoneInfo("America/Sao_Paulo")
REPO_ROOT = BASE_DIR.parent
ROOT_DATA_DIR = REPO_ROOT / "data"
ROOT_DATA_DIR.mkdir(exist_ok=True)
WORKFLOW_FILE = "automacao_web.yml"
ACTION_STATUS_KEYS = {
    "atualizar_bussola": "bussola",
    "atualizar_mercadofarma": "mercadofarma",
    "atualizar_mercado_farma": "mercadofarma",
    "limpar_pedido_mf": "comandos",
    "enviar_pedido_mf": "comandos",
}


def _empty_progress() -> dict[str, int]:
    return {"atual": 0, "total": 0, "percentual": 0}


def _event(texto: str, nivel: str = "info") -> dict[str, str]:
    return {"quando": now_str(), "texto": str(texto or "").strip(), "nivel": nivel}


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


def _secret(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value:
        return value
    if st is not None:
        try:
            return st.secrets.get(name, default)
        except Exception:
            pass
    return default


def _repo_owner() -> str:
    return _secret("GITHUB_REPO_OWNER", _secret("GITHUB_OWNER", "mauriciobarrosaguiar"))


def _repo_name() -> str:
    return _secret("GITHUB_REPO_NAME", _secret("GITHUB_REPO", "painelmauricio"))


def _repo_branch() -> str:
    return _secret("GITHUB_REPO_BRANCH", _secret("GITHUB_DEFAULT_REF", "main"))


def _token() -> str:
    return _secret("GITHUB_TOKEN", _secret("GH_AUTOMATION_TOKEN", ""))


def _api_base() -> str:
    return f"https://api.github.com/repos/{_repo_owner()}/{_repo_name()}"


def _raw_url(rel_path: str) -> str:
    return f"https://raw.githubusercontent.com/{_repo_owner()}/{_repo_name()}/{_repo_branch()}/{rel_path}"


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def _raw_headers() -> dict[str, str]:
    token = _token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _contents_url(rel_path: str) -> str:
    return f"{_api_base()}/contents/{rel_path}"


def _actions_url(path: str) -> str:
    return f"{_api_base()}{path}"


def _candidate_local_paths(rel_path: str) -> list[Path]:
    rel = Path(rel_path)
    paths = [REPO_ROOT / rel]
    if rel.parts and rel.parts[0] == "data":
        paths.append(DATA_DIR / rel.name)
    if len(rel.parts) >= 2 and rel.parts[0] == "painel_visitas_web" and rel.parts[1] == "data":
        paths.append(DATA_DIR / rel.name)
    uniq: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        s = str(p)
        if s not in seen:
            uniq.append(p)
            seen.add(s)
    return uniq


def _write_local_json(rel_path: str, data: Any) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2)
    for p in _candidate_local_paths(rel_path):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _load_local_json(rel_path: str) -> Any | None:
    for p in _candidate_local_paths(rel_path):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def _load_remote_json(rel_path: str) -> Any | None:
    try:
        r = requests.get(_raw_url(rel_path), headers=_raw_headers(), timeout=12)
        if not r.ok or not r.text.strip():
            return None
        data = r.json()
        _write_local_json(rel_path, data)
        return data
    except Exception:
        return None


def repo_load_json(rel_path: str, default: Any, prefer_remote: bool = False):
    if prefer_remote:
        remote = _load_remote_json(rel_path)
        if remote is not None:
            return remote
    local = _load_local_json(rel_path)
    if local is not None:
        return local
    remote = _load_remote_json(rel_path)
    if remote is not None:
        return remote
    return default


def repo_save_json(rel_path: str, data: Any, message: str = "Atualizar estado do painel"):
    content = json.dumps(data, ensure_ascii=False, indent=2)
    _write_local_json(rel_path, data)
    token = _token()
    if not token:
        return False, "GITHUB_TOKEN não configurado. Salvo apenas localmente."
    try:
        sha = None
        r0 = requests.get(_contents_url(rel_path), headers=_headers(), timeout=25)
        if r0.ok:
            sha = r0.json().get("sha")
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": _repo_branch(),
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(_contents_url(rel_path), headers=_headers(), json=payload, timeout=30)
        if r.ok:
            return True, "GitHub atualizado."
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:200]
        return False, f"Falha ao atualizar GitHub: {r.status_code} {detail}"
    except Exception as e:
        return False, f"Erro ao atualizar GitHub: {e}"


def now_str() -> str:
    return datetime.now(TZ_BR).strftime("%d/%m/%Y %H:%M:%S")


def _update_status_for_command(acao: str, command_id: str, mensagem: str, status_text: str = "solicitado"):
    chave = ACTION_STATUS_KEYS.get(acao)
    if not chave:
        return
    status = load_status()
    bloco = _touch_status_block(status.get(chave))
    bloco["status"] = status_text
    bloco["mensagem"] = mensagem
    bloco["atualizado_em"] = now_str()
    bloco["ultimo_comando_id"] = command_id
    bloco["etapa_atual"] = "Aguardando execucao"
    bloco["erro"] = ""
    bloco["progresso"] = _empty_progress()
    bloco["eventos"] = (bloco.get("eventos", []) + [_event(mensagem)])[-40:]
    status[chave] = bloco
    gh = _touch_status_block(status.get("github_actions"))
    gh["status"] = status_text
    gh["mensagem"] = mensagem
    gh["atualizado_em"] = now_str()
    status["github_actions"] = gh
    save_status(status)


def load_user_config() -> dict:
    return repo_load_json(
        "data/config_usuario.json",
        {
            "foco_semana_manual": [],
            "foco_mes_manual": [],
            "visible_dists": [],
            "addl_discount": {},
            "addl_discount_exclusions": {},
            "dist_pref": {},
        },
        prefer_remote=False,
    )


def save_user_config(cfg: dict):
    return repo_save_json("data/config_usuario.json", cfg, "Atualizar configurações do painel")


def load_status() -> dict:
    return repo_load_json(
        "data/status_atualizacao.json",
        {
            "bussola": _touch_status_block(),
            "mercadofarma": _touch_status_block(),
            "github_actions": _touch_status_block(),
            "comandos": {**_touch_status_block(), "ultimo_resultado": ""},
        },
        prefer_remote=True,
    )


def save_status(status: dict):
    return repo_save_json("data/status_atualizacao.json", status, "Atualizar status da automação")


def load_commands() -> dict:
    return repo_load_json("data/comandos_remotos.json", {"commands": []}, prefer_remote=True)


def save_commands(cmds: dict):
    return repo_save_json("data/comandos_remotos.json", cmds, "Atualizar fila de comandos do painel")


def _format_dt(iso_value: str | None) -> str:
    if not iso_value:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00")).astimezone(TZ_BR)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return iso_value


def _trad_status(status: str | None) -> str:
    return {"queued": "na fila", "in_progress": "em execução", "completed": "concluído"}.get(status or "", status or "-")


def _trad_resultado(resultado: str | None) -> str:
    return {"success": "sucesso", "failure": "falha", "cancelled": "cancelado", "skipped": "ignorado", "timed_out": "tempo esgotado", "action_required": "ação necessária", None: "-"}.get(resultado, resultado or "-")


def load_recent_workflow_runs(limit: int = 10) -> list[dict[str, Any]]:
    token = _token()
    if not token:
        return []
    try:
        r = requests.get(_actions_url(f"/actions/workflows/{WORKFLOW_FILE}/runs"), headers=_headers(), params={"per_page": limit, "branch": _repo_branch()}, timeout=20)
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
    except Exception:
        return []
    out = []
    for run in runs:
        out.append({
            "ID": run.get("id"),
            "Status": _trad_status(run.get("status")),
            "Resultado": _trad_resultado(run.get("conclusion")),
            "Título": run.get("name") or run.get("display_title") or "Automação Web Painel EMS",
            "Criado em": _format_dt(run.get("created_at")),
            "Atualizado em": _format_dt(run.get("updated_at")),
            "Link": run.get("html_url", ""),
        })
    return out


def _dispatch_workflow(inputs: dict[str, Any]) -> tuple[bool, str]:
    token = _token()
    if not token:
        return False, "GITHUB_TOKEN não configurado para disparar o workflow."
    payload = {"ref": _repo_branch(), "inputs": {k: str(v) for k, v in inputs.items() if v is not None and v != ""}}
    try:
        r = requests.post(_actions_url(f"/actions/workflows/{WORKFLOW_FILE}/dispatches"), headers=_headers(), json=payload, timeout=30)
        if r.status_code in (204, 201):
            return True, "Workflow disparado com sucesso."
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:200]
        return False, f"Falha ao iniciar workflow: {r.status_code} {detail}"
    except Exception as e:
        return False, f"Erro ao iniciar workflow: {e}"


def enqueue_command(acao: str, params: dict | None = None):
    params = params or {}
    if acao == "enviar_pedido_mf":
        generated = save_generated_order(
            params.get("cart_items", []),
            cupom=str(params.get("cupom", "") or ""),
            headless=bool(params.get("headless", True)),
        )
        params = dict(params)
        params["cart_items"] = generated["payload"].get("cart_items", [])
        repo_save_json("data/pedido_payload.json", generated["payload"], "Atualizar pedido gerado do painel")

    data = load_commands()
    cmds = data.get("commands", [])
    cmd_id = f"cmd_{int(datetime.now(TZ_BR).timestamp() * 1000)}"
    registro = {
        "id": cmd_id,
        "acao": acao,
        "params": params,
        "status": "solicitado",
        "criado_em": now_str(),
        "atualizado_em": now_str(),
        "mensagem": "Solicitado pelo painel web.",
        "origem": "streamlit",
    }
    cmds.append(registro)
    data["commands"] = cmds[-100:]
    ok_save, msg_save = save_commands(data)
    workflow_inputs = {"command_id": cmd_id, "acao": acao, "headless": str(params.get("headless", True)).lower(), "cnpj": params.get("cnpj", ""), "cupom": params.get("cupom", "")}
    ok_dispatch, msg_dispatch = _dispatch_workflow(workflow_inputs)
    _update_status_for_command(
        acao,
        cmd_id,
        "Comando enviado para a fila do painel." if ok_dispatch else msg_dispatch,
        status_text="solicitado" if ok_dispatch else "erro",
    )
    status = load_status()
    github = _touch_status_block(status.get("github_actions"))
    github["status"] = "solicitado" if ok_dispatch else "erro"
    github["mensagem"] = msg_dispatch
    github["atualizado_em"] = now_str()
    status["github_actions"] = github
    save_status(status)
    ok = ok_save and ok_dispatch
    msg = f"{msg_save} {msg_dispatch}".strip()
    return cmd_id, ok, msg
