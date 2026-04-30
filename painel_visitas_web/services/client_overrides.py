from __future__ import annotations

import pandas as pd

from services.repo_state import repo_load_json, repo_save_json

CLIENT_OVERRIDES_REL_PATH = "data/clientes_editados.json"
CLIENT_EDIT_FIELDS = [
    "nome_fantasia",
    "razao_social",
    "nome_contato",
    "contato",
    "cidade",
    "uf",
    "endereco",
    "bairro",
]


def _digits(value) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(14) if digits else ""


def _phone(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def load_client_overrides() -> dict:
    data = repo_load_json(CLIENT_OVERRIDES_REL_PATH, {"clientes": {}}, prefer_remote=True)
    return data if isinstance(data, dict) else {"clientes": {}}


def save_client_overrides(data: dict):
    payload = data if isinstance(data, dict) else {"clientes": {}}
    payload.setdefault("clientes", {})
    return repo_save_json(CLIENT_OVERRIDES_REL_PATH, payload, "Atualizar edicoes de clientes")


def clear_client_overrides():
    return save_client_overrides({"clientes": {}})


def upsert_client_override(cnpj: str, values: dict):
    data = load_client_overrides()
    clientes = data.setdefault("clientes", {})
    key = _digits(cnpj)
    if not key:
        return False, "CNPJ invalido."
    clean_values = {field: str(values.get(field, "") or "").strip() for field in CLIENT_EDIT_FIELDS}
    if clean_values.get("contato"):
        clean_values["telefone_limpo"] = _phone(clean_values["contato"])
    clientes[key] = clean_values
    return save_client_overrides(data)


def remove_client_override(cnpj: str):
    data = load_client_overrides()
    clientes = data.setdefault("clientes", {})
    key = _digits(cnpj)
    if key in clientes:
        clientes.pop(key, None)
    return save_client_overrides(data)


def apply_client_overrides(clientes_df: pd.DataFrame) -> pd.DataFrame:
    if clientes_df is None or clientes_df.empty:
        return clientes_df
    data = load_client_overrides()
    overrides = data.get("clientes", {}) if isinstance(data, dict) else {}
    if not overrides:
        return clientes_df

    df = clientes_df.copy()
    if "cnpj" not in df.columns:
        return df
    df["cnpj_norm_override"] = df["cnpj"].map(_digits)
    for field in CLIENT_EDIT_FIELDS + ["telefone_limpo"]:
        if field not in df.columns:
            df[field] = ""

    for cnpj, values in overrides.items():
        key = _digits(cnpj)
        if not key:
            continue
        mask = df["cnpj_norm_override"] == key
        if not mask.any():
            continue
        for field, value in dict(values or {}).items():
            if field in df.columns:
                df.loc[mask, field] = str(value or "")
        if "contato" in values and "telefone_limpo" in df.columns:
            df.loc[mask, "telefone_limpo"] = _phone(values.get("contato", ""))
    return df.drop(columns=["cnpj_norm_override"], errors="ignore")
