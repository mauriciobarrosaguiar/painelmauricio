from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from config import DATA_DIR
from services.repo_state import command_to_monitor_block, load_latest_command, load_status

TZ_BR = ZoneInfo("America/Sao_Paulo")
META_FILE = DATA_DIR / "metas_dashboard.json"


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


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


def _wa_link(phone: str, msg: str = "") -> str:
    digits = _digits(phone)
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return f"https://wa.me/{digits}?text={quote(msg)}"


def _metric(label: str, value: str, help_text: str = ""):
    st.markdown(
        f"""
        <div class="metric-card metric-center">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-help">{help_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _metric_compact(label: str, value: str, help_text: str = ""):
    st.markdown(
        f"""
        <div style="
            border:1px solid rgba(15, 59, 43, 0.12);
            border-radius:16px;
            padding:10px 16px 9px 16px;
            background:#ffffff;
            min-height:70px;
            display:flex;
            flex-direction:column;
            justify-content:center;
            text-align:center;
            box-shadow:0 4px 12px rgba(15, 59, 43, 0.04);
        ">
            <div style="font-size:12px;font-weight:800;color:#5F7365;margin-bottom:3px;line-height:1.15;">{label}</div>
            <div style="font-size:15px;font-weight:900;color:#0F3B2B;line-height:1.1;margin-bottom:2px;">{value}</div>
            <div style="font-size:11px;color:#7A877E;line-height:1.1;">{help_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _compact_stat(label: str, value: str):
    st.markdown(
        f"""
        <div class="compact-stat">
            <div class="compact-stat-label">{label}</div>
            <div class="compact-stat-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _load_metas() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"meta_ol": 0.0, "meta_prioritarios": 0.0, "meta_lancamentos": 0.0, "meta_clientes": 0}


def _save_metas(data: dict):
    META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_br_datetime(value: str | None) -> str:
    if not value:
        return "-"
    text = str(value).strip()
    if not text:
        return "-"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_BR)
        else:
            dt = dt.astimezone(TZ_BR)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        pass
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            naive = datetime.strptime(text, fmt)
            return naive.replace(tzinfo=TZ_BR).strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            pass
    return text


def _first_existing(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols_map = {str(col).lower(): col for col in df.columns}
    for candidate in candidates:
        if candidate.lower() in cols_map:
            return cols_map[candidate.lower()]
    return None


def _numeric_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    col = _first_existing(df, candidates)
    if col is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(0.0)


def _build_period_sales(base_full: pd.DataFrame) -> tuple[dict[str, float], dict[str, bool]]:
    if base_full is None or base_full.empty:
        return {}, {}
    cnpj_col = _first_existing(base_full, ["cnpj", "cnpj_pdv"])
    if cnpj_col is None:
        return {}, {}
    faturado = _numeric_series(base_full, ["total_faturado", "valor_faturado", "faturado", "total fat.", "total_fat"])
    if faturado.empty:
        faturado = _numeric_series(base_full, ["total_solicitado", "valor_solicitado"])
    aux = pd.DataFrame({"cnpj": base_full[cnpj_col].astype(str), "faturado": faturado})
    aux["cnpj"] = aux["cnpj"].map(_digits)
    aux = aux[aux["cnpj"] != ""]
    grouped = aux.groupby("cnpj", dropna=False)["faturado"].sum()
    venda = grouped.to_dict()
    comprou = {cnpj: total > 0 for cnpj, total in venda.items()}
    return venda, comprou


def _status_card_value(block: dict, fallback_file: Path | None = None) -> tuple[str, str]:
    dt_text = block.get("ultimo_sucesso") or block.get("atualizado_em") or ""
    status = str(block.get("status", "-") or "-")
    if dt_text:
        return _parse_br_datetime(dt_text), status
    if fallback_file and fallback_file.exists():
        dt = datetime.fromtimestamp(fallback_file.stat().st_mtime, tz=TZ_BR)
        return dt.strftime("%d/%m/%Y %H:%M:%S"), status
    return "-", status


def _status_resume(block: dict) -> str:
    text = str(block.get("erro") or block.get("mensagem") or block.get("status") or "").strip()
    if not text:
        return "Sem atualizacao recente."
    return text if len(text) <= 100 else text[:97] + "..."


def render_dashboard(
    score_df: pd.DataFrame,
    oportunidades: pd.DataFrame,
    foco: pd.DataFrame | None = None,
    inventario: pd.DataFrame | None = None,
    clientes_df: pd.DataFrame | None = None,
    base_full: pd.DataFrame | None = None,
    data_inicio=None,
    data_fim=None,
):
    st.markdown('<h2 class="page-title">Dashboard</h2>', unsafe_allow_html=True)
    if data_inicio is not None and data_fim is not None:
        ini_text = data_inicio.strftime("%d/%m/%Y") if hasattr(data_inicio, "strftime") else str(data_inicio)
        fim_text = data_fim.strftime("%d/%m/%Y") if hasattr(data_fim, "strftime") else str(data_fim)
        st.caption(f"Periodo aplicado: {ini_text} ate {fim_text}")

    base = score_df.copy() if isinstance(score_df, pd.DataFrame) else pd.DataFrame()
    if base.empty:
        st.warning("Nenhum cliente encontrado para os filtros atuais.")
        return

    base_full = base_full.copy() if isinstance(base_full, pd.DataFrame) else pd.DataFrame()
    venda_periodo, comprou_periodo = _build_period_sales(base_full)
    base["cnpj_norm"] = base.get("cnpj", pd.Series(dtype=str)).astype(str).map(_digits)
    base["venda_periodo"] = base["cnpj_norm"].map(venda_periodo).fillna(0.0)
    base["comprou_periodo"] = base["cnpj_norm"].map(comprou_periodo).fillna(False)

    if clientes_df is not None and not clientes_df.empty:
        cols_merge = [col for col in ["cnpj", "nome_contato", "contato", "telefone_limpo"] if col in clientes_df.columns]
        if cols_merge:
            contato_ref = clientes_df[cols_merge].drop_duplicates("cnpj").copy()
            contato_ref["cnpj"] = contato_ref["cnpj"].astype(str).map(_digits)
            base = base.merge(contato_ref, left_on="cnpj_norm", right_on="cnpj", how="left", suffixes=("", "_cad"))

    total_ol = float(pd.to_numeric(base.get("ol_sem_combate", 0), errors="coerce").fillna(0).sum())
    total_combate = float(pd.to_numeric(base.get("ol_combate", 0), errors="coerce").fillna(0).sum())
    total_prio = float(pd.to_numeric(base.get("ol_prioritarios", 0), errors="coerce").fillna(0).sum())
    total_lanc = float(pd.to_numeric(base.get("ol_lancamentos", 0), errors="coerce").fillna(0).sum())
    clientes_com_ol = int((pd.to_numeric(base.get("ol_sem_combate", 0), errors="coerce").fillna(0) > 0).sum())
    clientes_com_venda = int(base["comprou_periodo"].sum())
    clientes_sem_venda = int((~base["comprou_periodo"]).sum())
    faturado_periodo = float(pd.to_numeric(base["venda_periodo"], errors="coerce").fillna(0).sum())
    perc_prio = (total_prio / total_ol) if total_ol else 0.0
    perc_lanc = (total_lanc / total_ol) if total_ol else 0.0
    metas = _load_metas()
    status_auto = load_status()
    latest_send = command_to_monitor_block(load_latest_command({"enviar_pedido_mf", "limpar_pedido_mf"}))

    bussola_dt, bussola_status = _status_card_value(status_auto.get("bussola", {}), DATA_DIR / "Pedidos.xlsx")
    mercado_dt, mercado_status = _status_card_value(status_auto.get("mercadofarma", {}), DATA_DIR / "Estoque_preco_distribuidora.xlsx")
    pedido_source = latest_send or status_auto.get("comandos", {})
    pedido_dt, pedido_status = _status_card_value(pedido_source, None)

    st.markdown('<div class="section-title">Ultimas atualizacoes</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        _metric_compact("Bussola", bussola_dt, bussola_status)
        st.caption(_status_resume(status_auto.get("bussola", {})))
    with c2:
        _metric_compact("Mercado Farma", mercado_dt, mercado_status)
        st.caption(_status_resume(status_auto.get("mercadofarma", {})))
    with c3:
        _metric_compact("Pedido MF", pedido_dt, pedido_status)
        st.caption(_status_resume(pedido_source))

    erros = []
    for nome, bloco in (
        ("Bussola", status_auto.get("bussola", {})),
        ("Mercado Farma", status_auto.get("mercadofarma", {})),
        ("Pedido MF", pedido_source),
    ):
        if str(bloco.get("status", "")).lower() in {"erro", "falha"}:
            erros.append(f"{nome}: {_status_resume(bloco)}")
    if erros:
        st.warning(" | ".join(erros))

    st.markdown('<div class="section-title">Indicadores do periodo</div>', unsafe_allow_html=True)
    row1 = st.columns(4)
    with row1[0]:
        _metric(
            "OL sem combate",
            _money(total_ol),
            f"Combate: {_money(total_combate)} | Meta: {_pct(total_ol / metas.get('meta_ol', 1)) if metas.get('meta_ol', 0) else '-'}",
        )
    with row1[1]:
        _metric(
            "OL prioritarios",
            _money(total_prio),
            f"{_pct(perc_prio)} do OL | Meta: {_pct(total_prio / metas.get('meta_prioritarios', 1)) if metas.get('meta_prioritarios', 0) else '-'}",
        )
    with row1[2]:
        _metric(
            "OL lancamentos",
            _money(total_lanc),
            f"{_pct(perc_lanc)} do OL | Meta: {_pct(total_lanc / metas.get('meta_lancamentos', 1)) if metas.get('meta_lancamentos', 0) else '-'}",
        )
    with row1[3]:
        _metric(
            "Clientes com venda",
            str(clientes_com_venda),
            f"Atingido vs meta: {_pct(clientes_com_venda / metas.get('meta_clientes', 1)) if metas.get('meta_clientes', 0) else '-'}",
        )

    row2 = st.columns(3)
    with row2[0]:
        _metric("Faturado do periodo", _money(faturado_periodo), "Base refletida pelas ultimas cargas")
    with row2[1]:
        _metric("Clientes com OL", str(clientes_com_ol), "Clientes com oportunidade no periodo")
    with row2[2]:
        _metric("Sem venda", str(clientes_sem_venda), "Clientes sem faturamento no periodo")

    st.markdown('<div class="section-title">Top clientes visitados</div>', unsafe_allow_html=True)
    top = base.copy()
    if "score_visita" in top.columns:
        top = top.sort_values(["score_visita", "ol_sem_combate", "total_faturado"], ascending=[False, False, False])
    else:
        top = top.sort_values(["ol_sem_combate", "total_faturado"], ascending=[False, False])
    top = top.head(6)
    cols = st.columns(2)
    for idx, (_, row) in enumerate(top.iterrows()):
        contato_nome = str(row.get("nome_contato", "") or row.get("nome_contato_cad", "") or "Sem comprador")
        contato_tel = str(row.get("contato", "") or row.get("contato_cad", "") or row.get("telefone_limpo", "") or row.get("telefone_limpo_cad", "") or "")
        mensagem = f"Ola, {contato_nome}. Estou acompanhando o cliente {row.get('nome_fantasia', '')} e posso ajudar no pedido."
        wa = _wa_link(contato_tel, mensagem)
        with cols[idx % 2]:
            with st.container(border=True):
                st.markdown(f"**{row.get('nome_fantasia', '')}**")
                st.caption(f"CNPJ: {row.get('cnpj', '')} | {row.get('cidade', '')}")
                st.markdown(f"**Comprador:** {contato_nome}")
                if wa:
                    st.markdown(f"**Telefone:** [{contato_tel}]({wa})")
                else:
                    st.markdown(f"**Telefone:** {contato_tel or '-'}")
                i3, i4, i5 = st.columns(3)
                with i3:
                    _compact_stat("OL", _money(row.get("ol_sem_combate", 0)))
                with i4:
                    _compact_stat("Prioritarios", _money(row.get("ol_prioritarios", 0)))
                with i5:
                    _compact_stat("Lancamentos", _money(row.get("ol_lancamentos", 0)))
                st.caption(str(row.get("motivo_principal", "") or "Cliente com oportunidade no periodo."))
                b1, b2 = st.columns(2)
                if b1.button(f"Montar pedido {idx + 1}", key=f"dash_pedido_{row.get('cnpj', idx)}", use_container_width=True):
                    st.session_state.pedido_cliente_cnpj = row.get("cnpj", "")
                    st.session_state.page = "Montar pedido"
                    st.rerun()
                if wa:
                    b2.link_button("WhatsApp", wa, use_container_width=True)
                else:
                    b2.button("WhatsApp", key=f"dash_wa_{idx}", disabled=True, use_container_width=True)

    with st.expander("Cadastrar metas do mes", expanded=False):
        m1, m2, m3, m4 = st.columns(4)
        meta_ol = m1.number_input("Meta OL sem combate", min_value=0.0, value=float(metas.get("meta_ol", 0.0)), step=100.0)
        meta_prio = m2.number_input("Meta OL prioritarios", min_value=0.0, value=float(metas.get("meta_prioritarios", 0.0)), step=100.0)
        meta_lanc = m3.number_input("Meta OL lancamentos", min_value=0.0, value=float(metas.get("meta_lancamentos", 0.0)), step=100.0)
        meta_cli = m4.number_input("Meta clientes com venda", min_value=0, value=int(metas.get("meta_clientes", 0)), step=1)
        if st.button("Salvar metas", use_container_width=True):
            _save_metas(
                {
                    "meta_ol": meta_ol,
                    "meta_prioritarios": meta_prio,
                    "meta_lancamentos": meta_lanc,
                    "meta_clientes": meta_cli,
                }
            )
            st.success("Metas salvas.")
