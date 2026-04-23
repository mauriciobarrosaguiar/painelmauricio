from __future__ import annotations

from datetime import date, datetime
from io import StringIO
import re
import unicodedata

import pandas as pd

TYPE_MELHOR_PRECO = "MELHOR_PRECO"
TYPE_COMBO = "COMBO"
TYPE_PROGRESSIVO = "PROGRESSIVO"

ACTION_FIELDS = (
    "tipo_acao",
    "nome_acao",
    "ean",
    "produto",
    "desconto",
    "distribuidora",
    "cupom",
    "validade",
    "qtd_minima",
    "qtd_de",
    "qtd_ate",
)


def _strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _norm_col(value: str) -> str:
    text = _strip_accents(value).lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _norm_text(value: str) -> str:
    text = _strip_accents(value).lower()
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


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


def _int_number(value, default: int = 0) -> int:
    if pd.isna(value):
        return default
    text = str(value or "").strip().lower()
    if not text:
        return default
    if any(token in text for token in ("acima", "livre", "+")):
        digits = _digits(text)
        return int(digits) if digits else default
    number = pd.to_numeric(text.replace(",", "."), errors="coerce")
    if pd.isna(number):
        digits = _digits(text)
        return int(digits) if digits else default
    return int(number)


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


def _normalize_type(value: str) -> str:
    text = _norm_col(value)
    if "combo" in text:
        return TYPE_COMBO
    if any(token in text for token in ("progressivo", "progressiva", "escalonado", "volume")):
        return TYPE_PROGRESSIVO
    return TYPE_MELHOR_PRECO


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
        "tipo": "tipo_acao",
        "tipo_acao": "tipo_acao",
        "tipo_da_acao": "tipo_acao",
        "tipo_de_acao": "tipo_acao",
        "modalidade": "tipo_acao",
        "acao_tipo": "tipo_acao",
        "nome": "nome_acao",
        "nome_acao": "nome_acao",
        "nome_da_acao": "nome_acao",
        "acao": "nome_acao",
        "campanha": "nome_acao",
        "combo": "nome_acao",
        "nome_combo": "nome_acao",
        "ean": "ean",
        "codigo_ean": "ean",
        "produto": "produto",
        "nome_produto": "produto",
        "nome_do_produto": "produto",
        "principio_ativo": "produto",
        "desconto": "desconto",
        "desc": "desconto",
        "desconto_acao": "desconto",
        "desconto_percentual": "desconto",
        "distribuidora": "distribuidora",
        "cupom": "cupom",
        "validade": "validade",
        "validade_da_acao": "validade",
        "validade_acao": "validade",
        "data_validade": "validade",
        "qtd_minima": "qtd_minima",
        "qtde_minima": "qtd_minima",
        "quantidade_minima": "qtd_minima",
        "quantidade_da_acao": "qtd_minima",
        "qtd_acao": "qtd_minima",
        "qtd_padrao": "qtd_minima",
        "quantidade_padrao": "qtd_minima",
        "minima": "qtd_minima",
        "qtd_de": "qtd_de",
        "quantidade_de": "qtd_de",
        "volume_de": "qtd_de",
        "faixa_de": "qtd_de",
        "de": "qtd_de",
        "qtd_ate": "qtd_ate",
        "quantidade_ate": "qtd_ate",
        "volume_ate": "qtd_ate",
        "faixa_ate": "qtd_ate",
        "ate": "qtd_ate",
    }
    out: dict[str, str] = {}
    for col in df.columns:
        canonical = aliases.get(_norm_col(col))
        if canonical and canonical not in out:
            out[canonical] = col
    return out


def _get_row_value(row, lookup: dict[str, str], field: str, default=""):
    col = lookup.get(field)
    if not col:
        return default
    return row.get(col, default)


def _canonical_action(rec: dict) -> dict:
    tipo = _normalize_type(rec.get("tipo_acao", rec.get("tipo", rec.get("tipo_da_acao", ""))))
    desconto = _br_number(rec.get("desconto", rec.get("desconto_acao", 0)))
    qtd_minima = _int_number(rec.get("qtd_minima", rec.get("qtd_padrao", 0)), 0)
    qtd_de = _int_number(rec.get("qtd_de", 0), 0)
    qtd_ate = _int_number(rec.get("qtd_ate", 0), 0)

    if tipo == TYPE_COMBO:
        qtd_minima = max(1, qtd_minima or qtd_de or 1)
        qtd_de = 0
        qtd_ate = 0
    elif tipo == TYPE_PROGRESSIVO:
        qtd_de = max(1, qtd_de or qtd_minima or 1)
        qtd_minima = qtd_de
        qtd_ate = max(0, qtd_ate)
    else:
        qtd_minima = max(1, qtd_minima or 1)
        qtd_de = 1
        qtd_ate = 0

    return {
        "tipo_acao": tipo,
        "nome_acao": str(rec.get("nome_acao", rec.get("acao", "")) or "").strip(),
        "ean": _digits(rec.get("ean", "")),
        "produto": str(rec.get("produto", "") or "").strip(),
        "desconto": round(float(desconto), 4),
        "distribuidora": str(rec.get("distribuidora", "") or "").strip(),
        "cupom": str(rec.get("cupom", "") or "").strip(),
        "validade": _parse_date(rec.get("validade", rec.get("validade_da_acao", ""))),
        "qtd_minima": int(qtd_minima),
        "qtd_de": int(qtd_de),
        "qtd_ate": int(qtd_ate),
    }


def parse_discount_actions(
    pasted_text: str,
    default_distribuidora: str = "",
    default_validade=None,
) -> tuple[list[dict], list[str]]:
    df = _read_pasted_table(pasted_text)
    if df.empty:
        return [], [
            "Cole uma tabela com TIPO_ACAO, NOME_ACAO, EAN, PRODUTO, DESCONTO, DISTRIBUIDORA, CUPOM, VALIDADE_DA_ACAO, QTD_MINIMA, QTD_DE e QTD_ATE."
        ]

    lookup = _column_lookup(df)
    if "desconto" not in lookup:
        return [], ["Coluna obrigatoria ausente: DESCONTO."]
    if "ean" not in lookup and "produto" not in lookup:
        return [], ["Informe ao menos EAN ou PRODUTO na planilha de acoes."]

    records: list[dict] = []
    errors: list[str] = []
    for idx, row in df.iterrows():
        line = idx + 2
        raw_tipo = _get_row_value(row, lookup, "tipo_acao", TYPE_MELHOR_PRECO)
        tipo = _normalize_type(raw_tipo)
        ean = _digits(_get_row_value(row, lookup, "ean", ""))
        produto = str(_get_row_value(row, lookup, "produto", "") or "").strip()
        desconto = _br_number(_get_row_value(row, lookup, "desconto", ""))
        distribuidora = str(_get_row_value(row, lookup, "distribuidora", "") or default_distribuidora or "").strip()
        validade = _parse_date(_get_row_value(row, lookup, "validade", ""), default_validade)
        cupom = str(_get_row_value(row, lookup, "cupom", "") or "").strip()
        nome_acao = str(_get_row_value(row, lookup, "nome_acao", "") or "").strip()
        qtd_minima = _int_number(_get_row_value(row, lookup, "qtd_minima", ""), 0)
        qtd_de = _int_number(_get_row_value(row, lookup, "qtd_de", ""), 0)
        qtd_ate = _int_number(_get_row_value(row, lookup, "qtd_ate", ""), 0)

        if not ean and not produto:
            errors.append(f"Linha {line}: informe EAN ou produto.")
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
        if tipo == TYPE_PROGRESSIVO and qtd_ate and qtd_de and qtd_ate < qtd_de:
            errors.append(f"Linha {line}: QTD_ATE menor que QTD_DE.")
            continue

        records.append(
            _canonical_action(
                {
                    "tipo_acao": tipo,
                    "nome_acao": nome_acao,
                    "ean": ean,
                    "produto": produto,
                    "desconto": desconto,
                    "distribuidora": distribuidora,
                    "cupom": cupom,
                    "validade": validade,
                    "qtd_minima": qtd_minima,
                    "qtd_de": qtd_de,
                    "qtd_ate": qtd_ate,
                }
            )
        )
    return records, errors


def actions_to_dataframe(records) -> pd.DataFrame:
    rows = []
    today = date.today()
    for rec in list(records or []):
        action = _canonical_action(dict(rec or {}))
        validade_dt = pd.to_datetime(action["validade"], errors="coerce")
        status = "Vigente" if not pd.isna(validade_dt) and validade_dt.date() >= today else "Vencida"
        action["status"] = status
        rows.append(action)
    df = pd.DataFrame(rows, columns=[*ACTION_FIELDS, "status"])
    if df.empty:
        return df
    return df[(df["ean"].ne("") | df["produto"].ne("")) & df["distribuidora"].ne("")].reset_index(drop=True)


def _records_from_key(action_key=()) -> list[dict]:
    records = []
    for row in action_key or []:
        if len(row) >= len(ACTION_FIELDS):
            records.append(dict(zip(ACTION_FIELDS, row[: len(ACTION_FIELDS)])))
        elif len(row) >= 6:
            records.append(
                {
                    "tipo_acao": TYPE_MELHOR_PRECO,
                    "nome_acao": "",
                    "ean": row[0],
                    "produto": row[1],
                    "desconto": row[2],
                    "distribuidora": row[3],
                    "cupom": row[4],
                    "validade": row[5],
                    "qtd_minima": 1,
                    "qtd_de": 1,
                    "qtd_ate": 0,
                }
            )
    return records


def actions_to_key(records) -> tuple[tuple, ...]:
    df = actions_to_dataframe(records)
    if df.empty:
        return tuple()
    return tuple(tuple(getattr(row, field) for field in ACTION_FIELDS) for row in df.itertuples(index=False))


def _active_actions(action_key=()) -> pd.DataFrame:
    actions = actions_to_dataframe(_records_from_key(action_key))
    if actions.empty:
        return actions
    today = pd.Timestamp(date.today())
    actions["validade_dt"] = pd.to_datetime(actions["validade"], errors="coerce")
    actions = actions[(actions["validade_dt"].notna()) & (actions["validade_dt"] >= today)].copy()
    actions["_ordem"] = range(len(actions))
    return actions


def _base_price(choice) -> float:
    pf_fabrica = pd.to_numeric(choice.get("pf_fabrica", 0), errors="coerce")
    pf_dist = pd.to_numeric(choice.get("pf_dist", 0), errors="coerce")
    preco_sem = pd.to_numeric(choice.get("preco_sem_imposto", 0), errors="coerce")
    desconto = pd.to_numeric(choice.get("desconto", 0), errors="coerce")
    pf_fabrica = 0.0 if pd.isna(pf_fabrica) else float(pf_fabrica)
    pf_dist = 0.0 if pd.isna(pf_dist) else float(pf_dist)
    preco_sem = 0.0 if pd.isna(preco_sem) else float(preco_sem)
    desconto = 0.0 if pd.isna(desconto) else min(99.99, max(0.0, float(desconto)))
    if pf_fabrica > 0:
        return pf_fabrica
    if pf_dist > 0:
        return pf_dist
    return preco_sem / max(0.0001, 1 - desconto / 100)


def action_price_from_choice(choice, desconto: float) -> float:
    desconto = min(99.99, max(0.0, float(desconto or 0)))
    return round(_base_price(choice) * (1 - desconto / 100), 2)


def apply_action_to_choice(choice, action: dict | pd.Series | None):
    if not action:
        return choice
    out = choice.copy() if hasattr(choice, "copy") else dict(choice or {})
    desconto = _br_number(action.get("desconto", 0))
    out["acao_desconto"] = True
    out["tipo_acao"] = str(action.get("tipo_acao", "") or "")
    out["nome_acao"] = str(action.get("nome_acao", "") or "")
    out["desconto_acao"] = desconto
    out["cupom_acao"] = str(action.get("cupom", "") or "")
    out["validade_acao"] = str(action.get("validade", "") or "")
    out["preco_base_acao"] = round(_base_price(out), 2)
    out["desconto"] = round(desconto, 2)
    out["preco_sem_imposto"] = action_price_from_choice(out, desconto)
    return out


def _product_matches(action_product: str, product: str) -> bool:
    if not action_product or not product:
        return False
    return _norm_text(action_product) == _norm_text(product)


def _quantity_matches(action: pd.Series, quantidade: int) -> bool:
    tipo = str(action.get("tipo_acao", TYPE_MELHOR_PRECO))
    qtd = max(1, int(quantidade or 1))
    if tipo == TYPE_PROGRESSIVO:
        qtd_de = int(action.get("qtd_de", 1) or 1)
        qtd_ate = int(action.get("qtd_ate", 0) or 0)
        return qtd >= qtd_de and (qtd_ate <= 0 or qtd <= qtd_ate)
    return qtd >= int(action.get("qtd_minima", 1) or 1)


def find_action_for_item(
    action_key=(),
    ean: str = "",
    distribuidora: str = "",
    quantidade: int = 1,
    produto: str = "",
    tipo_preferido: str = "",
) -> dict | None:
    actions = _active_actions(action_key)
    if actions.empty:
        return None

    ean = _digits(ean)
    distribuidora = str(distribuidora or "").strip()
    tipo_preferido = _normalize_type(tipo_preferido) if tipo_preferido else ""

    dist_mask = actions["distribuidora"].astype(str).str.strip().eq(distribuidora)
    if ean:
        item_mask = actions["ean"].astype(str).eq(ean)
    else:
        item_mask = pd.Series(False, index=actions.index)
    if produto:
        item_mask = item_mask | actions["produto"].astype(str).map(lambda value: _product_matches(value, produto))
    candidates = actions[dist_mask & item_mask].copy()
    if candidates.empty:
        return None
    if tipo_preferido:
        preferred = candidates[candidates["tipo_acao"].eq(tipo_preferido)].copy()
        if not preferred.empty:
            candidates = preferred

    candidates = candidates[candidates.apply(lambda row: _quantity_matches(row, quantidade), axis=1)].copy()
    if candidates.empty:
        return None
    candidates["desconto"] = pd.to_numeric(candidates["desconto"], errors="coerce").fillna(0.0)
    candidates["qtd_de"] = pd.to_numeric(candidates["qtd_de"], errors="coerce").fillna(0).astype(int)
    candidates = candidates.sort_values(["desconto", "qtd_de", "_ordem"], ascending=[False, False, False])
    return candidates.iloc[0][list(ACTION_FIELDS)].to_dict()


def combo_groups(records) -> list[dict]:
    df = actions_to_dataframe(records)
    if df.empty:
        return []
    df = df[(df["status"].eq("Vigente")) & (df["tipo_acao"].eq(TYPE_COMBO))].copy()
    if df.empty:
        return []
    groups = []
    group_cols = ["distribuidora", "nome_acao", "cupom", "validade"]
    for key, sub in df.sort_values(["distribuidora", "nome_acao", "produto"]).groupby(group_cols, dropna=False):
        distribuidora, nome_acao, cupom, validade = key
        groups.append(
            {
                "distribuidora": distribuidora,
                "nome_acao": nome_acao or "Combo",
                "cupom": cupom,
                "validade": validade,
                "itens": sub[list(ACTION_FIELDS)].to_dict("records"),
            }
        )
    return groups


def action_template_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "TIPO_ACAO": "MELHOR_PRECO",
                "NOME_ACAO": "OFERTAS ABRIL",
                "EAN": "7890000000001",
                "PRODUTO": "PRODUTO EXEMPLO",
                "DESCONTO": "72,97",
                "DISTRIBUIDORA": "Panpharma - GO",
                "CUPOM": "ABR26ONDAPP",
                "VALIDADE_DA_ACAO": "30/04/2026",
                "QTD_MINIMA": "1",
                "QTD_DE": "",
                "QTD_ATE": "",
            },
            {
                "TIPO_ACAO": "COMBO",
                "NOME_ACAO": "COMBO RINITE",
                "EAN": "7890000000002",
                "PRODUTO": "BILASTINA 20 MG X 15",
                "DESCONTO": "49,82",
                "DISTRIBUIDORA": "Panpharma - GO",
                "CUPOM": "RINITEABR26PP",
                "VALIDADE_DA_ACAO": "30/04/2026",
                "QTD_MINIMA": "10",
                "QTD_DE": "",
                "QTD_ATE": "",
            },
            {
                "TIPO_ACAO": "PROGRESSIVO",
                "NOME_ACAO": "DAPAGLIFLOZINA",
                "EAN": "7890000000003",
                "PRODUTO": "DAPAGLIFLOZINA 10MG X 30",
                "DESCONTO": "45,00",
                "DISTRIBUIDORA": "Panpharma - GO",
                "CUPOM": "DAPA4507A12PP",
                "VALIDADE_DA_ACAO": "30/04/2026",
                "QTD_MINIMA": "",
                "QTD_DE": "7",
                "QTD_ATE": "12",
            },
        ]
    )


def apply_discount_actions(inventario: pd.DataFrame, action_key=()) -> pd.DataFrame:
    if inventario is None or inventario.empty:
        return inventario
    out = inventario.copy()
    out["acao_desconto"] = False

    actions = _active_actions(action_key)
    if actions.empty:
        return out

    actions = actions[
        actions["ean"].astype(str).ne("")
        & (
            actions["tipo_acao"].eq(TYPE_MELHOR_PRECO)
            | ((actions["tipo_acao"].eq(TYPE_PROGRESSIVO)) & (pd.to_numeric(actions["qtd_de"], errors="coerce").fillna(1).le(1)))
        )
    ].copy()
    if actions.empty:
        return out

    actions["desconto"] = pd.to_numeric(actions["desconto"], errors="coerce").fillna(0.0)
    actions = actions.sort_values(["ean", "distribuidora", "desconto", "_ordem"]).drop_duplicates(["ean", "distribuidora"], keep="last")
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
        actions[
            [
                "ean",
                "distribuidora",
                "tipo_acao",
                "nome_acao",
                "desconto_acao",
                "cupom_acao",
                "validade_acao",
                "produto_acao",
                "qtd_minima",
                "qtd_de",
                "qtd_ate",
            ]
        ],
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
