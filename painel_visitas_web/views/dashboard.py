from __future__ import annotations
import json
from datetime import datetime, date
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from config import DATA_DIR
from services.repo_state import load_status
from views.sip import build_sip_summary, load_sip_groups, selected_group

TZ_BR = ZoneInfo("America/Sao_Paulo")
META_FILE = DATA_DIR / 'metas_dashboard.json'


def _digits(v):
    return ''.join(ch for ch in str(v or '') if ch.isdigit())


def _wa_link(phone: str, msg: str):
    phone = _digits(phone)
    if not phone:
        return ''
    if not phone.startswith('55'):
        phone = '55' + phone
    return f"https://wa.me/{phone}?text={quote(msg)}"


def _cliente_info_map(clientes_df: pd.DataFrame | None):
    if clientes_df is None or clientes_df.empty:
        return {}
    cols = {c.lower(): c for c in clientes_df.columns}
    out = {}
    cnpj_col = cols.get('cnpj') or cols.get('cnpj_pdv')
    if not cnpj_col:
        return out
    for _, r in clientes_df.iterrows():
        cnpj = _digits(r.get(cnpj_col, ''))
        if not cnpj:
            continue
        out[cnpj] = {
            'nome_contato': r.get(cols.get('nome_contato', ''), '') if cols.get('nome_contato') else '',
            'contato': r.get(cols.get('contato', ''), '') if cols.get('contato') else '',
            'nome_fantasia': r.get(cols.get('nome fantasia', ''), '') if cols.get('nome fantasia') else '',
        }
    return out


def _money(v):
    try:
        return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return 'R$ 0,00'


def _pct(v):
    try:
        return f"{float(v)*100:.1f}%".replace('.', ',')
    except Exception:
        return '0,0%'


def _metric(label, value, help_text=''):
    st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>{label}</div><div class='metric-value'>{value}</div><div class='metric-help'>{help_text}</div></div>", unsafe_allow_html=True)


def _metric_compact(label, value, help_text=''):
    st.markdown(
        f"""
        <div style="
            border:1px solid rgba(15, 23, 42, 0.08);
            border-radius:16px;
            padding:8px 18px 7px 18px;
            background:#ffffff;
            min-height:66px;
            display:flex;
            flex-direction:column;
            justify-content:center;
            text-align:center;
            box-shadow:0 2px 8px rgba(15, 23, 42, 0.03);
        ">
            <div style="font-size:12px;font-weight:600;color:#5b6b82;margin-bottom:2px;line-height:1.1;">{label}</div>
            <div style="font-size:16px;font-weight:800;color:#003b5c;line-height:1.05;margin-bottom:1px;">{value}</div>
            <div style="font-size:11px;color:#7c8aa5;line-height:1.0;">{help_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _load_metas():
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'meta_ol': 0.0, 'meta_prioritarios': 0.0, 'meta_lancamentos': 0.0, 'meta_clientes': 0}


def _save_metas(data: dict):
    META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _parse_br_datetime(value: str | None) -> str:
    if not value:
        return 'â€”'
    txt = str(value).strip()
    if not txt:
        return 'â€”'
    try:
        dt = datetime.fromisoformat(txt.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_BR)
        else:
            dt = dt.astimezone(TZ_BR)
        return dt.strftime('%d/%m/%Y %H:%M:%S')
    except Exception:
        pass
    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M'):
        try:
            naive = datetime.strptime(txt, fmt)
            now_br = datetime.now(TZ_BR)
            local_assumed = naive.replace(tzinfo=TZ_BR)
            if local_assumed > now_br.replace(second=59, microsecond=999999) + pd.Timedelta(minutes=5):
                utc_assumed = naive.replace(tzinfo=ZoneInfo('UTC')).astimezone(TZ_BR)
                return utc_assumed.strftime('%d/%m/%Y %H:%M:%S')
            return local_assumed.strftime('%d/%m/%Y %H:%M:%S')
        except Exception:
            pass
    return txt


def _first_existing(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols_map = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_map:
            return cols_map[cand.lower()]
    return None


def _numeric_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    col = _first_existing(df, candidates)
    if col is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors='coerce').fillna(0.0)


def _build_period_sales(base_full: pd.DataFrame) -> tuple[dict[str, float], dict[str, bool]]:
    if base_full is None or base_full.empty:
        return {}, {}
    cnpj_col = _first_existing(base_full, ['cnpj', 'cnpj_pdv'])
    if cnpj_col is None:
        return {}, {}
    fat = _numeric_series(base_full, ['total_faturado', 'valor_faturado', 'faturado', 'total fat.', 'total_fat'])
    if fat.empty:
        fat = _numeric_series(base_full, ['total_solicitado', 'valor_solicitado'])
    aux = pd.DataFrame({'cnpj': base_full[cnpj_col].astype(str), 'faturado': fat})
    aux['cnpj'] = aux['cnpj'].map(_digits)
    aux = aux[aux['cnpj'] != '']
    grp = aux.groupby('cnpj', dropna=False)['faturado'].sum()
    venda = grp.to_dict()
    comprou = {k: v > 0 for k, v in venda.items()}
    return venda, comprou


def _status_card_value(bloco: dict, fallback_file: Path | None = None) -> tuple[str, str]:
    dt_txt = bloco.get('ultimo_sucesso') or bloco.get('atualizado_em') or ''
    status = bloco.get('status', 'â€”')
    if dt_txt:
        return _parse_br_datetime(dt_txt), status
    if fallback_file and fallback_file.exists():
        dt = datetime.fromtimestamp(fallback_file.stat().st_mtime, tz=TZ_BR)
        return dt.strftime('%d/%m/%Y %H:%M:%S'), status
    return 'â€”', status


def render_dashboard(score_df: pd.DataFrame, oportunidades: pd.DataFrame, foco: pd.DataFrame | None = None, inventario: pd.DataFrame | None = None, clientes_df: pd.DataFrame | None = None, base_full: pd.DataFrame | None = None, data_inicio=None, data_fim=None):
    st.markdown('<h2 class="page-title">Dashboard</h2>', unsafe_allow_html=True)
    if data_inicio is not None and data_fim is not None:
        ini_txt = data_inicio.strftime('%d/%m/%Y') if hasattr(data_inicio, 'strftime') else str(data_inicio)
        fim_txt = data_fim.strftime('%d/%m/%Y') if hasattr(data_fim, 'strftime') else str(data_fim)
        st.caption(f"PerÃ­odo aplicado: {ini_txt} atÃ© {fim_txt}")

    base = score_df.copy() if score_df is not None else pd.DataFrame()
    if base.empty:
        st.warning('Nenhum cliente encontrado para os filtros atuais.')
        return

    foco = foco.copy() if foco is not None else pd.DataFrame()
    info_map = _cliente_info_map(clientes_df)
    inventario = inventario.copy() if inventario is not None else pd.DataFrame()
    base_full = base_full.copy() if base_full is not None else pd.DataFrame()

    venda_periodo, comprou_periodo = _build_period_sales(base_full)
    base['cnpj_norm'] = base.get('cnpj', pd.Series(dtype=str)).astype(str).map(_digits)
    base['venda_periodo'] = base['cnpj_norm'].map(venda_periodo).fillna(0.0)
    base['comprou_periodo'] = base['cnpj_norm'].map(comprou_periodo).fillna(False)
    base['flag_sem_compra_periodo'] = ~base['comprou_periodo']

    foco_cards = pd.DataFrame()
    if not foco.empty and not inventario.empty:
        inv = inventario.copy()
        inv['preco_sem_imposto'] = pd.to_numeric(inv.get('preco_sem_imposto', 0), errors='coerce').fillna(0)
        inv['estoque'] = pd.to_numeric(inv.get('estoque', 0), errors='coerce').fillna(0)
        inv = inv[inv['estoque'] > 0].sort_values(['ean', 'preco_sem_imposto', 'estoque'], ascending=[True, True, False]).drop_duplicates('ean')
        foco_cards = foco[['ean', 'principio_ativo']].drop_duplicates().merge(inv[['ean', 'distribuidora', 'preco_sem_imposto', 'estoque']], on='ean', how='left')
        foco_cards = foco_cards.sort_values(['preco_sem_imposto', 'principio_ativo']).head(12)

    status_auto = load_status()
    total_ol = float(pd.to_numeric(base.get('ol_sem_combate', 0), errors='coerce').fillna(0).sum())
    total_combate = float(pd.to_numeric(base.get('ol_combate', 0), errors='coerce').fillna(0).sum())
    total_prio = float(pd.to_numeric(base.get('ol_prioritarios', 0), errors='coerce').fillna(0).sum())
    total_lanc = float(pd.to_numeric(base.get('ol_lancamentos', 0), errors='coerce').fillna(0).sum())
    perc_prio = (total_prio / total_ol) if total_ol else 0
    perc_lanc = (total_lanc / total_ol) if total_ol else 0
    com_venda_periodo = int(base['comprou_periodo'].sum())
    sem_venda_periodo = int((~base['comprou_periodo']).sum())
    metas = _load_metas()
    clientes_com_ol = int((pd.to_numeric(base.get('ol_sem_combate', 0), errors='coerce').fillna(0) > 0).sum())
    foco_disponivel = int(foco['ean'].astype(str).nunique()) if not foco.empty and 'ean' in foco.columns else 0

    st.markdown('<div class="section-title">Atalhos do dia</div>', unsafe_allow_html=True)
    qa1, qa2, qa3, qa4 = st.columns(4)
    qa1.metric('Sem compra', str(sem_venda_periodo))
    qa2.metric('Clientes com OL', str(clientes_com_ol))
    qa3.metric('Produtos foco', str(foco_disponivel))
    qa4.metric('Clientes com venda', str(com_venda_periodo))
    qb1, qb2, qb3 = st.columns(3)
    if qb1.button('Abrir clientes', use_container_width=True, key='dash_open_clientes'):
        st.session_state.page = 'Clientes'
        st.rerun()
    if qb2.button('Montar pedido', use_container_width=True, key='dash_open_pedido'):
        st.session_state.page = 'Montar pedido'
        st.rerun()
    if qb3.button('Abrir carrinho', use_container_width=True, key='dash_open_cart'):
        st.session_state.page = 'Carrinho'
        st.rerun()

    with st.expander('Cadastrar metas do mÃªs', expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        meta_ol = c1.number_input('Meta OL sem combate', min_value=0.0, value=float(metas.get('meta_ol', 0.0)), step=100.0)
        meta_prio = c2.number_input('Meta OL prioritÃ¡rios', min_value=0.0, value=float(metas.get('meta_prioritarios', 0.0)), step=100.0)
        meta_lanc = c3.number_input('Meta OL lanÃ§amentos', min_value=0.0, value=float(metas.get('meta_lancamentos', 0.0)), step=100.0)
        meta_cli = c4.number_input('Meta clientes com venda', min_value=0, value=int(metas.get('meta_clientes', 0)), step=1)
        if st.button('Salvar metas'):
            _save_metas({'meta_ol': meta_ol, 'meta_prioritarios': meta_prio, 'meta_lancamentos': meta_lanc, 'meta_clientes': meta_cli})
            st.success('Metas salvas.')

    bussola_dt, bussola_status = _status_card_value(status_auto.get('bussola', {}), DATA_DIR / 'Pedidos.xlsx')
    mf_dt, mf_status = _status_card_value(status_auto.get('mercadofarma', {}), None)

    st.markdown('<div class="section-title">Ãšltimas atualizaÃ§Ãµes</div>', unsafe_allow_html=True)
    spacer_left, ua1, ua2, spacer_right = st.columns([0.6, 1, 1, 0.6])
    with ua1:
        _metric_compact('BÃºssola', bussola_dt, bussola_status)
    with ua2:
        _metric_compact('Mercado Farma', mf_dt, mf_status)

    st.markdown('<div class="section-title">Indicadores do perÃ­odo</div>', unsafe_allow_html=True)
    cols = st.columns(4)
    with cols[0]:
        _metric('OL sem combate', _money(total_ol), f"Combate: {_money(total_combate)} â€¢ Meta: {_pct(total_ol / metas.get('meta_ol',1)) if metas.get('meta_ol',0) else 'â€”'}")
    with cols[1]:
        _metric('OL PrioritÃ¡rios', _money(total_prio), f"{_pct(perc_prio)} do OL â€¢ Meta: {_pct(total_prio / metas.get('meta_prioritarios',1)) if metas.get('meta_prioritarios',0) else 'â€”'}")
    with cols[2]:
        _metric('OL LanÃ§amentos', _money(total_lanc), f"{_pct(perc_lanc)} do OL â€¢ Meta: {_pct(total_lanc / metas.get('meta_lancamentos',1)) if metas.get('meta_lancamentos',0) else 'â€”'}")
    with cols[3]:
        _metric('Clientes com venda no perÃ­odo', str(com_venda_periodo), f"Atingido vs meta: {_pct(com_venda_periodo / metas.get('meta_clientes',1)) if metas.get('meta_clientes',0) else 'â€”'} â€¢ Sem venda: {sem_venda_periodo}")

    sip = selected_group()
    if sip:
        cnpjs_sip = {str(x) for x in sip.get('cnpjs', [])}
        sip_base = base[base['cnpj'].astype(str).isin(cnpjs_sip)].copy()
        faturado = float(pd.to_numeric(sip_base.get('venda_periodo', 0), errors='coerce').fillna(0).sum()) if not sip_base.empty else 0.0
        meta = float(sip.get('meta_mes', 0) or 0)
        perc = (faturado / meta) if meta else 0.0
        st.markdown(f"<div class='section-title' style='margin-top:8px'>{sip.get('nome','Grupo SIP')}</div>", unsafe_allow_html=True)
        cols_sip = st.columns(6)
        with cols_sip[0]: _metric('Qtde CNPJs', str(len(sip.get('cnpjs', []))), f"Pagamento a partir de {sip.get('pagamento_percentual',80):.0f}%")
        with cols_sip[1]: _metric('Qtde faturado', _money(faturado), f"% faturado: {_pct(perc)}")
        with cols_sip[2]: _metric('Falta para 80%', _money(max(0, meta*0.8 - faturado)), '')
        with cols_sip[3]: _metric('Falta para 90%', _money(max(0, meta*0.9 - faturado)), '')
        with cols_sip[4]: _metric('Falta para 100%', _money(max(0, meta - faturado)), '')
        with cols_sip[5]: _metric('Pagamento', f"{sip.get('pagamento_percentual',80):.0f}%", 'Regra da SIP')
    else:
        cols2 = st.columns(4)
        teve_prio = pd.to_numeric(base.get('ol_prioritarios', 0), errors='coerce').fillna(0) > 0
        teve_lanc = pd.to_numeric(base.get('ol_lancamentos', 0), errors='coerce').fillna(0) > 0
        with cols2[0]: _metric('Sem prioritÃ¡rio', str(int((~teve_prio).sum())), 'Clientes do filtro atual')
        with cols2[1]: _metric('Sem lanÃ§amentos', str(int((~teve_lanc).sum())), 'Clientes do filtro atual')
        with cols2[2]: _metric('Grupos SIP', str(len(load_sip_groups())), 'Cadastre no menu SIP')
        with cols2[3]: _metric('SIP selecionado', 'â€”', 'Nenhum grupo ativo')

    resumo_sip = build_sip_summary(base)
    if not resumo_sip.empty:
        sip_show = resumo_sip.copy()
        sip_show['Faturado'] = sip_show['Faturado'].map(_money)
        sip_show['Meta'] = sip_show['Meta'].map(_money)
        sip_show['Atingimento'] = sip_show['Atingimento'].map(_pct)
        sip_show['Falta regra'] = sip_show['Falta regra'].map(_money)
        sip_show['Pagamento'] = sip_show['Pagamento'].map(lambda v: f"{int(v)}%")
        st.markdown('<div class="section-title">Resumo das SIPs</div>', unsafe_allow_html=True)
        st.dataframe(sip_show[['SIP', 'CNPJs', 'Faturado', 'Meta', 'Atingimento', 'Falta regra', 'Pagamento']], use_container_width=True, hide_index=True)

    if not foco_cards.empty:
        st.markdown('<div class="section-title">Produtos foco em evidÃªncia</div>', unsafe_allow_html=True)
        for _, row in foco_cards.iterrows():
            c1, c2 = st.columns([5, 1])
            with c1:
                st.markdown(f"<div class='mini-alert-card'><div><b>{row.get('principio_ativo','')}</b></div><div style='font-size:.85rem;color:#5D7485'>EAN: {row.get('ean','')} â€¢ {row.get('distribuidora','')} â€¢ {_money(row.get('preco_sem_imposto',0))}</div></div>", unsafe_allow_html=True)
            with c2:
                if st.button('Carrinho', key=f"dash_foco_{row.get('ean','')}", use_container_width=True):
                    st.session_state.preselected_products = st.session_state.get('preselected_products', {})
                    st.session_state.preselected_products[str(row.get('ean',''))] = 1
                    st.session_state.page = 'Montar pedido'
                    st.rerun()

    st.markdown('<div class="section-title">Top visitas do perÃ­odo</div>', unsafe_allow_html=True)
    score_col = 'score_visita' if 'score_visita' in base.columns else None
    sort_cols = ['flag_sem_compra_periodo', 'venda_periodo', 'ol_sem_combate'] + ([score_col] if score_col else [])
    sort_asc = [False, True, True] + ([False] if score_col else [])
    top = base.sort_values(sort_cols, ascending=sort_asc).head(6)
    cols_vis = st.columns(2)
    for i, (_, row) in enumerate(top.iterrows()):
        with cols_vis[i % 2]:
            comprou_foco = 'Sim' if bool(row.get('comprou_foco_semana', False)) else 'NÃ£o'
            prioridade = 'Alta' if (not row['comprou_periodo'] or row.get('venda_periodo', 0) < 300) else ('MÃ©dia' if i < 4 else 'Monitorar')
            st.markdown(
                f"<div class='visit-card'><div class='visit-head'><div><div class='visit-name'>{row['nome_fantasia']}</div><div class='visit-sub'>CNPJ: {row['cnpj']} â€¢ {row.get('cidade','')}</div></div><div class='priority-badge'>{prioridade}</div></div>"
                f"<div class='reason-box'>{row.get('motivo_principal', 'Visita priorizada pelo painel')}</div>"
                f"<div class='visit-grid'><div><span>Venda no perÃ­odo</span><b>{'Sim' if row['comprou_periodo'] else 'NÃ£o'}</b></div><div><span>Produto da semana</span><b>{comprou_foco}</b></div>"
                f"<div><span>OL sem combate</span><b>{_money(row.get('ol_sem_combate', 0))}</b></div><div><span>OL prioritÃ¡rios</span><b>{_money(row.get('ol_prioritarios', 0))}</b></div>"
                f"<div><span>OL lanÃ§amentos</span><b>{_money(row.get('ol_lancamentos', 0))}</b></div><div><span>Resultado no perÃ­odo</span><b>{_money(row.get('venda_periodo', 0))}</b></div></div></div>",
                unsafe_allow_html=True
            )
            if st.button(f"Tirar pedido â€¢ {row['nome_fantasia']}", key=f"go_pedido_{row['cnpj']}", use_container_width=True):
                st.session_state.pedido_cliente_cnpj = row['cnpj']
                st.session_state.page = 'Montar pedido'
                st.rerun()

    st.markdown('<div class="section-title">Clientes sem compra ou abaixo de R$ 300,00 no perÃ­odo</div>', unsafe_allow_html=True)

    if 'comprou_periodo' not in base.columns:
        base['comprou_periodo'] = False
    if 'venda_periodo' not in base.columns:
        base['venda_periodo'] = 0.0

    base['comprou_periodo'] = base['comprou_periodo'].fillna(False).astype(bool)
    base['venda_periodo'] = pd.to_numeric(base['venda_periodo'], errors='coerce').fillna(0.0)

    alertas = base[(~base['comprou_periodo']) | (base['venda_periodo'] < 300)].copy()

    if alertas.empty:
        st.info('Nenhum cliente nessa condiÃ§Ã£o no perÃ­odo.')
    else:
        sem_compra = alertas[~alertas['comprou_periodo']].copy()
        if not sem_compra.empty:
            rows_exp = []
            for _, row in sem_compra.iterrows():
                inf = info_map.get(_digits(row.get('cnpj', '')), {})
                rows_exp.append({
                    'CNPJ': _digits(row.get('cnpj', '')),
                    'ResponsÃ¡vel': inf.get('nome_contato', ''),
                    'Contato': inf.get('contato', ''),
                })
            exp_df = pd.DataFrame(rows_exp).drop_duplicates()
            st.download_button(
                'Exportar contatos sem compras',
                exp_df.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig'),
                file_name='clientes_sem_compras.csv',
                mime='text/csv',
                use_container_width=True,
            )

        for _, row in alertas.sort_values(['comprou_periodo', 'venda_periodo'], ascending=[True, True]).iterrows():
            inf = info_map.get(_digits(row.get('cnpj', '')), {})
            nome_fantasia = row.get('nome_fantasia', '') or 'Cliente'
            cnpj_txt = row.get('cnpj', '')
            cidade_txt = row.get('cidade', '')
            problema = 'Sem compras no perÃ­odo' if not bool(row.get('comprou_periodo', False)) else 'Compras abaixo de R$ 300,00 no perÃ­odo'
            nome = (inf.get('nome_contato') or nome_fantasia or 'cliente').strip()
            mensagem = f"Bom dia, {nome}. Vi aqui uma oportunidade no CNPJ {cnpj_txt}: {problema}. Posso te ajudar a montar um pedido agora?"
            wa = _wa_link(inf.get('contato', ''), mensagem)

            c1, c2, c3 = st.columns([7, 1.2, 1.2])
            with c1:
                st.markdown(
                    f"<div class='mini-alert-card'><b>{nome_fantasia}</b> â€¢ CNPJ: {cnpj_txt} â€¢ {cidade_txt} â€¢ Resultado: {_money(row.get('venda_periodo', 0))}</div>",
                    unsafe_allow_html=True,
                )
            with c2:
                if st.button('Abrir pedido', key=f"alerta_pedido_{cnpj_txt}", use_container_width=True):
                    st.session_state.pedido_cliente_cnpj = cnpj_txt
                    st.session_state.page = 'Montar pedido'
                    st.rerun()
            with c3:
                if wa:
                    st.link_button('WhatsApp', wa, use_container_width=True)
                else:
                    st.button('WhatsApp', key=f"no_wa_{cnpj_txt}", disabled=True, use_container_width=True)
