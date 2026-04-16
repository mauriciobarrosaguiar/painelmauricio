from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from config import BASE_DIR, DATA_DIR

TZ_BR = ZoneInfo("America/Sao_Paulo")
ROOT_DATA_DIR = BASE_DIR.parent / "data"
ROOT_DATA_DIR.mkdir(exist_ok=True)

ORDER_COLUMNS = [
    "Cliente",
    "CNPJ",
    "Empresa",
    "Razao social",
    "Nome do comprador",
    "Tel do comprador",
    "EAN",
    "Produto",
    "Distribuidora",
    "Preco",
    "Estoque",
    "Mix",
    "Qtde",
    "Foco",
]


def _candidate_paths(filename: str) -> list[Path]:
    paths = [DATA_DIR / filename, ROOT_DATA_DIR / filename]
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def _write_text(filename: str, content: str) -> None:
    for path in _candidate_paths(filename):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_bytes(filename: str, content: bytes) -> None:
    for path in _candidate_paths(filename):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _write_json(filename: str, data: dict) -> None:
    _write_text(filename, json.dumps(data, ensure_ascii=False, indent=2))


def _plain_digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _safe_float(value, default: float = 0.0) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(number) else number)


def _safe_int(value, default: int = 0) -> int:
    number = pd.to_numeric(value, errors="coerce")
    return int(default if pd.isna(number) else number)


def _first_value(item: dict, *keys: str, default=""):
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
    return default


def _money(value) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def normalize_cart_items(cart_items: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for item in cart_items or []:
        ean = _plain_digits(_first_value(item, "EAN", "ean"))
        cnpj = _plain_digits(_first_value(item, "CNPJ", "cnpj"))
        qtde = max(0, _safe_int(_first_value(item, "Qtde", "qtd"), 0))
        if not ean or qtde <= 0:
            continue
        normalized.append(
            {
                "Cliente": str(_first_value(item, "Cliente")),
                "CNPJ": cnpj,
                "Empresa": str(_first_value(item, "Empresa")),
                "Razao social": str(_first_value(item, "Razao social", "Razao Social")),
                "Nome do comprador": str(_first_value(item, "Nome do comprador")),
                "Tel do comprador": _plain_digits(_first_value(item, "Tel do comprador")),
                "EAN": ean,
                "Produto": str(_first_value(item, "Produto")),
                "Distribuidora": str(_first_value(item, "Distribuidora", default="Sem distribuidora") or "Sem distribuidora"),
                "Preco": round(_safe_float(_first_value(item, "Preco", "Preço", "PreÃ§o", "PreÃƒÂ§o"), 0.0), 2),
                "Estoque": max(0, _safe_int(_first_value(item, "Estoque"), 0)),
                "Mix": str(_first_value(item, "Mix", default="LINHA") or "LINHA"),
                "Qtde": qtde,
                "Foco": bool(_first_value(item, "Foco", default=False)),
            }
        )
    return normalized


def build_order_dataframe(cart_items: list[dict] | None) -> pd.DataFrame:
    normalized = normalize_cart_items(cart_items)
    if not normalized:
        return pd.DataFrame(columns=ORDER_COLUMNS + ["Total"])

    df = pd.DataFrame(normalized)
    for column in ORDER_COLUMNS:
        if column not in df.columns:
            df[column] = "" if column not in {"Preco", "Estoque", "Qtde", "Foco"} else 0
    df["Preco"] = pd.to_numeric(df["Preco"], errors="coerce").fillna(0.0)
    df["Estoque"] = pd.to_numeric(df["Estoque"], errors="coerce").fillna(0).astype(int)
    df["Qtde"] = pd.to_numeric(df["Qtde"], errors="coerce").fillna(0).astype(int)
    df["Total"] = (df["Preco"] * df["Qtde"]).round(2)
    return df[ORDER_COLUMNS + ["Total"]]


def build_order_payload(cart_items: list[dict] | None, cupom: str = "", headless: bool = True) -> dict:
    df = build_order_dataframe(cart_items)
    header = df.iloc[0].to_dict() if not df.empty else {}
    grouped = []

    if not df.empty:
        resumo_dist = (
            df.groupby("Distribuidora", as_index=False)
            .agg(produtos=("EAN", "nunique"), unidades=("Qtde", "sum"), total=("Total", "sum"))
            .sort_values(["total", "Distribuidora"], ascending=[False, True])
        )
        for _, row in resumo_dist.iterrows():
            grouped.append(
                {
                    "Distribuidora": row["Distribuidora"],
                    "Produtos": int(row["produtos"]),
                    "Unidades": int(row["unidades"]),
                    "Total": round(float(row["total"]), 2),
                }
            )

    return {
        "gerado_em": datetime.now(TZ_BR).isoformat(),
        "cupom": str(cupom or "").strip(),
        "headless": bool(headless),
        "cliente": {
            "Cliente": header.get("Cliente", ""),
            "CNPJ": header.get("CNPJ", ""),
            "Empresa": header.get("Empresa", ""),
            "Razao social": header.get("Razao social", ""),
            "Nome do comprador": header.get("Nome do comprador", ""),
            "Tel do comprador": header.get("Tel do comprador", ""),
        },
        "resumo": {
            "linhas": int(len(df)),
            "produtos": int(df["EAN"].nunique()) if not df.empty else 0,
            "distribuidoras": int(df["Distribuidora"].nunique()) if not df.empty else 0,
            "total_estimado": round(float(df["Total"].sum()), 2) if not df.empty else 0.0,
        },
        "por_distribuidora": grouped,
        "cart_items": df[ORDER_COLUMNS].to_dict(orient="records"),
    }


def build_order_exports(payload: dict) -> dict[str, bytes]:
    df = build_order_dataframe(payload.get("cart_items", []))
    export_df = df.copy()
    if export_df.empty:
        export_df = pd.DataFrame(columns=ORDER_COLUMNS + ["Total"])

    export_df["Preco"] = export_df["Preco"].map(_money)
    export_df["Total"] = export_df["Total"].map(_money)
    csv_bytes = export_df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")

    cliente = payload.get("cliente", {})
    resumo = payload.get("resumo", {})
    linhas = [
        f"GERADO EM: {payload.get('gerado_em', '')}",
        f"CLIENTE: {cliente.get('Cliente', '')}",
        f"CNPJ: {cliente.get('CNPJ', '')}",
        f"EMPRESA: {cliente.get('Empresa', '')}",
        f"RAZAO SOCIAL: {cliente.get('Razao social', '')}",
        f"COMPRADOR: {cliente.get('Nome do comprador', '')}",
        f"TELEFONE: {cliente.get('Tel do comprador', '')}",
        f"CUPOM: {payload.get('cupom', '') or '-'}",
        f"TOTAL ESTIMADO: {_money(resumo.get('total_estimado', 0.0))}",
        "",
        "ITENS",
    ]

    for _, row in df.iterrows():
        linhas.append(
            f"{row['CNPJ']}; {row['EAN']}; {row['Produto']}; {row['Distribuidora']}; {int(row['Qtde'])}; {_money(row['Preco'])}; {_money(row['Total'])}"
        )

    txt_bytes = "\n".join(linhas).encode("utf-8-sig")
    return {"csv_bytes": csv_bytes, "txt_bytes": txt_bytes}


def save_generated_order(cart_items: list[dict] | None, cupom: str = "", headless: bool = True) -> dict:
    payload = build_order_payload(cart_items, cupom=cupom, headless=headless)
    exports = build_order_exports(payload)
    _write_json("pedido_payload.json", payload)
    _write_json("pedido_gerado.json", payload)
    _write_bytes("pedido_gerado.csv", exports["csv_bytes"])
    _write_bytes("pedido_gerado.txt", exports["txt_bytes"])
    return {
        "payload": payload,
        "csv_bytes": exports["csv_bytes"],
        "txt_bytes": exports["txt_bytes"],
    }
