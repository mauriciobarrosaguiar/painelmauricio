from __future__ import annotations
import json
import pandas as pd
import streamlit as st
from config import DATA_DIR

SIP_FILE = DATA_DIR / 'sip_grupos.json'

def _money(v):
    try:
        return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return 'R$ 0,00'

def _pct(v):
    try:
        return f"{float(v)*100:.2f}%".replace('.', ',')
    except Exception:
        return '0,00%'

def load_sip_groups() -> list[dict]:
    if SIP_FILE.exists():
        try:
            return json.loads(SIP_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []

def save_sip_groups(groups: list[dict]):
    SIP_FILE.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding='utf-8')

def selected_group() -> dict | None:
    groups = load_sip_groups()
    gid = st.session_state.get('sip_selected_id')
    for g in groups:
        if g.get('id') == gid:
            return g
    return groups[0] if groups else None

def render_sip(score_df: pd.DataFrame, clientes_df: pd.DataFrame):
    st.markdown('<h2 class="page-title">SIP / Redes</h2>', unsafe_allow_html=True)
    groups = load_sip_groups()
    cli_ref = clientes_df[['cnpj','nome_fantasia']].drop_duplicates().sort_values('nome_fantasia').copy()
    cli_ref['label'] = cli_ref['nome_fantasia'].astype(str) + ' — ' + cli_ref['cnpj'].astype(str)
    label_to_cnpj = dict(zip(cli_ref['label'], cli_ref['cnpj']))

    current = selected_group()
    nomes = ['Novo grupo'] + [g['nome'] for g in groups]
    idx = nomes.index(current['nome']) if current and current['nome'] in nomes else 0
    escolha = st.selectbox('Grupo SIP para cadastrar/editar', nomes, index=idx)
    editing = next((g for g in groups if g['nome'] == escolha), None) if escolha != 'Novo grupo' else None

    st.markdown('<div class="section-title">Cadastrar ou editar grupo econômico</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1.8, 1.0])
    nome = c1.text_input('Nome do grupo econômico', value=editing.get('nome','') if editing else '')
    meta = c2.number_input('Meta do mês', min_value=0.0, step=100.0, value=float(editing.get('meta_mes', 0.0)) if editing else 0.0)
    membros_default = [lbl for lbl, cnpj in label_to_cnpj.items() if editing and str(cnpj) in [str(x) for x in editing.get('cnpjs',[])]]
    membros = st.multiselect('CNPJs da rede', cli_ref['label'].tolist(), default=membros_default, placeholder='Escolha as opções')
    pagamento = st.number_input('Pagamento a partir de (%)', min_value=0.0, max_value=100.0, value=float(editing.get('pagamento_percentual',80.0)) if editing else 80.0, step=1.0)

    csave, cdel = st.columns(2)
    if csave.button('Salvar grupo SIP', use_container_width=True, disabled=not nome or not membros):
        gid = (editing.get('id') if editing else nome.strip().lower().replace(' ','_'))
        novo = {'id': gid, 'nome': nome.strip(), 'meta_mes': float(meta), 'pagamento_percentual': float(pagamento), 'cnpjs': [str(label_to_cnpj[m]) for m in membros]}
        groups = [g for g in groups if g.get('id') != gid] + [novo]
        save_sip_groups(groups)
        st.session_state['sip_selected_id'] = gid
        st.success('Grupo SIP salvo.')
        st.rerun()

    if editing and cdel.button('Excluir grupo', use_container_width=True):
        groups = [g for g in groups if g.get('id') != editing.get('id')]
        save_sip_groups(groups)
        st.session_state['sip_selected_id'] = None
        st.success('Grupo removido.')
        st.rerun()

    groups = load_sip_groups()
    if not groups:
        st.info('Nenhum grupo SIP cadastrado.')
        return

    labels = [g['nome'] for g in groups]
    current = selected_group() or groups[0]
    idx = labels.index(current['nome']) if current['nome'] in labels else 0
    chosen = st.selectbox('Grupo SIP selecionado para análise', labels, index=idx)
    group = next(g for g in groups if g['nome'] == chosen)
    st.session_state['sip_selected_id'] = group['id']

    base = score_df[score_df['cnpj'].astype(str).isin([str(x) for x in group.get('cnpjs',[])])].copy()
    faturado = float(base['total_faturado'].sum()) if not base.empty else 0.0
    perc = (faturado / group.get('meta_mes',0)) if group.get('meta_mes',0) else 0.0

    m1,m2,m3,m4 = st.columns(4)
    for col, lab, val in [(m1,'Qtde CNPJs', str(len(group.get('cnpjs',[])))), (m2,'Meta mês', _money(group.get('meta_mes',0))), (m3,'Qtde faturado', _money(faturado)), (m4,'% faturado', _pct(perc))]:
        col.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>{lab}</div><div class='metric-value'>{val}</div></div>", unsafe_allow_html=True)

    st.caption(f"Pagamento a partir de {group.get('pagamento_percentual',80):.0f}%")
    if not base.empty:
        show = base[['nome_fantasia','cnpj','cidade','total_faturado','ol_prioritarios','ol_lancamentos']].copy()
        show.columns = ['Cliente','CNPJ','Cidade','Faturado','Prioritários','Lançamentos']
        for c in ['Faturado','Prioritários','Lançamentos']:
            show[c] = show[c].map(_money)
        st.dataframe(show, use_container_width=True, hide_index=True)
