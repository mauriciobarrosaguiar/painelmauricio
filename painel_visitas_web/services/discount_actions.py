from __future__ import annotations

from datetime import date, datetime
from io import StringIO
import re
import unicodedata

import pandas as pd

ACTION_FIELDS = ("ean", "produto", "desconto", "distribuidora", "cupom", "validade")


def _strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _norm_col(value: str) -> str:
    text = _strip_accents(value).lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _br_number(value) -> float:
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace("%", "").replace("R$", "").replace(" ", "")
        if not text:
            return 0.0
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        number = float(pd.to_numeric(text, errors="coerce") or 0)
    return number * 100 if 0 < number <= 1 else number


def _parse_date(value, default_validade=None) -> str:
    raw = value
    if (raw is None or str(raw).strip() == "" or str(raw).lower() == "nan") and default_validade is not None:
        raw = default_validade
    text = str(raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except Exception:
            pass
    dt = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        return ""
    return dt.date().isoformat()


def _read_pasted_table(text: str) -> pd.DataFrame:
    raw = str(text or "").strip()
    if not raw:
        return pd.DataFrame()
    for sep in ("\t", ";", ","):
        try:
            df = pd.read_csv(StringIO(raw), sep=sep, dtype=str)
            if len(df.columns) > 1:
                return df
        except Exception:
            continue
    return pd.DataFrame()


def _column_lookup(df: pd.DataFrame) -> dict[str, str]:
    aliases = {
        "ean": "ean",
        "codigo_ean": "ean",
        "produto": "produto",
        "nome_produto": "produto",
        "nome_do_produto": "produto",
        "principio_ativo": "produto",
        "desconto": "desconto",
        "desc": "desconto",
        "desconto_acao": "desconto",
        "distribuidora": "distribuidora",
        "cupom": "cupom",
        "validade": "validade",
        "validade_da_acao": "validade",
        "validade_acao": "validade",
        "data_validade": "validade",
    }
    out: dict[str, str] = {}
    for col in df.columns:
        canonical = aliases.get(_norm_col(col))
        if canonical and canonical not in out:
            out[canonical] = col
    return out


def parse_discount_actions(
    pasted_text: str,
    default_distribuidora: str = "",
    default_validade=None,
) -> tuple[list[dict], list[str]]:
    df = _read_pasted_table(pasted_text)
    if df.empty:
        return [], ["Cole uma tabela com as colunas EAN, PRODUTO, DESCONTO, DISTRIBUIDORA, CUPOM e VALIDADE DA ACAO."]

    lookup = _column_lookup(df)
    missing = [col for col in ("ean", "desconto") if col not in lookup]
    if missing:
        return [], [f"Coluna obrigatoria ausente: {', '.join(missing).upper()}."]

    records: list[dict] = []
    errors: list[str] = []
    for idx, row in df.iterrows():
        line = idx + 2
        ean = _digits(row.get(lookup["ean"], ""))
        desconto = _br_number(row.get(lookup["desconto"], ""))
        distribuidora = str(row.get(lookup.get("distribuidora", ""), "") or default_distribuidora or "").strip()
        validade = _parse_date(row.get(lookup.get("validade", ""), ""), default_validade)
        produto = str(row.get(lookup.get("produto", ""), "") or "").strip()
        cupom = str(row.get(lookup.get("cupom", ""), "") or "").strip()

        if not ean:
            errors.append(f"Linha {line}: EAN vazio.")
            continue
        if not distribuidora:
            errors.append(f"Linha {line}: distribuidora vazia.")
            continue
        if desconto <= 0 or desconto >= 100:
            errors.append(f"Linha {line}: desconto invalido.")
            continue
        if not validade:
            errors.append(f"Linha {line}: validade invalida.")
            continue

        records.append(
            {
                "ean": ean,
                "produto": produto,
                "desconto": round(float(desconto), 4),
                "distribuidora": distribuidora,
                "cupom": cupom,
                "validade": validade,
            }
        )
    return records, errors


def actions_to_dataframe(records) -> pd.DataFrame:
    rows = []
    today = date.today()
    for rec in list(records or []):
        validade = _parse_date(rec.get("validade", ""))
        validade_dt = pd.to_datetime(validade, errors="coerce")
        status = "Vigente" if not pd.isna(validade_dt) and validade_dt.date() >= today else "Vencida"
        rows.append(
            {
                "ean": _digits(rec.get("ean", "")),
                "produto": str(rec.get("produto", "") or ""),
                "desconto": _br_number(rec.get("desconto", 0)),
                "distribuidora": str(rec.get("distribuidora", "") or "").strip(),
                "cupom": str(rec.get("cupom", "") or "").strip(),
                "validade": validade,
                "status": status,
            }
        )
    df = pd.DataFrame(rows, columns=[*ACTION_FIELDS, "status"])
    if df.empty:
        return df
    return df[df["ean"].ne("") & df["distribuidora"].ne("")].reset_index(drop=True)


def actions_to_key(records) -> tuple[tuple[str, str, float, str, str, str], ...]:
    df = actions_to_dataframe(records)
    if df.empty:
        return tuple()
    return tuple(
        (
            str(row.ean),
            str(row.produto),
            float(row.desconto),
            str(row.distribuidora),
            str(row.cupom),
            str(row.validade),
        )
        for row in df.itertuples(index=False)
    )


def apply_discount_actions(inventario: pd.DataFrame, action_key=()) -> pd.DataFrame:
    if inventario is None or inventario.empty:
        return inventario
    out = inventario.copy()
    out["acao_desconto"] = False

    actions = actions_to_dataframe(
        [
            {
                "ean": row[0],
                "produto": row[1],
                "desconto": row[2],
                "distribuidora": row[3],
                "cupom": row[4],
                "validade": row[5],
            }
            for row in (action_key or [])
        ]
    )
    if actions.empty:
        return out

    today = pd.Timestamp(date.today())
    actions["validade_dt"] = pd.to_datetime(actions["validade"], errors="coerce")
    actions = actions[(actions["validade_dt"].notna()) & (actions["validade_dt"] >= today)].copy()
    if actions.empty:
        return out

    actions["_ordem"] = range(len(actions))
    actions = actions.sort_values(["ean", "distribuidora", "validade_dt", "_ordem"]).drop_duplicates(["ean", "distribuidora"], keep="last")
    actions = actions.rename(
        columns={
            "desconto": "desconto_acao",
            "cupom": "cupom_acao",
            "validade": "validade_acao",
            "produto": "produto_acao",
        }
    )

    out["ean"] = out["ean"].astype(str)
    out["distribuidora"] = out["distribuidora"].astype(str)
    out = out.merge(
        actions[["ean", "distribuidora", "desconto_acao", "cupom_acao", "validade_acao", "produto_acao"]],
        on=["ean", "distribuidora"],
        how="left",
        suffixes=("", "_nova"),
    )

    mask = pd.to_numeric(out["desconto_acao"], errors="coerce").fillna(0).gt(0)
    if not mask.any():
        out = out.drop(columns=["produto_acao"], errors="ignore")
        return out

    desconto_normal = pd.to_numeric(out.get("desconto", 0), errors="coerce").fillna(0).clip(0, 99.99)
    preco_sem = pd.to_numeric(out.get("preco_sem_imposto", 0), errors="coerce").fillna(0)
    pf_fabrica = pd.to_numeric(out.get("pf_fabrica", 0), errors="coerce").fillna(0)
    pf_dist = pd.to_numeric(out.get("pf_dist", 0), errors="coerce").fillna(0)
    base_derivada = preco_sem / (1 - (desconto_normal / 100)).clip(lower=0.0001)
    base_preco = pf_fabrica.where(pf_fabrica.gt(0), pf_dist.where(pf_dist.gt(0), base_derivada))
    desconto_acao = pd.to_numeric(out["desconto_acao"], errors="coerce").fillna(0).clip(0, 99.99)

    out.loc[mask, "acao_desconto"] = True
    out.loc[mask, "preco_base_acao"] = base_preco[mask].round(2)
    out.loc[mask, "desconto"] = desconto_acao[mask].round(2)
    out.loc[mask, "preco_sem_imposto"] = (base_preco[mask] * (1 - desconto_acao[mask] / 100)).round(2)
    out = out.drop(columns=["produto_acao"], errors="ignore")
    return out
