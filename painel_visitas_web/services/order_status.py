from __future__ import annotations

from io import BytesIO
import re

import pandas as pd

STATUS_FATURADO = "Faturado / nota gerada"
STATUS_SEM_NOTA = "Ainda nao gerou nota"
STATUS_CANCELADO = "Cancelado"
STATUS_TODOS = "Todos"
STATUS_OPTIONS = [STATUS_TODOS, STATUS_FATURADO, STATUS_SEM_NOTA, STATUS_CANCELADO]


def digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def money(value) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _norm_status(value: str) -> str:
    text = str(value or "").upper().strip()
    text = text.replace("Ã‡", "C").replace("Ç", "C")
    text = re.sub(r"\s+", " ", text)
    return text


def _has_invoice(values: pd.Series) -> bool:
    cleaned = values.fillna("").astype(str).str.strip()
    cleaned = cleaned[~cleaned.str.upper().isin(["", "NAN", "NONE", "<NA>", "0"])]
    return not cleaned.empty


def _first_text(values: pd.Series, default: str = "") -> str:
    cleaned = values.dropna().astype(str).str.strip()
    cleaned = cleaned[~cleaned.str.upper().isin(["", "NAN", "NONE", "<NA>"])]
    return str(cleaned.iloc[0]) if not cleaned.empty else default


def _join_unique(values: pd.Series, limit: int = 4) -> str:
    seen: list[str] = []
    for value in values.dropna().astype(str):
        text = value.strip()
        if not text or text.upper() in {"NAN", "NONE", "<NA>"} or text in seen:
            continue
        seen.append(text)
    extra = " ..." if len(seen) > limit else ""
    return ", ".join(seen[:limit]) + extra


def _first_existing(df: pd.DataFrame, names: list[str]) -> str | None:
    lower = {str(col).lower(): col for col in df.columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def build_order_detail(base_full: pd.DataFrame, cnpjs: list[str] | set[str] | tuple[str, ...] | None = None) -> pd.DataFrame:
    if base_full is None or base_full.empty:
        return pd.DataFrame(columns=detail_columns())

    base = base_full.copy()
    cnpj_col = _first_existing(base, ["cnpj_pdv", "cnpj"])
    if cnpj_col is None:
        return pd.DataFrame(columns=detail_columns())

    base["cnpj_norm"] = base[cnpj_col].astype(str).map(digits)
    if cnpjs:
        allowed = {digits(item) for item in cnpjs if digits(item)}
        base = base[base["cnpj_norm"].isin(allowed)].copy()
    if base.empty:
        return pd.DataFrame(columns=detail_columns())

    pedido_col = _first_existing(base, ["pedido_id", "id_pedido", "pedido", "numero_pedido"])
    if pedido_col is None:
        base["_pedido_id_tmp"] = base.index.astype(str)
        pedido_col = "_pedido_id_tmp"

    status_col = _first_existing(base, ["status_pedido", "status"])
    nota_col = _first_existing(base, ["nota_fiscal", "nf", "numero_nota"])
    cliente_col = _first_existing(base, ["nome_fantasia", "cliente", "razao_social"])
    cidade_col = _first_existing(base, ["cidade"])
    produto_col = _first_existing(base, ["produto", "principio_ativo"])
    ean_col = _first_existing(base, ["ean"])
    data_col = _first_existing(base, ["data_do_pedido", "data_pedido"])
    faturamento_col = _first_existing(base, ["data_de_faturamento", "data_faturamento"])
    solicitado_col = _first_existing(base, ["valor_total_solicitado_sem_imposto", "valor_total_solicitado_com_imposto", "total_solicitado"])
    faturado_col = _first_existing(base, ["valor_faturado", "total_atendido_sem_imposto", "total_atendido_com_imposto"])
    cancelado_col = _first_existing(base, ["quantidade_cancelada"])

    if status_col is None:
        base["_status_tmp"] = ""
        status_col = "_status_tmp"
    if nota_col is None:
        base["_nota_tmp"] = ""
        nota_col = "_nota_tmp"
    if solicitado_col is None:
        base["_solicitado_tmp"] = 0.0
        solicitado_col = "_solicitado_tmp"
    if faturado_col is None:
        base["_faturado_tmp"] = 0.0
        faturado_col = "_faturado_tmp"

    base[solicitado_col] = pd.to_numeric(base[solicitado_col], errors="coerce").fillna(0.0)
    base[faturado_col] = pd.to_numeric(base[faturado_col], errors="coerce").fillna(0.0)
    if cancelado_col:
        base[cancelado_col] = pd.to_numeric(base[cancelado_col], errors="coerce").fillna(0.0)

    rows: list[dict] = []
    for pedido, grupo in base.groupby(pedido_col, dropna=False):
        statuses = grupo[status_col].fillna("").astype(str).map(_norm_status)
        nota = _join_unique(grupo[nota_col])
        has_invoice = _has_invoice(grupo[nota_col])
        has_faturado = statuses.str.contains("FATURADO", na=False).any() or has_invoice
        has_cancelado = statuses.str.contains("CANCELADO", na=False).any()
        if has_faturado:
            categoria = STATUS_FATURADO
        elif has_cancelado:
            categoria = STATUS_CANCELADO
        else:
            categoria = STATUS_SEM_NOTA

        valor_solicitado = float(grupo[solicitado_col].sum())
        valor_faturado = float(grupo[faturado_col].sum())
        falta_faturar = 0.0 if categoria == STATUS_CANCELADO else max(0.0, valor_solicitado - valor_faturado)
        data_pedido = pd.to_datetime(grupo[data_col], errors="coerce").min() if data_col else pd.NaT
        data_fat = pd.to_datetime(grupo[faturamento_col], errors="coerce").max() if faturamento_col else pd.NaT
        rows.append(
            {
                "Categoria": categoria,
                "Pedido": str(pedido),
                "Nota fiscal": nota,
                "Status": _join_unique(grupo[status_col]),
                "CNPJ": _first_text(grupo["cnpj_norm"]),
                "Cliente": _first_text(grupo[cliente_col]) if cliente_col else "",
                "Cidade": _first_text(grupo[cidade_col]) if cidade_col else "",
                "Data pedido": data_pedido,
                "Data faturamento": data_fat,
                "Valor solicitado": valor_solicitado,
                "Valor faturado": valor_faturado,
                "Falta faturar": falta_faturar,
                "Valor cancelado": valor_solicitado if categoria == STATUS_CANCELADO else 0.0,
                "Linhas": int(len(grupo)),
                "Produtos": int(grupo[ean_col].astype(str).nunique()) if ean_col else int(grupo[produto_col].astype(str).nunique()) if produto_col else int(len(grupo)),
                "Produtos no pedido": _join_unique(grupo[produto_col], limit=5) if produto_col else "",
            }
        )

    return pd.DataFrame(rows, columns=detail_columns()).sort_values(["Data pedido", "Pedido"], ascending=[False, False]).reset_index(drop=True)


def detail_columns() -> list[str]:
    return [
        "Categoria",
        "Pedido",
        "Nota fiscal",
        "Status",
        "CNPJ",
        "Cliente",
        "Cidade",
        "Data pedido",
        "Data faturamento",
        "Valor solicitado",
        "Valor faturado",
        "Falta faturar",
        "Valor cancelado",
        "Linhas",
        "Produtos",
        "Produtos no pedido",
    ]


def filter_order_detail(detail: pd.DataFrame, data_inicio, data_fim, status: str = STATUS_TODOS) -> pd.DataFrame:
    out = detail.copy() if isinstance(detail, pd.DataFrame) else pd.DataFrame(columns=detail_columns())
    if out.empty:
        return out
    out["Data pedido"] = pd.to_datetime(out["Data pedido"], errors="coerce")
    if data_inicio is not None:
        out = out[out["Data pedido"] >= pd.to_datetime(data_inicio)]
    if data_fim is not None:
        out = out[out["Data pedido"] <= pd.to_datetime(data_fim) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)]
    if status and status != STATUS_TODOS:
        out = out[out["Categoria"].eq(status)]
    return out.reset_index(drop=True)


def summarize_order_detail(detail: pd.DataFrame) -> dict:
    df = detail.copy() if isinstance(detail, pd.DataFrame) else pd.DataFrame(columns=detail_columns())
    if df.empty:
        return {
            "faturado_qtd": 0,
            "faturado_valor": 0.0,
            "sem_nota_qtd": 0,
            "sem_nota_valor": 0.0,
            "cancelado_qtd": 0,
            "cancelado_valor": 0.0,
        }
    return {
        "faturado_qtd": int(df[df["Categoria"].eq(STATUS_FATURADO)]["Pedido"].nunique()),
        "faturado_valor": float(df.loc[df["Categoria"].eq(STATUS_FATURADO), "Valor faturado"].sum()),
        "sem_nota_qtd": int(df[df["Categoria"].eq(STATUS_SEM_NOTA)]["Pedido"].nunique()),
        "sem_nota_valor": float(df.loc[df["Categoria"].eq(STATUS_SEM_NOTA), "Falta faturar"].sum()),
        "cancelado_qtd": int(df[df["Categoria"].eq(STATUS_CANCELADO)]["Pedido"].nunique()),
        "cancelado_valor": float(df.loc[df["Categoria"].eq(STATUS_CANCELADO), "Valor cancelado"].sum()),
    }


def display_order_detail(detail: pd.DataFrame) -> pd.DataFrame:
    out = detail.copy() if isinstance(detail, pd.DataFrame) else pd.DataFrame(columns=detail_columns())
    if out.empty:
        return out
    for col in ["Data pedido", "Data faturamento"]:
        out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%d/%m/%Y").fillna("")
    for col in ["Valor solicitado", "Valor faturado", "Falta faturar", "Valor cancelado"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0).map(money)
    return out


def excel_bytes(df: pd.DataFrame, sheet_name: str = "Pedidos") -> bytes:
    output = BytesIO()
    export = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame(columns=detail_columns())
    with pd.ExcelWriter(output, engine="xlsxwriter", datetime_format="dd/mm/yyyy") as writer:
        export.to_excel(writer, index=False, sheet_name=sheet_name[:31])
        workbook = writer.book
        worksheet = writer.sheets[sheet_name[:31]]
        number_format = workbook.add_format({"num_format": "#,##0.00"})
        date_format = workbook.add_format({"num_format": "dd/mm/yyyy"})
        for idx, column in enumerate(export.columns):
            largura = max(len(str(column)), min(48, int(export[column].astype(str).str.len().fillna(0).max()) + 2 if not export.empty else 14))
            fmt = date_format if "Data" in str(column) else number_format if pd.api.types.is_numeric_dtype(export[column]) else None
            worksheet.set_column(idx, idx, largura, fmt)
    output.seek(0)
    return output.getvalue()
