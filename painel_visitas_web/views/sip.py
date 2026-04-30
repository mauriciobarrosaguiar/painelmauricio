from __future__ import annotations

import pandas as pd
import streamlit as st

from services.order_status import (
    STATUS_OPTIONS,
    build_order_detail,
    display_order_detail,
    excel_bytes as order_excel_bytes,
    filter_order_detail,
    money as status_money,
    summarize_order_detail,
)
from services.repo_state import repo_load_json, repo_save_json

SIP_REL_PATH = "data/sip_grupos.json"


def _money(value) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _pct(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%".replace(".", ",")
    except Exception:
        return "0,0%"


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _metric_card(label: str, value: str, help_text: str = "") -> str:
    return (
        "<div class='metric-card metric-center'>"
        f"<div class='metric-label'>{label}</div>"
        f"<div class='metric-value'>{value}</div>"
        f"<div class='metric-help'>{help_text}</div>"
        "</div>"
    )


def load_sip_groups() -> list[dict]:
    groups = repo_load_json(SIP_REL_PATH, [], prefer_remote=True)
    return groups if isinstance(groups, list) else []


def save_sip_groups(groups: list[dict]):
    return repo_save_json(SIP_REL_PATH, list(groups or []), "Atualizar grupos SIP")


def selected_group() -> dict | None:
    groups = load_sip_groups()
    gid = st.session_state.get("sip_selected_id")
    for group in groups:
        if group.get("id") == gid:
            return group
    return groups[0] if groups else None


def build_sip_summary(score_df: pd.DataFrame) -> pd.DataFrame:
    groups = load_sip_groups()
    empty_cols = ["SIP", "CNPJs", "Faturado", "OL Prioritarios", "OL Lancamentos", "Meta", "Atingimento", "Falta regra", "Pagamento"]
    if not groups:
        return pd.DataFrame(columns=empty_cols)

    base = score_df.copy() if isinstance(score_df, pd.DataFrame) else pd.DataFrame()
    if base.empty:
        return pd.DataFrame(columns=empty_cols)

    base["cnpj_norm"] = base.get("cnpj", pd.Series(dtype=str)).astype(str).map(_digits)
    base["total_faturado"] = pd.to_numeric(base.get("total_faturado", 0), errors="coerce").fillna(0.0)
    base["ol_prioritarios"] = pd.to_numeric(base.get("ol_prioritarios", 0), errors="coerce").fillna(0.0)
    base["ol_lancamentos"] = pd.to_numeric(base.get("ol_lancamentos", 0), errors="coerce").fillna(0.0)

    rows = []
    for group in groups:
        cnpjs = {_digits(item) for item in group.get("cnpjs", [])}
        sip_base = base[base["cnpj_norm"].isin(cnpjs)].copy() if cnpjs else pd.DataFrame()
        faturado = float(sip_base["total_faturado"].sum()) if not sip_base.empty else 0.0
        faturado_prio = float(sip_base["ol_prioritarios"].sum()) if not sip_base.empty else 0.0
        faturado_lanc = float(sip_base["ol_lancamentos"].sum()) if not sip_base.empty else 0.0
        meta = float(group.get("meta_mes", 0) or 0)
        pagamento = float(group.get("pagamento_percentual", 80) or 80)
        rows.append(
            {
                "SIP": group.get("nome", ""),
                "ID": group.get("id", ""),
                "CNPJs": len(group.get("cnpjs", [])),
                "Faturado": faturado,
                "OL Prioritarios": faturado_prio,
                "OL Lancamentos": faturado_lanc,
                "Meta": meta,
                "Atingimento": (faturado / meta) if meta else 0.0,
                "Falta regra": max(0.0, (meta * (pagamento / 100.0)) - faturado) if meta else 0.0,
                "Pagamento": pagamento,
            }
        )

    return pd.DataFrame(rows).sort_values(["Faturado", "SIP"], ascending=[False, True]).reset_index(drop=True)


def _default_period(detail: pd.DataFrame):
    datas = pd.to_datetime(detail.get("Data pedido", pd.Series(dtype="datetime64[ns]")), errors="coerce").dropna()
    if datas.empty:
        hoje = pd.Timestamp.today().normalize()
        return hoje.replace(day=1).date(), hoje.date()
    fim = datas.max().normalize()
    return fim.replace(day=1).date(), fim.date()


def _render_pedidos_sip(base_full: pd.DataFrame | None, group: dict):
    group_cnpjs = {_digits(item) for item in group.get("cnpjs", [])}
    detail_all = build_order_detail(base_full if base_full is not None else pd.DataFrame(), group_cnpjs)
    st.markdown('<div class="section-title">Pedidos e notas da SIP</div>', unsafe_allow_html=True)
    if detail_all.empty:
        st.info("Nenhum pedido encontrado no Bussola para os CNPJs deste grupo.")
        return

    default_inicio, default_fim = _default_period(detail_all)
    gid = str(group.get("id", "sip"))
    f1, f2, f3 = st.columns([1, 1, 1.1])
    data_inicio = f1.date_input("Data inicial", value=default_inicio, format="DD/MM/YYYY", key=f"sip_ped_ini_{gid}")
    data_fim = f2.date_input("Data final", value=default_fim, format="DD/MM/YYYY", key=f"sip_ped_fim_{gid}")
    status_sel = f3.selectbox("Status do pedido", STATUS_OPTIONS, index=0, key=f"sip_ped_status_{gid}")
    if data_inicio > data_fim:
        st.warning("A data inicial nao pode ser maior que a data final.")
        return

    detail = filter_order_detail(detail_all, data_inicio, data_fim, status_sel)
    resumo = summarize_order_detail(detail)
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(
            f"<div class='metric-card metric-center'><div class='metric-label'>Pedidos faturados</div><div class='metric-value'>{resumo['faturado_qtd']}</div><div class='metric-help'>{status_money(resumo['faturado_valor'])} faturado</div></div>",
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f"<div class='metric-card metric-center'><div class='metric-label'>Sem nota</div><div class='metric-value'>{resumo['sem_nota_qtd']}</div><div class='metric-help'>{status_money(resumo['sem_nota_valor'])} a faturar</div></div>",
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            f"<div class='metric-card metric-center'><div class='metric-label'>Cancelados</div><div class='metric-value'>{resumo['cancelado_qtd']}</div><div class='metric-help'>{status_money(resumo['cancelado_valor'])} cancelado</div></div>",
            unsafe_allow_html=True,
        )

    export_cols = [
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
    st.download_button(
        "Extrair pedidos detalhados da SIP",
        data=order_excel_bytes(detail[export_cols], "Pedidos SIP"),
        file_name=f"pedidos_sip_{gid}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        disabled=detail.empty,
    )
    if detail.empty:
        st.info("Nenhum pedido encontrado para os filtros selecionados.")
    else:
        st.dataframe(display_order_detail(detail[export_cols]), use_container_width=True, hide_index=True)


def render_sip(score_df: pd.DataFrame, clientes_df: pd.DataFrame, base_full: pd.DataFrame | None = None):
    st.markdown('<h2 class="page-title">SIP / Redes</h2>', unsafe_allow_html=True)
    groups = load_sip_groups()
    cli_ref = clientes_df[["cnpj", "nome_fantasia"]].drop_duplicates().sort_values("nome_fantasia").copy()
    cli_ref["label"] = cli_ref["nome_fantasia"].astype(str) + " - " + cli_ref["cnpj"].astype(str)
    label_to_cnpj = dict(zip(cli_ref["label"], cli_ref["cnpj"]))

    summary = build_sip_summary(score_df)
    if not summary.empty:
        show_summary = summary.copy()
        show_summary["Faturado"] = show_summary["Faturado"].map(_money)
        show_summary["OL Prioritarios"] = show_summary["OL Prioritarios"].map(_money)
        show_summary["OL Lancamentos"] = show_summary["OL Lancamentos"].map(_money)
        show_summary["Meta"] = show_summary["Meta"].map(_money)
        show_summary["Atingimento"] = show_summary["Atingimento"].map(_pct)
        show_summary["Falta regra"] = show_summary["Falta regra"].map(_money)
        show_summary["Pagamento"] = show_summary["Pagamento"].map(lambda value: f"{int(value)}%")
        st.markdown("### Panorama das SIPs")
        st.dataframe(
            show_summary[["SIP", "CNPJs", "Faturado", "OL Prioritarios", "OL Lancamentos", "Meta", "Atingimento", "Falta regra", "Pagamento"]],
            use_container_width=True,
            hide_index=True,
        )

    current = selected_group()
    names = ["Novo grupo"] + [group["nome"] for group in groups]
    idx = names.index(current["nome"]) if current and current["nome"] in names else 0
    choice = st.selectbox("Grupo SIP para cadastrar ou editar", names, index=idx)
    editing = next((group for group in groups if group["nome"] == choice), None) if choice != "Novo grupo" else None

    st.markdown("### Cadastro")
    c1, c2 = st.columns([1.8, 1.0])
    nome = c1.text_input("Nome do grupo economico", value=editing.get("nome", "") if editing else "")
    meta = c2.number_input("Meta do mes", min_value=0.0, step=100.0, value=float(editing.get("meta_mes", 0.0)) if editing else 0.0)
    default_members = [label for label, cnpj in label_to_cnpj.items() if editing and str(cnpj) in [str(item) for item in editing.get("cnpjs", [])]]
    membros = st.multiselect("CNPJs da rede", cli_ref["label"].tolist(), default=default_members, placeholder="Escolha as opcoes")
    pagamento = st.number_input("Pagamento a partir de (%)", min_value=0.0, max_value=100.0, value=float(editing.get("pagamento_percentual", 80.0)) if editing else 80.0, step=1.0)

    s1, s2 = st.columns(2)
    if s1.button("Salvar grupo SIP", use_container_width=True, disabled=not nome or not membros):
        gid = editing.get("id") if editing else nome.strip().lower().replace(" ", "_")
        novo = {
            "id": gid,
            "nome": nome.strip(),
            "meta_mes": float(meta),
            "pagamento_percentual": float(pagamento),
            "cnpjs": [str(label_to_cnpj[item]) for item in membros],
        }
        groups = [group for group in groups if group.get("id") != gid] + [novo]
        ok, msg = save_sip_groups(groups)
        st.session_state["sip_selected_id"] = gid
        (st.success if ok else st.warning)(f"Grupo SIP salvo. {msg}")
        if ok:
            st.rerun()

    if editing and s2.button("Excluir grupo", use_container_width=True):
        groups = [group for group in groups if group.get("id") != editing.get("id")]
        ok, msg = save_sip_groups(groups)
        st.session_state["sip_selected_id"] = None
        (st.success if ok else st.warning)(f"Grupo removido. {msg}")
        if ok:
            st.rerun()

    groups = load_sip_groups()
    if not groups:
        st.info("Nenhum grupo SIP cadastrado.")
        return

    labels = [group["nome"] for group in groups]
    current = selected_group() or groups[0]
    idx = labels.index(current["nome"]) if current["nome"] in labels else 0
    chosen = st.selectbox("Grupo para analise", labels, index=idx)
    group = next(group for group in groups if group["nome"] == chosen)
    st.session_state["sip_selected_id"] = group["id"]

    base = score_df.copy()
    base["cnpj_norm"] = base["cnpj"].astype(str).map(_digits)
    group_cnpjs = {_digits(item) for item in group.get("cnpjs", [])}
    base = base[base["cnpj_norm"].isin(group_cnpjs)].copy()
    faturado = float(pd.to_numeric(base.get("total_faturado", 0), errors="coerce").fillna(0).sum()) if not base.empty else 0.0
    faturado_prio = float(pd.to_numeric(base.get("ol_prioritarios", 0), errors="coerce").fillna(0).sum()) if not base.empty else 0.0
    faturado_lanc = float(pd.to_numeric(base.get("ol_lancamentos", 0), errors="coerce").fillna(0).sum()) if not base.empty else 0.0
    meta_mes = float(group.get("meta_mes", 0) or 0)
    pagamento_min = float(group.get("pagamento_percentual", 80) or 80)
    atingimento = (faturado / meta_mes) if meta_mes else 0.0
    falta_regra = max(0.0, (meta_mes * (pagamento_min / 100.0)) - faturado) if meta_mes else 0.0

    row_top = st.columns(3)
    row_bottom = st.columns(3)
    with row_top[0]:
        st.markdown(_metric_card("CNPJs", str(len(group.get("cnpjs", [])))), unsafe_allow_html=True)
    with row_top[1]:
        st.markdown(_metric_card("Meta", _money(meta_mes)), unsafe_allow_html=True)
    with row_top[2]:
        st.markdown(_metric_card("Faturado", _money(faturado)), unsafe_allow_html=True)
    with row_bottom[0]:
        st.markdown(_metric_card("OL prioritarios", _money(faturado_prio)), unsafe_allow_html=True)
    with row_bottom[1]:
        st.markdown(_metric_card("OL lancamentos", _money(faturado_lanc)), unsafe_allow_html=True)
    with row_bottom[2]:
        st.markdown(_metric_card("Falta regra", _money(falta_regra)), unsafe_allow_html=True)
    st.caption(f"Atingimento: {_pct(atingimento)} | Pagamento a partir de {pagamento_min:.0f}%")

    _render_pedidos_sip(base_full, group)

    if not base.empty:
        show = base[["nome_fantasia", "cnpj", "cidade", "total_faturado", "ol_prioritarios", "ol_lancamentos"]].copy()
        show.columns = ["Cliente", "CNPJ", "Cidade", "Faturado", "Prioritarios", "Lancamentos"]
        for col in ["Faturado", "Prioritarios", "Lancamentos"]:
            show[col] = pd.to_numeric(show[col], errors="coerce").fillna(0).map(_money)
        st.dataframe(show, use_container_width=True, hide_index=True)
