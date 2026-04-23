from __future__ import annotations

import pandas as pd
import streamlit as st

from services.discount_actions import actions_to_dataframe


def _money(value) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _pct(value) -> str:
    try:
        return f"{float(value):.2f}%".replace(".", ",")
    except Exception:
        return "0,00%"


def _format_date(value: str) -> str:
    dt = pd.to_datetime(value, errors="coerce")
    return "-" if pd.isna(dt) else dt.strftime("%d/%m/%Y")


def render_acoes_desconto(inventario: pd.DataFrame, action_records: list[dict] | None = None):
    st.markdown('<h2 class="page-title">Acoes de desconto</h2>', unsafe_allow_html=True)
    st.caption("Produtos com desconto promocional por distribuidora e validade.")

    inv = inventario.copy() if isinstance(inventario, pd.DataFrame) else pd.DataFrame()
    actions_df = actions_to_dataframe(action_records or [])

    if inv.empty:
        st.info("Atualize o Mercado Farma para visualizar os produtos com estoque e preco.")
        return

    acao_mask = inv.get("acao_desconto", False)
    if not isinstance(acao_mask, pd.Series):
        acao_mask = pd.Series(False, index=inv.index)
    vigentes = inv[acao_mask.fillna(False).astype(bool)].copy()

    dist_options = ["Todas"]
    if not inv.empty and "distribuidora" in inv.columns:
        dist_options += sorted([d for d in inv["distribuidora"].dropna().astype(str).unique().tolist() if d])
    f1, f2 = st.columns([1.1, 1.4])
    distribuidora = f1.selectbox("Distribuidora", dist_options, key="acoes_filtro_distribuidora")
    busca = f2.text_input("Buscar produto ou EAN", key="acoes_busca_produto")

    if distribuidora != "Todas" and not vigentes.empty:
        vigentes = vigentes[vigentes["distribuidora"].astype(str) == distribuidora]
    if busca and not vigentes.empty:
        termo = busca.strip().lower()
        digits = "".join(ch for ch in busca if ch.isdigit())
        mask = vigentes["principio_ativo"].astype(str).str.lower().str.contains(termo, na=False)
        if digits:
            mask = mask | vigentes["ean"].astype(str).str.contains(digits, na=False)
        vigentes = vigentes[mask]

    if vigentes.empty:
        st.info("Nenhuma acao vigente encontrada para os filtros atuais.")
    else:
        show = pd.DataFrame(
            {
                "Distribuidora": vigentes["distribuidora"].astype(str),
                "EAN": vigentes["ean"].astype(str),
                "Produto": vigentes["principio_ativo"].astype(str),
                "Estoque": pd.to_numeric(vigentes.get("estoque", 0), errors="coerce").fillna(0).astype(int),
                "Preco fabrica": pd.to_numeric(vigentes.get("preco_base_acao", 0), errors="coerce").fillna(0).map(_money),
                "Desconto acao": pd.to_numeric(vigentes.get("desconto_acao", 0), errors="coerce").fillna(0).map(_pct),
                "Preco acao": pd.to_numeric(vigentes.get("preco_sem_imposto", 0), errors="coerce").fillna(0).map(_money),
                "Cupom": vigentes.get("cupom_acao", "").astype(str),
                "Validade": vigentes.get("validade_acao", "").astype(str).map(_format_date),
            }
        )
        st.dataframe(show.sort_values(["Distribuidora", "Produto"]), use_container_width=True, hide_index=True)

    with st.expander("Todas as acoes cadastradas", expanded=False):
        if actions_df.empty:
            st.caption("Nenhuma acao cadastrada.")
        else:
            all_show = actions_df.copy()
            all_show.rename(
                columns={
                    "ean": "EAN",
                    "produto": "Produto",
                    "desconto": "Desconto",
                    "distribuidora": "Distribuidora",
                    "cupom": "Cupom",
                    "validade": "Validade",
                    "status": "Status",
                },
                inplace=True,
            )
            all_show["Desconto"] = all_show["Desconto"].map(_pct)
            all_show["Validade"] = all_show["Validade"].map(_format_date)
            st.dataframe(all_show, use_container_width=True, hide_index=True)
