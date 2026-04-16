from __future__ import annotations

import base64
import io
import os
from pathlib import Path

import pandas as pd
import requests

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

from config import PEDIDOS_FILE, PRODUTOS_FILE, CLIENTES_FILE, FOCO_SEMANA_FILE, INVENTARIO_FILE


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


def _headers_json() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = _secret("GITHUB_TOKEN", _secret("GH_AUTOMATION_TOKEN", ""))
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def _contents_url(rel_path: str) -> str:
    return f"https://api.github.com/repos/{_repo_owner()}/{_repo_name()}/contents/{rel_path}"


if st is not None:
    @st.cache_data(ttl=20, show_spinner=False)
    def _github_bytes(rel_path: str, branch: str) -> bytes | None:
        # Usa a API contents para evitar cache atrasado do raw.githubusercontent.
        try:
            resp = requests.get(
                _contents_url(rel_path),
                headers=_headers_json(),
                params={"ref": branch},
                timeout=20,
            )
            if not resp.ok:
                return None
            payload = resp.json()
            content = payload.get("content", "")
            encoding = payload.get("encoding", "")
            if encoding == "base64" and content:
                return base64.b64decode(content)
        except Exception:
            return None
        return None
else:
    def _github_bytes(rel_path: str, branch: str) -> bytes | None:
        try:
            resp = requests.get(
                _contents_url(rel_path),
                headers=_headers_json(),
                params={"ref": branch},
                timeout=20,
            )
            if not resp.ok:
                return None
            payload = resp.json()
            content = payload.get("content", "")
            encoding = payload.get("encoding", "")
            if encoding == "base64" and content:
                return base64.b64decode(content)
        except Exception:
            return None
        return None


def _read_excel_local(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_excel(path, sheet_name=0)


def _read_excel_repo_first(path: Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    rel_path = f"painel_visitas_web/data/{path.name}"
    raw = _github_bytes(rel_path, _repo_branch())
    if raw:
        try:
            return pd.read_excel(io.BytesIO(raw), sheet_name=0)
        except Exception:
            pass
    return _read_excel_local(path)


def load_pedidos() -> pd.DataFrame:
    return _read_excel_repo_first(PEDIDOS_FILE)


def load_produtos() -> pd.DataFrame:
    return _read_excel_local(PRODUTOS_FILE)


def load_clientes() -> pd.DataFrame:
    return _read_excel_local(CLIENTES_FILE)


def load_foco_semana() -> pd.DataFrame:
    return _read_excel_local(FOCO_SEMANA_FILE)


def load_inventario() -> pd.DataFrame:
    return _read_excel_repo_first(INVENTARIO_FILE)