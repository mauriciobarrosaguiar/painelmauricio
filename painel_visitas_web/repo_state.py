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

TZ_BR = ZoneInfo("America/Sao_Paulo")
REPO_ROOT = BASE_DIR.parent
ROOT_DATA_DIR = REPO_ROOT / "data"
ROOT_DATA_DIR.mkdir(exist_ok=True)
WORKFLOW_FILE = "automacao_web.yml"


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


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def _raw_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github.raw"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


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
    # Usa a API contents, não raw.githubusercontent.com, para evitar cache atrasado do CDN.
    try:
        r = requests.get(
            _contents_url(rel_path),
            headers=_headers(),
            params={"ref": _repo_branch()},
            timeout=15,
        )
        if not r.ok:
            return None
        payload = r.json()
        content = payload.get("content", "")
        encoding = payload.get("encoding", "")
        if encoding == "base64" and content:
            raw = base64.b64decode(content).decode("utf-8")
            data = json.loads(raw)
            _write_local_json(rel_path, data)
            return data
    except Exception:
        return None
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
    atual_local = _load_local_json(rel_path)
    if atual_local == data:
        # evita regravar e deixa o app mais leve
        pass
    _write_local_json(rel_path, data)
    token = _token()
    if not token:
        return False, "GITHUB_TOKEN não configurado. Salvo apenas localmente."
    try:
        sha = None
        atual_remoto = None
        r0 = requests.get(_contents_url(rel_path), headers=_headers(), params={"ref": _repo_branch()}, timeout=25)
        if r0.ok:
            payload0 = r0.json()
            sha = payload0.get("sha")
            content0 = payload0.get("content", "")
            if payload0.get("encoding") == "base64" and content0:
                try:
                    atual_remoto = json.loads(base64.b64decode(content0).decode("utf-8"))
                except Exception:
                    atual_remoto = None

        if atual_remoto == data:
            return True, "Sem alterações para enviar."

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
            "bussola": {"ultimo_sucesso": "", "status": "nunca", "mensagem": ""},
            "mercadofarma": {"ultimo_sucesso": "", "status": "nunca", "mensagem": ""},
            "github_actions": {"status": "nunca", "mensagem": ""},
            "comandos": {"ultimo_resultado": "", "status": "nunca"},
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
    mapa = {
        "queued": "na fila",
        "in_progress": "em execução",
        "completed": "concluído",
    }
    return mapa.get(status or "", status or "-")


def _trad_resultado(resultado: str | None) -> str:
    mapa = {
        "success": "sucesso",
        "failure": "falha",
        "cancelled": "cancelado",
        "skipped": "ignorado",
        "timed_out": "tempo esgotado",
        "action_required": "ação necessária",
        None: "-",
    }
    return mapa.get(resultado, resultado or "-")


def load_recent_workflow_runs(limit: int = 10) -> list[dict[str, Any]]:
    token = _token()
    if not token:
        return []

    try:
        r = requests.get(
            _actions_url("/actions/workflows/automacao_web.yml/runs"),
            headers=_headers(),
            params={"per_page": limit, "branch": _repo_branch()},
            timeout=30,
        )
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for run in runs:
        out.append(
            {
                "ID": run.get("id"),
                "Status": _trad_status(run.get("status")),
                "Resultado": _trad_resultado(run.get("conclusion")),
                "Título": run.get("name") or run.get("display_title") or "Automação Web Painel EMS",
                "Criado em": _format_dt(run.get("created_at")),
                "Atualizado em": _format_dt(run.get("updated_at")),
                "Link": run.get("html_url", ""),
            }
        )
    return out


def _dispatch_workflow(inputs: dict[str, Any]) -> tuple[bool, str]:
    token = _token()
    if not token:
        return False, "GITHUB_TOKEN não configurado para disparar o workflow."

    payload = {
        "ref": _repo_branch(),
        "inputs": {k: str(v) for k, v in inputs.items() if v is not None and v != ""},
    }

    try:
        r = requests.post(
            _actions_url(f"/actions/workflows/{WORKFLOW_FILE}/dispatches"),
            headers=_headers(),
            json=payload,
            timeout=30,
        )
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
    data = load_commands()
    cmds = data.get("commands", [])
    cmd_id = f"cmd_{int(datetime.now(TZ_BR).timestamp() * 1000)}"

    registro = {
        "id": cmd_id,
        "acao": acao,
        "params": params,
        "status": "pendente",
        "criado_em": now_str(),
        "atualizado_em": now_str(),
        "mensagem": "",
    }

    cmds.append(registro)
    data["commands"] = cmds[-100:]
    ok_save, msg_save = save_commands(data)

    workflow_inputs = {
        "command_id": cmd_id,
        "acao": acao,
        "headless": str(params.get("headless", True)).lower(),
        "cnpj": params.get("cnpj", ""),
        "cupom": params.get("cupom", ""),
    }
    ok_dispatch, msg_dispatch = _dispatch_workflow(workflow_inputs)

    status = load_status()
    status["github_actions"] = {
        "status": "solicitado" if ok_dispatch else "erro",
        "mensagem": msg_dispatch,
        "atualizado_em": now_str(),
    }
    save_status(status)

    ok = ok_save and ok_dispatch
    msg = f"{msg_save} {msg_dispatch}".strip()
    return cmd_id, ok, msg