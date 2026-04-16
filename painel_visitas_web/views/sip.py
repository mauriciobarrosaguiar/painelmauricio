from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from config import DATA_DIR

SIP_FILE = DATA_DIR / "sip_grupos.json"


def _money(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _pct(v):
    try:
        return f"{float(v) * 100:.1f}%".replace(".", ",")
    except Exception:
        return "0,0%"


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def load_sip_groups() -> list[dict]:
    if SIP_FILE.exists():
        try:
            return json.loads(SIP_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_sip_groups(groups: list[dict]):
    SIP_FILE.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")


def selected_group() -> dict | None:
    groups = load_sip_groups()
    gid = st.session_state.get("sip_selected_id")
    for group in groups:
        if group.get("id") == gid:
            return group
    return groups[0] if groups else None


def build_sip_summary(score_df: pd.DataFrame) -> pd.DataFrame:
    groups = load_sip_groups()
    if not groups:
        return pd.DataFrame(columns=["SIP", "CNPJs", "Faturado", "Meta", "Atingimento", "Falta regra"])

    base = score_df.copy() if isinstance(score_df, pd.DataFrame) else pd.DataFrame()
    if base.empty:
        return pd.DataFrame(columns=["SIP", "CNPJs", "Faturado", "Meta", "Atingimento", "Falta regra"])
    base["cnpj_norm"] = base.get("cnpj", pd.Series(dtype=str)).astype(str).map(_digits)
    base["total_faturado"] = pd.to_numeric(base.get("total_faturado", 0), errors="coerce").fillna(0.0)

    rows = []
    for group in groups:
        cnpjs = {_digits(x) for x in group.get("cnpjs", [])}
        fat = float(base[base["cnpj_norm"].isin(cnpjs)]["total_faturado"].sum()) if cnpjs else 0.0
        meta = float(group.get("meta_mes", 0) or 0)
        regra = float(group.get("pagamento_percentual", 80) or 80) / 100.0
        rows.append(
            {
                "SIP": group.get("nome", ""),
                "ID": group.get("id", ""),
                "CNPJs": len(group.get("cnpjs", [])),
                "Faturado": fat,
                "Meta": meta,
                "Atingimento": (fat / meta) if meta else 0.0,
                "Falta regra": max(0.0, (meta * regra) - fat) if meta else 0.0,
                "Pagamento": float(group.get("pagamento_percentual", 80) or 80),
            }
        )

    return pd.DataFrame(rows).sort_values(["Faturado", "SIP"], ascending=[False, True]).reset_index(drop=True)


def render_sip(score_df: pd.DataFrame, clientes_df: pd.DataFrame):
    st.markdown('<h2 class="page-title">SIP / Redes</h2>', unsafe_allow_html=True)
    groups = load_sip_groups()
    cli_ref = clientes_df[["cnpj", "nome_fantasia"]].drop_duplicates().sort_values("nome_fantasia").copy()
    cli_ref["label"] = cli_ref["nome_fantasia"].astype(str) + " - " + cli_ref["cnpj"].astype(str)
    label_to_cnpj = dict(zip(cli_ref["label"], cli_ref["cnpj"]))

    summary = build_sip_summary(score_df)
    if not summary.empty:
        show_summary = summary.copy()
        show_summary["Faturado"] = show_summary["Faturado"].map(_money)
        show_summary["Meta"] = show_summary["Meta"].map(_money)
        show_summary["Atingimento"] = show_summary["Atingimento"].map(_pct)
        show_summary["Falta regra"] = show_summary["Falta regra"].map(_money)
        show_summary["Pagamento"] = show_summary["Pagamento"].map(lambda v: f"{int(v)}%")
        st.markdown("### Panorama das SIPs")
        st.dataframe(show_summary[["SIP", "CNPJs", "Faturado", "Meta", "Atingimento", "Falta regra", "Pagamento"]], use_container_width=True, hide_index=True)

    current = selected_group()
    nomes = ["Novo grupo"] + [g["nome"] for g in groups]
    idx = nomes.index(current["nome"]) if current and current["nome"] in nomes else 0
    escolha = st.selectbox("Grupo SIP para cadastrar ou editar", nomes, index=idx)
    editing = next((g for g in groups if g["nome"] == escolha), None) if escolha != "Novo grupo" else None

    st.markdown("### Cadastro")
    c1, c2 = st.columns([1.8, 1.0])
    nome = c1.text_input("Nome do grupo economico", value=editing.get("nome", "") if editing else "")
    meta = c2.number_input("Meta do mes", min_value=0.0, step=100.0, value=float(editing.get("meta_mes", 0.0)) if editing else 0.0)
    membros_default = [lbl for lbl, cnpj in label_to_cnpj.items() if editing and str(cnpj) in [str(x) for x in editing.get("cnpjs", [])]]
    membros = st.multiselect("CNPJs da rede", cli_ref["label"].tolist(), default=membros_default, placeholder="Escolha as opcoes")
    pagamento = st.number_input("Pagamento a partir de (%)", min_value=0.0, max_value=100.0, value=float(editing.get("pagamento_percentual", 80.0)) if editing else 80.0, step=1.0)

    s1, s2 = st.columns(2)
    if s1.button("Salvar grupo SIP", use_container_width=True, disabled=not nome or not membros):
        gid = editing.get("id") if editing else nome.strip().lower().replace(" ", "_")
        novo = {"id": gid, "nome": nome.strip(), "meta_mes": float(meta), "pagamento_percentual": float(pagamento), "cnpjs": [str(label_to_cnpj[m]) for m in membros]}
        groups = [g for g in groups if g.get("id") != gid] + [novo]
        save_sip_groups(groups)
        st.session_state["sip_selected_id"] = gid
        st.success("Grupo SIP salvo.")
        st.rerun()

    if editing and s2.button("Excluir grupo", use_container_width=True):
        groups = [g for g in groups if g.get("id") != editing.get("id")]
        save_sip_groups(groups)
        st.session_state["sip_selected_id"] = None
        st.success("Grupo removido.")
        st.rerun()

    groups = load_sip_groups()
    if not groups:
        st.info("Nenhum grupo SIP cadastrado.")
        return

    labels = [g["nome"] for g in groups]
    current = selected_group() or groups[0]
    idx = labels.index(current["nome"]) if current["nome"] in labels else 0
    chosen = st.selectbox("Grupo para analise", labels, index=idx)
    group = next(g for g in groups if g["nome"] == chosen)
    st.session_state["sip_selected_id"] = group["id"]

    base = score_df.copy()
    base["cnpj_norm"] = base["cnpj"].astype(str).map(_digits)
    group_cnpjs = {_digits(x) for x in group.get("cnpjs", [])}
    base = base[base["cnpj_norm"].isin(group_cnpjs)].copy()
    faturado = float(pd.to_numeric(base.get("total_faturado", 0), errors="coerce").fillna(0).sum()) if not base.empty else 0.0
    meta_mes = float(group.get("meta_mes", 0) or 0)
    perc = (faturado / meta_mes) if meta_mes else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CNPJs", str(len(group.get("cnpjs", []))))
    m2.metric("Meta", _money(meta_mes))
    m3.metric("Faturado", _money(faturado))
    m4.metric("Atingimento", _pct(perc))
    st.caption(f"Pagamento a partir de {group.get('pagamento_percentual', 80):.0f}%")

    if not base.empty:
        show = base[["nome_fantasia", "cnpj", "cidade", "total_faturado", "ol_prioritarios", "ol_lancamentos"]].copy()
        show.columns = ["Cliente", "CNPJ", "Cidade", "Faturado", "Prioritarios", "Lancamentos"]
        for col in ["Faturado", "Prioritarios", "Lancamentos"]:
            show[col] = pd.to_numeric(show[col], errors="coerce").fillna(0).map(_money)
        st.dataframe(show, use_container_width=True, hide_index=True)
