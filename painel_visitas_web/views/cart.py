from __future__ import annotations

import pandas as pd
import streamlit as st

from services.order_builder import build_order_dataframe, build_order_exports, build_order_payload, save_generated_order
from services.repo_state import enqueue_command, load_status
from views.monitoring import render_monitor

MINIMOS = {
    "Total - TO": 300.0,
    "Panpharma - GO": None,
    "Nazaria - MA - Imperatriz": 200.0,
    "Profarma - DF": 200.0,
}


def _money(value):
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _plain_digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _safe_float(value, default: float = 0.0) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(number) else number)


def _safe_int(value, default: int = 0) -> int:
    number = pd.to_numeric(value, errors="coerce")
    return int(default if pd.isna(number) else number)


def _normalize_state_item(item: dict) -> dict:
    return {
        "Cliente": str(item.get("Cliente", "")),
        "CNPJ": _plain_digits(item.get("CNPJ", "")),
        "Empresa": str(item.get("Empresa", "")),
        "Razao social": str(item.get("Razao social", item.get("Razao Social", ""))),
        "Nome do comprador": str(item.get("Nome do comprador", "")),
        "Tel do comprador": _plain_digits(item.get("Tel do comprador", "")),
        "EAN": _plain_digits(item.get("EAN", "")),
        "Produto": str(item.get("Produto", "")),
        "Distribuidora": str(item.get("Distribuidora", "") or "Sem distribuidora"),
        "Preco": round(_safe_float(item.get("Preco", item.get("Preco", 0)), 0.0), 2),
        "Estoque": max(0, _safe_int(item.get("Estoque", 0), 0)),
        "Mix": str(item.get("Mix", "LINHA") or "LINHA"),
        "Qtde": max(0, _safe_int(item.get("Qtde", 0), 0)),
        "Foco": bool(item.get("Foco", False)),
    }


def _pedidos_minimos(df: pd.DataFrame) -> tuple[bool, list[dict]]:
    grouped = df.groupby("Distribuidora", as_index=False)["Total"].sum()
    rows = []
    can_send = True
    for _, row in grouped.iterrows():
        minimo = MINIMOS.get(row["Distribuidora"])
        atingido = True if minimo is None else row["Total"] >= minimo
        if not atingido:
            can_send = False
        status = "Sem minimo" if minimo is None else ("Atingido" if atingido else f"Faltam {_money(minimo - row['Total'])}")
        rows.append(
            {
                "Distribuidora": row["Distribuidora"],
                "Total pedido": _money(row["Total"]),
                "Pedido minimo": "Sem minimo" if minimo is None else _money(minimo),
                "Status": status,
            }
        )
    return can_send, rows


def render_cart(inventario: pd.DataFrame | None = None, foco: pd.DataFrame | None = None):
    st.markdown('<h2 class="page-title">Carrinho</h2>', unsafe_allow_html=True)

    raw_items = st.session_state.get("cart_items", [])
    items = [_normalize_state_item(item) for item in raw_items]
    items = [item for item in items if item["EAN"]]
    st.session_state.cart_items = items

    if not items:
        st.info("Seu carrinho esta vazio.")
        return

    inv = inventario.copy() if isinstance(inventario, pd.DataFrame) else pd.DataFrame()
    if not inv.empty:
        inv["ean"] = inv["ean"].astype(str)
        inv["preco_sem_imposto"] = pd.to_numeric(inv.get("preco_sem_imposto", 0), errors="coerce").fillna(0.0)
        inv["estoque"] = pd.to_numeric(inv.get("estoque", 0), errors="coerce").fillna(0).astype(int)

    header_df = build_order_dataframe(items)
    header = header_df.iloc[0].to_dict() if not header_df.empty else {}
    if header:
        st.markdown(
            (
                f"<div class='detail-card'><div class='detail-title'>{header.get('Empresa', '')}</div>"
                f"<div class='detail-sub'>Razao social: {header.get('Razao social', '')} | CNPJ: {header.get('CNPJ', '')}</div>"
                f"<div class='detail-sub'>Comprador: {header.get('Nome do comprador', '')} | Tel: {header.get('Tel do comprador', '')}</div></div>"
            ),
            unsafe_allow_html=True,
        )

    total_df = build_order_dataframe(items)
    m1, m2, m3 = st.columns(3)
    m1.metric("Itens", str(int(total_df["Qtde"].sum())))
    m2.metric("Produtos", str(int(total_df["EAN"].nunique())))
    m3.metric("Total", _money(total_df["Total"].sum()))

    st.markdown('<div class="section-title">Itens do pedido</div>', unsafe_allow_html=True)
    updated = [item.copy() for item in items]
    remove_idx = None

    for idx, item in enumerate(updated):
        ean = str(item.get("EAN", ""))
        variants = inv[inv["ean"] == ean].copy().sort_values(["preco_sem_imposto", "estoque"], ascending=[True, False]) if not inv.empty else pd.DataFrame()
        if variants.empty:
            variants = pd.DataFrame(
                [
                    {
                        "distribuidora": item.get("Distribuidora", "Sem distribuidora") or "Sem distribuidora",
                        "preco_sem_imposto": item.get("Preco", 0),
                        "estoque": item.get("Estoque", 0),
                    }
                ]
            )

        variants["distribuidora"] = variants["distribuidora"].fillna("Sem distribuidora").astype(str)
        options = variants["distribuidora"].tolist() or ["Sem distribuidora"]
        current_dist = item.get("Distribuidora", options[0])
        default_idx = options.index(current_dist) if current_dist in options else 0

        title = f"{item.get('Produto', '')} | {current_dist} | {item.get('Qtde', 0)} un."
        with st.expander(title, expanded=idx < 2):
            selected_dist = st.selectbox("Distribuidora", options, index=default_idx, key=f"cart_dist_{idx}")
            selected_row = variants[variants["distribuidora"] == selected_dist]
            choice = selected_row.iloc[0] if not selected_row.empty else variants.iloc[0]

            qty = st.number_input("Quantidade", min_value=0, step=1, value=int(item.get("Qtde", 0)), key=f"cart_qtd_{idx}")
            price = _safe_float(choice.get("preco_sem_imposto", item.get("Preco", 0)), 0.0)
            stock = _safe_int(choice.get("estoque", item.get("Estoque", 0)), 0)

            info1, info2, info3 = st.columns(3)
            info1.metric("Preco", _money(price))
            info2.metric("Estoque", str(stock))
            info3.metric("Total", _money(price * qty))

            if st.button("Remover item", key=f"del_{idx}", use_container_width=True):
                remove_idx = idx

            updated[idx]["Distribuidora"] = selected_dist
            updated[idx]["Preco"] = price
            updated[idx]["Estoque"] = stock
            updated[idx]["Qtde"] = int(qty)

    if remove_idx is not None:
        updated.pop(remove_idx)
        st.session_state.cart_items = updated
        st.rerun()

    updated = [item for item in updated if int(item.get("Qtde", 0)) > 0]
    st.session_state.cart_items = updated
    if not updated:
        st.info("Seu carrinho ficou vazio.")
        return

    df = build_order_dataframe(updated)
    can_send, minimum_rows = _pedidos_minimos(df)
    if not can_send:
        st.warning("Ainda existem distribuidoras abaixo do pedido minimo para envio.")

    st.markdown('<div class="section-title">Minimo por distribuidora</div>', unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(minimum_rows), use_container_width=True, hide_index=True)

    status = load_status()
    headless = st.toggle("Enviar invisivel", value=True)
    cupom = st.text_input("Cupom de desconto (opcional)", value=st.session_state.get("mf_cupom", ""))
    st.session_state["mf_cupom"] = cupom

    payload = build_order_payload(updated, cupom=cupom, headless=headless)
    exports = build_order_exports(payload)

    cnpj_envio = header.get("CNPJ", "")
    a1, a2, a3, a4, a5 = st.columns(5)
    if a1.button("Limpar carrinho", use_container_width=True):
        st.session_state.cart_items = []
        st.rerun()
    if a2.button("Atualizar arquivo do pedido", use_container_width=True):
        save_generated_order(updated, cupom=cupom, headless=headless)
        st.success("Pedido gerado atualizado.")
    a3.download_button("Baixar CSV", data=exports["csv_bytes"], file_name="pedido_gerado.csv", mime="text/csv", use_container_width=True)
    a4.download_button("Baixar TXT", data=exports["txt_bytes"], file_name="pedido_gerado.txt", mime="text/plain", use_container_width=True)
    if a5.button("Limpar pedido MF", use_container_width=True, disabled=not bool(cnpj_envio)):
        _, ok, msg = enqueue_command("limpar_pedido_mf", {"cnpj": cnpj_envio, "headless": headless})
        (st.success if ok else st.error)(msg)

    confirm_send = st.checkbox("Confirmo o envio do pedido ao Mercado Farma", value=False)
    if st.button("Enviar pedido para Mercado Farma", use_container_width=True, disabled=(not can_send) or (not confirm_send)):
        save_generated_order(updated, cupom=cupom, headless=headless)
        _, ok, msg = enqueue_command("enviar_pedido_mf", {"cart_items": updated, "headless": headless, "cupom": cupom})
        (st.success if ok else st.error)(msg)

    st.markdown("### Acompanhamento do envio")
    render_monitor("Mercado Farma", status.get("comandos", {}), key_prefix="cart_monitor", empty_message="Nenhum envio recente.")
