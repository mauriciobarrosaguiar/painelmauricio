from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from services.loaders import load_pedidos, load_produtos, load_clientes, load_foco_semana, load_inventario
from services.cleaning import clean_pedidos, clean_produtos, clean_clientes, clean_foco_semana, clean_inventario
from services.analytics import enrich_pedidos, build_cliente_resumo, build_gap_por_cliente, build_oportunidades_cliente, build_cancelados_cliente
from services.scoring import score_clientes
from views.dashboard import render_dashboard
from views.clientes import render_clientes
from views.importacao import render_importacao
from views.pedido import render_pedido
from views.busca_inteligente import render_busca_inteligente
from views.cart import render_cart
from views.sip import render_sip
from config import COR_BORDA, COR_PRIMARIA, COR_TEXTO
from services.repo_state import load_user_config, save_user_config, load_status

TZ_BR = ZoneInfo("America/Sao_Paulo")

def agora_br() -> datetime:
    return datetime.now(TZ_BR)

def primeiro_dia_mes_atual() -> date:
    hoje = agora_br().date()
    return hoje.replace(day=1)

def ultimo_dia_mes_atual() -> date:
    hoje = agora_br().date()
    if hoje.month == 12:
        prox = hoje.replace(year=hoje.year + 1, month=1, day=1)
    else:
        prox = hoje.replace(month=hoje.month + 1, day=1)
    return prox - timedelta(days=1)

st.set_page_config(page_title="Painel de Visitas - Mauricio", page_icon="📊", layout="wide", initial_sidebar_state="collapsed")

st.markdown(f"""
<style>
[data-testid="collapsedControl"] {{display:none !important;}}
section[data-testid="stSidebarNav"] {{display:none !important;}}
header[data-testid="stHeader"] {{background: transparent;}}
#MainMenu {{visibility:hidden;}}
footer {{visibility:hidden;}}
.stApp {{background: #F6FAFC; color:{COR_TEXTO};}}
.block-container {{padding-top: .7rem; padding-bottom: 1rem; max-width: 1500px;}}
html, body, .stApp, .stApp * {{font-family:"Segoe UI", Arial, sans-serif !important;}}

[data-testid="stDataFrame"] div[role="grid"] {{font-size: 12px !important;}}
[data-testid="stDataEditor"] div[role="grid"] {{font-size: 12px !important;}}
[data-testid="stDataFrame"] [data-testid="StyledFullScreenButton"] {{display:none !important;}}
[data-testid="stDataEditor"] [data-testid="StyledFullScreenButton"] {{display:none !important;}}
.main ::-webkit-scrollbar, [data-testid="stSidebar"] ::-webkit-scrollbar {{width: 22px; height: 22px;}}
.main ::-webkit-scrollbar-thumb, [data-testid="stSidebar"] ::-webkit-scrollbar-thumb {{background:#0B6C86; border-radius: 10px; border: 3px solid #EAF9FD;}}
.main ::-webkit-scrollbar-track, [data-testid="stSidebar"] ::-webkit-scrollbar-track {{background:#DCECF1; border-radius: 10px;}}
[data-testid="stSidebar"] {{background: linear-gradient(180deg, #02161D 0%, #05303B 45%, #0B6C86 100%); border-right:none; min-width: 300px; max-width:300px;}}
[data-testid="stSidebar"] * {{color: white;}}
.sidebar-title {{font-size:1.85rem; font-weight:900; margin-bottom:.85rem;}}
.sidebar-section {{font-size:.8rem; font-weight:900; text-transform:uppercase; letter-spacing:.06em; color:#D2F0F7; margin:1rem 0 .5rem 0;}}
.stRadio > label, .stMultiSelect > label, .stSelectbox > label, .stDateInput > label {{font-weight:800 !important; color:#EAF9FD !important;}}
[data-testid="stSidebar"] .stButton > button {{width:100%; min-height:48px; border-radius:14px !important; font-weight:900; border:1px solid rgba(255,255,255,.14) !important; background: rgba(255,255,255,.08) !important; color:#fff !important; margin:0 0 .5rem 0 !important;}}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {{background: linear-gradient(135deg, #0C8CB2 0%, #1DA9C8 100%) !important; box-shadow:0 10px 20px rgba(0,0,0,.12) !important;}}
[data-testid="stSidebar"] .stButton > button p {{color:#fff !important; font-size:1rem !important;}}
[data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea {{color:#0f172a !important; background:#ffffff !important;}}
[data-testid="stSidebar"] [data-baseweb="select"] div {{color:#0f172a !important;}}
[data-testid="stSidebar"] .stMultiSelect div[data-baseweb="tag"] span {{color:#0f172a !important;}}
.hero {{padding: 18px 22px; border-radius: 22px; background: linear-gradient(135deg, #056A83 0%, #0C8CB2 100%); color: white; box-shadow: 0 14px 30px rgba(0,74,97,.10); margin-bottom: 14px;}}
.hero h1 {{font-size: 1.95rem; margin:0 0 6px 0; text-align:center;}}
.hero p {{margin:0; opacity:.96; text-align:center;}}
.metric-card {{background:#fff; border:1px solid {COR_BORDA}; border-radius:18px; padding:14px 12px; box-shadow:0 8px 18px rgba(15,23,42,.03); min-height:108px;}}
.metric-center {{display:flex; flex-direction:column; justify-content:center; align-items:center; text-align:center;}}
.metric-label {{font-size:.84rem; color:#587186; font-weight:800;}}
.metric-value {{font-size:1.34rem; color:{COR_PRIMARIA}; font-weight:900; margin-top:4px;}}
.metric-help {{font-size:.78rem; color:#75879A; margin-top:4px;}}
.page-title {{margin:.15rem 0 .9rem 0; color:{COR_TEXTO}; font-size:1.9rem; font-weight:900; text-align:center;}}

div[data-testid="stDataFrame"] [role="row"] > div, div[data-testid="stDataEditor"] [role="row"] > div {{box-shadow: inset 0 -1px 0 rgba(8,53,69,.06);}}
div[data-testid="stDataFrame"] [role="columnheader"] > div, div[data-testid="stDataEditor"] [role="columnheader"] > div {{font-weight:800 !important; box-shadow: inset 0 -2px 0 rgba(8,53,69,.10);}}
.section-title {{text-align:center; color:{COR_TEXTO}; font-size:1.2rem; font-weight:900; margin:.25rem 0 .8rem 0;}}
.visit-card,.detail-card {{background:#fff; border:1px solid {COR_BORDA}; border-radius:20px; padding:16px; box-shadow:0 8px 18px rgba(15,23,42,.03); margin-bottom:12px;}}
.visit-head {{display:flex; justify-content:space-between; gap:10px; align-items:flex-start; flex-wrap:wrap;}}
.visit-name,.detail-title {{font-size:1.05rem; font-weight:900; color:#0f172a; text-align:center;}}
.visit-sub,.detail-sub {{font-size:.9rem; color:#5B6B7E; margin-top:4px; text-align:center;}}
.priority-badge {{background:linear-gradient(135deg, #DFF7EE 0%, #B8F0D7 100%); color:#0E7C53; border-radius:14px; padding:8px 12px; font-weight:800;}}
.reason-box {{margin-top:12px; padding:10px 12px; border-radius:14px; background:#F6FBFD; color:#334155; border:1px solid #D7EEF6; text-align:center;}}
.visit-grid, .detail-grid {{display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:12px;}}
.visit-grid div, .detail-grid div {{background:#FBFDFF; border:1px solid #EDF2F7; border-radius:14px; padding:10px; text-align:center;}}
.visit-grid span, .detail-grid span {{display:block; font-size:.78rem; color:#64748b; margin-bottom:4px;}}
.detail-grid {{grid-template-columns:repeat(3,minmax(0,1fr));}}
.filter-panel {{background:#ffffff; border:1px solid {COR_BORDA}; border-radius:18px; padding:14px 16px; box-shadow:0 8px 18px rgba(15,23,42,.03); margin-bottom:12px;}}
.product-card {{background:#ffffff; border:1px solid {COR_BORDA}; border-radius:20px; padding:16px; box-shadow:0 8px 18px rgba(15,23,42,.03); margin-bottom:14px;}}
.product-card h4 {{margin:0 0 8px 0; color:#0B2E43; font-size:1rem; line-height:1.35;}}
.product-card small {{color:#63758A;}}
.product-badge {{display:inline-block; padding:4px 10px; border-radius:999px; font-size:.72rem; font-weight:900; letter-spacing:.03em; background:#E8F7EF; color:#0D5E3A; margin-bottom:10px;}}
.product-badge.neutro {{background:#EDF4F7; color:#41596B;}}
.product-badge.info {{background:#EAF4FB; color:#123E63;}}
.inventory-pill {{display:inline-block; padding:6px 10px; border-radius:12px; background:#F3F8FB; color:#335266; font-size:.78rem; font-weight:700; margin:4px 6px 0 0;}}
.hero-search {{padding:24px 26px; border-radius:28px; background:linear-gradient(135deg, #0D3B2A 0%, #1B6B4A 55%, #C9A63B 100%); color:#FFFFFF; box-shadow:0 18px 34px rgba(13,59,42,.18); margin-bottom:16px;}}
.hero-search h1 {{margin:0 0 8px 0; font-size:2.4rem; line-height:1.05;}}
.hero-search p {{margin:0; font-size:1.02rem; opacity:.96;}}
div[data-testid="stDataFrame"] {{border-radius:18px; overflow:hidden; border:1px solid {COR_BORDA};}}
div[data-baseweb="select"] > div, div[data-baseweb="input"] > div {{border-radius:12px !important;}}
.stButton > button, .stDownloadButton > button {{border-radius:12px !important; min-height:42px; font-weight:800; border:1px solid {COR_BORDA} !important; background:#EAF4F8 !important; color:#0A2533 !important;}}
::-webkit-scrollbar {{width:22px; height:22px;}}
::-webkit-scrollbar-thumb {{background:#0C8CB2; border-radius:12px; border:3px solid #EAF4F8;}}
::-webkit-scrollbar-track {{background:#EAF4F8;}}
[data-testid="stSidebar"] ::-webkit-scrollbar {{width:22px; height:22px;}}
[data-testid="stSidebar"] ::-webkit-scrollbar-thumb {{background:#38B8D6; border-radius:12px; border:3px solid rgba(255,255,255,.2);}}
[data-testid="stSidebar"] ::-webkit-scrollbar-track {{background:rgba(255,255,255,.12);}}
.stTextInput input, .stNumberInput input, .stTextArea textarea, .stDateInput input {{background:#E7F2F8 !important; color:#102A3A !important; border:1px solid #6F8D9B !important;}}
div[data-baseweb="select"] > div {{background:#E7F2F8 !important; border:1px solid #6F8D9B !important; color:#102A3A !important;}}
.stMultiSelect div[data-baseweb="select"] > div {{background:#E7F2F8 !important; border:1px solid #6F8D9B !important; color:#102A3A !important;}}
.stTextInput input::placeholder, .stTextArea textarea::placeholder {{color:#5B6B7E !important; opacity:1;}}
@media (max-width: 900px) {{ .visit-grid, .detail-grid {{grid-template-columns:1fr;}} }}
@media (max-width: 900px) {{
  [data-testid="collapsedControl"] {{display:flex !important; position:fixed; left:8px; top:64px; z-index:1003; background:#0C8CB2; border-radius:12px; padding:8px; box-shadow:0 8px 18px rgba(0,0,0,.22);}}
  [data-testid="stSidebar"] {{margin-left:-100vw !important; min-width:0 !important; max-width:0 !important; width:0 !important; opacity:0; overflow:hidden; transition:all .25s ease;}}
  [data-testid="stSidebar"][aria-expanded="true"] {{margin-left:0 !important; min-width:88vw !important; max-width:88vw !important; width:88vw !important; opacity:1; overflow:auto; box-shadow:0 20px 40px rgba(0,0,0,.28);}}
  .block-container {{padding-left: .6rem !important; padding-right: .6rem !important;}}
  .sidebar-title,.sidebar-section {{display:block;}}
}}

.mini-alert-card {{background:#FFFFFF; border:1px solid #D8E8EE; border-radius:12px; padding:.7rem .9rem; margin:.2rem 0; box-shadow:0 2px 10px rgba(8,53,69,.05);}} 

</style>
""", unsafe_allow_html=True)
st.markdown("""
<style>
.notice-card {border-radius:16px; padding:.95rem 1rem; margin:.25rem 0 .7rem 0; border:1px solid #CFE1EA; box-shadow:0 3px 12px rgba(10,37,51,.05); font-weight:700; line-height:1.45;}
.notice-card strong {display:block; margin-bottom:.2rem;}
.notice-success {background:#E8F7EF; color:#0D5E3A; border-color:#A8DABD;}
.notice-info {background:#EAF4FB; color:#123E63; border-color:#AFCDE5;}
.notice-warning {background:#FFF4D8; color:#6A4A00; border-color:#E8C66A;}
.notice-error {background:#FDECEC; color:#8A1C1C; border-color:#EDB4B4;}
.status-mini-card, .base-mini-card, .run-mini-card {background:#FFFFFF; border:1px solid #D8E8EE; border-radius:16px; padding:.85rem 1rem; box-shadow:0 2px 10px rgba(8,53,69,.05); margin-bottom:.7rem;}
.status-mini-title, .base-mini-title, .run-mini-title {font-size:.82rem; font-weight:900; color:#506B80; text-transform:uppercase; letter-spacing:.04em; margin-bottom:.2rem;}
.status-mini-main, .base-mini-main {font-size:1rem; font-weight:900; color:#0B2E43; line-height:1.25; word-break:break-word; overflow-wrap:anywhere;}
.status-mini-sub, .base-mini-sub, .run-mini-sub {font-size:.84rem; color:#61758A; margin-top:.15rem;}
.status-badge {display:inline-block; margin-top:.45rem; padding:.22rem .55rem; border-radius:999px; font-size:.74rem; font-weight:900;}
.status-ok {background:#E8F7EF; color:#0D5E3A;}
.status-falha {background:#FDECEC; color:#8A1C1C;}
.status-pendente {background:#FFF4D8; color:#6A4A00;}
.status-neutro {background:#EDF4F7; color:#41596B;}
[data-testid="stFileUploader"] {background:#FFFFFF; border:1px solid #D8E8EE; border-radius:18px; padding:.55rem .6rem; box-shadow:0 2px 10px rgba(8,53,69,.05);}
[data-testid="stFileUploader"] section {background:#F7FBFD !important; border:2px dashed #8CB8C7 !important; border-radius:16px !important; padding:1rem !important;}
[data-testid="stFileUploader"] section * {color:#123042 !important;}
[data-testid="stFileUploader"] small {color:#506B80 !important;}
[data-testid="stFileUploader"] button {background:#EAF4F8 !important; color:#0A2533 !important; border:1px solid #B7D2DE !important;}
[data-testid="stFileUploader"] button:hover {background:#DCECF4 !important; color:#0A2533 !important;}
[data-testid="stAlert"] {border-radius:14px !important;}
[data-testid="stAlert"] * {color:#102A3A !important;}
@media (max-width: 900px) {
  [data-testid="collapsedControl"] {display:flex !important; align-items:center; justify-content:center; position:fixed; left:10px; top:70px; z-index:1003; width:46px; height:46px; background:#0C8CB2 !important; border:2px solid rgba(255,255,255,.92); border-radius:14px; box-shadow:0 10px 22px rgba(0,0,0,.28);}
  [data-testid="collapsedControl"]:hover {background:#095F79 !important;}
  [data-testid="collapsedControl"] button {background:transparent !important; border:none !important; box-shadow:none !important; min-height:auto !important; padding:0 !important; margin:0 !important;}
  [data-testid="collapsedControl"] svg {width:28px !important; height:28px !important; stroke:#FFFFFF !important; fill:none !important; stroke-width:2.6 !important;}
  [data-testid="collapsedControl"] path {stroke:#FFFFFF !important; fill:none !important;}
  .notice-card {padding:.85rem .9rem; font-size:.95rem;}
  .status-mini-card, .base-mini-card, .run-mini-card {border-radius:14px; padding:.8rem .85rem; margin-bottom:.6rem;}
  .status-mini-main, .base-mini-main {font-size:.98rem;}
  [data-testid="stFileUploader"] {padding:.45rem .45rem; border-radius:16px;}
  [data-testid="stFileUploader"] section {padding:.9rem !important; min-height:140px !important;}
}
</style>
""", unsafe_allow_html=True)

@st.cache_data(show_spinner=False)
def get_clean_bases(data_version_key: str = ''):
    pedidos = clean_pedidos(load_pedidos())
    produtos = clean_produtos(load_produtos())
    clientes = clean_clientes(load_clientes())
    foco = clean_foco_semana(load_foco_semana())
    inventario = clean_inventario(load_inventario())
    if not inventario.empty:
        inventario = inventario.merge(produtos[['ean', 'mix_lancamentos']].drop_duplicates(), on='ean', how='left', suffixes=('', '_prod'))
        inventario['mix_lancamentos'] = inventario['mix_lancamentos'].replace('', pd.NA).fillna(inventario.get('mix_lancamentos_prod'))
        inventario = inventario.drop(columns=[c for c in inventario.columns if c.endswith('_prod')], errors='ignore')
    base_full = enrich_pedidos(pedidos, produtos, clientes)
    return pedidos, produtos, clientes, foco, inventario, base_full

@st.cache_data(show_spinner=False)
def compute_views(data_version_key: str, foco_manual_key: tuple[str, ...], cidade_key: str, preferencias_key: tuple[tuple[str, str], ...], data_inicio=None, data_fim=None, distrib_visiveis: tuple[str, ...] = (), descontos_adic: tuple[tuple[str, float], ...] = (), descontos_exc: tuple[tuple[str, str], ...] = ()): 
    pedidos, produtos, clientes, foco, inventario, base_full = get_clean_bases(data_version_key)
    foco = foco.copy()
    if foco_manual_key:
        foco_manual = produtos[produtos['principio_ativo'].isin(foco_manual_key)].copy()
        foco_manual['peso_foco'] = 1
        foco_manual['observacao'] = 'selecionado manualmente'
        foco = pd.concat([foco, foco_manual[['ean', 'principio_ativo', 'peso_foco', 'observacao']]], ignore_index=True).drop_duplicates('ean')
    if not inventario.empty:
        if distrib_visiveis:
            inventario = inventario[inventario['distribuidora'].astype(str).isin(list(distrib_visiveis))].copy()
        if descontos_adic:
            mapa = {k: float(v) for k, v in descontos_adic}
            exc_map = {k: set(v.split('|')) if isinstance(v, str) and v else set() for k, v in descontos_exc}
            inventario['desconto_adicional'] = inventario['distribuidora'].map(mapa).fillna(0.0)
            exc_mask = inventario.apply(lambda r: str(r.get('ean','')) in exc_map.get(str(r.get('distribuidora','')), set()), axis=1) if descontos_exc else pd.Series(False, index=inventario.index)
            inventario.loc[exc_mask, 'desconto_adicional'] = 0.0
            inventario['desconto_total'] = inventario['desconto'].fillna(0) + inventario['desconto_adicional']
            inventario['pf_base_sem'] = (inventario['preco_sem_imposto'] / (1 - (inventario['desconto'].fillna(0) / 100)).clip(lower=0.0001)).replace([pd.NA, pd.NaT], 0)
            inventario['pf_dist'] = inventario['pf_base_sem'].where(inventario['pf_base_sem'] > 0, inventario['pf_dist'])
            inventario['preco_sem_imposto'] = (inventario['pf_dist'] * (1 - (inventario['desconto_total'] / 100))).round(2)
            inventario['desconto'] = inventario['desconto_total']
    base = base_full.copy()
    if data_inicio is not None and data_fim is not None and 'data_do_pedido' in base.columns:
        ini = pd.to_datetime(data_inicio)
        fim = pd.to_datetime(data_fim) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        base = base[(base['data_do_pedido'] >= ini) & (base['data_do_pedido'] <= fim)]
        base_full = base_full[(base_full['data_do_pedido'] >= ini) & (base_full['data_do_pedido'] <= fim)] if 'data_do_pedido' in base_full.columns else base_full
    if cidade_key != 'Todas' and not base.empty:
        base = base[base['cidade'] == cidade_key]
        base_full = base_full[base_full['cidade'] == cidade_key]
        clientes = clientes[clientes['cidade'] == cidade_key].copy()
    resumo = build_cliente_resumo(base, base_full, clientes)
    gap = build_gap_por_cliente(base, produtos)
    score_df = score_clientes(resumo, gap)
    foco_eans = set(foco['ean'].dropna().astype(str).tolist()) if not foco.empty else set()
    if foco_eans and not base.empty:
        foco_compra = base[(base['status_pedido'].isin(['FATURADO', 'FATURADO PARCIAL'])) & (base['ean'].astype(str).isin(foco_eans))].groupby('cnpj_pdv')['ean'].nunique().gt(0)
        score_df = score_df.merge(foco_compra.rename('comprou_foco_semana'), left_on='cnpj', right_index=True, how='left')
        score_df['comprou_foco_semana'] = score_df['comprou_foco_semana'].fillna(False)
    else:
        score_df['comprou_foco_semana'] = False
    pref_df = pd.DataFrame(preferencias_key, columns=['cnpj', 'distribuidoras_preferidas']) if preferencias_key else pd.DataFrame(columns=['cnpj', 'distribuidoras_preferidas'])
    oportunidades = build_oportunidades_cliente(base_full, produtos, foco, inventario, pref_df)
    cancelados = build_cancelados_cliente(base_full)
    return pedidos, produtos, clientes, foco, inventario, base, base_full, resumo, gap, score_df, oportunidades, cancelados

status_live = load_status()
data_version_key = '|'.join([
    str(status_live.get('bussola', {}).get('ultimo_sucesso', '')),
    str(status_live.get('bussola', {}).get('atualizado_em', '')),
    str(status_live.get('mercadofarma', {}).get('ultimo_sucesso', '')),
    str(status_live.get('mercadofarma', {}).get('atualizado_em', '')),
    str(status_live.get('github_actions', {}).get('atualizado_em', '')),
])

persist_cfg = load_user_config()

for key, default in {
    'page': 'Dashboard',
    'dist_pref': persist_cfg.get('dist_pref', {}),
    'pedido_cliente_cnpj': None,
    'cart_items': [],
    'preselected_products': {},
    'visible_dists': persist_cfg.get('visible_dists', []),
    'addl_discount': persist_cfg.get('addl_discount', {}),
    'addl_discount_exclusions': persist_cfg.get('addl_discount_exclusions', {}),
    'sip_selected_id': None,
    'foco_mes_manual': persist_cfg.get('foco_mes_manual', []),
    'foco_semana_manual': persist_cfg.get('foco_semana_manual', []),
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

pedidos_ref, produtos_ref, clientes_ref, _, inventario_ref, _ = get_clean_bases(data_version_key)

if 'filtro_data_inicial' not in st.session_state:
    st.session_state.filtro_data_inicial = primeiro_dia_mes_atual()
if 'filtro_data_final' not in st.session_state:
    st.session_state.filtro_data_final = ultimo_dia_mes_atual()

with st.sidebar:
    st.markdown('<div class="sidebar-title">🧭 Painel de Visitas</div>', unsafe_allow_html=True)
    cart_n = len(st.session_state.get('cart_items', []))
    menu_items = ['Dashboard', 'Clientes', 'Montar pedido', 'Pedido Inteligente', f'Carrinho ({cart_n})', 'SIP', 'Importação']
    for item in menu_items:
        page_name = 'Carrinho' if item.startswith('Carrinho') else item
        is_active = st.session_state.page == page_name
        if st.button(item, use_container_width=True, type='primary' if is_active else 'secondary', key=f'menu_{item}'):
            st.session_state.page = page_name
            st.rerun()
    st.markdown('---')
    with st.expander('Filtros do painel', expanded=False):
        cidades = sorted(clientes_ref['cidade'].dropna().unique().tolist())
        cidade_global = st.selectbox('Cidade em atuacao', ['Todas'] + cidades)

        data_inicio = st.date_input('Data inicial', format='DD/MM/YYYY', key='filtro_data_inicial')
        data_fim = st.date_input('Data final', format='DD/MM/YYYY', key='filtro_data_final')
        if data_inicio > data_fim:
            st.error('A data inicial nao pode ser maior que a data final.')
            st.stop()

        foco_manual = st.multiselect('Produtos foco da semana', sorted(produtos_ref['principio_ativo'].dropna().unique().tolist()), default=st.session_state.get('foco_semana_manual', []), placeholder='Escolha a opcao')
        foco_mes_manual = st.multiselect('Produtos foco do mes', sorted(produtos_ref['principio_ativo'].dropna().unique().tolist()), default=st.session_state.get('foco_mes_manual', []), placeholder='Escolha a opcao')
        st.session_state.foco_mes_manual = foco_mes_manual
        st.session_state.foco_semana_manual = foco_manual
        dist_options = sorted(inventario_ref['distribuidora'].dropna().astype(str).unique().tolist()) if not inventario_ref.empty else []
        default_visiveis = st.session_state.visible_dists or dist_options
        visiveis = st.multiselect('Distribuidoras visiveis no painel', dist_options, default=default_visiveis, placeholder='Todos')
        st.session_state.visible_dists = visiveis
        add_map = {}
        exc_map = {}
        for dist in visiveis:
            add_map[dist] = st.number_input(f'{dist} (%)', min_value=0.0, max_value=100.0, value=float(st.session_state.addl_discount.get(dist, 0.0)), step=0.5, key=f'add_{dist}')
            opts_exc = []
            if not inventario_ref.empty:
                aux = inventario_ref[inventario_ref['distribuidora'].astype(str) == dist][['ean','principio_ativo']].drop_duplicates().sort_values('principio_ativo')
                opts_exc = [f"{r['ean']} - {r['principio_ativo']}" for _, r in aux.iterrows()]
            sel_exc = st.multiselect(f'Aplicar em todos os produtos exceto...', opts_exc, default=st.session_state.addl_discount_exclusions.get(dist, []), key=f'exc_{dist}', placeholder='Escolha as opcoes')
            exc_map[dist] = sel_exc
        st.session_state.addl_discount = add_map
        st.session_state.addl_discount_exclusions = exc_map
        hoje = agora_br().weekday()
        semana_ativa = hoje in [1, 2, 3]
        st.caption(f'Foco automatico ativo: {"Sim" if semana_ativa else "Nao"}.')

if 'cidade_global' not in locals():
    cidades = sorted(clientes_ref['cidade'].dropna().unique().tolist())
    cidade_global = 'Todas'
    data_inicio = st.session_state.filtro_data_inicial
    data_fim = st.session_state.filtro_data_final
    foco_manual = st.session_state.get('foco_semana_manual', [])
    foco_mes_manual = st.session_state.get('foco_mes_manual', [])
    semana_ativa = agora_br().weekday() in [1, 2, 3]

preferencias_key = tuple(sorted((k, '|'.join(v) if isinstance(v, list) else str(v)) for k, v in st.session_state.dist_pref.items()))
descontos_key = tuple(sorted((k, float(v)) for k, v in st.session_state.addl_discount.items()))
exclusoes_key = tuple(sorted((k, '|'.join([x.split(' — ')[0] for x in v])) for k, v in st.session_state.addl_discount_exclusions.items()))
foco_key = tuple(sorted(set((foco_manual if semana_ativa or foco_manual else []) + foco_mes_manual)))


def _views_for(cidade: str):
    return compute_views(
        data_version_key,
        foco_key,
        cidade,
        preferencias_key,
        data_inicio,
        data_fim,
        tuple(st.session_state.visible_dists),
        descontos_key,
        exclusoes_key,
    )


page = st.session_state.page

if page == 'Dashboard':
    _, _, clientes_g, foco_g, inventario_g, _, base_full_g, _, _, score_df_g, oportunidades_g, _ = _views_for('Todas')
    render_dashboard(score_df_g, oportunidades_g, foco_g, inventario_g, clientes_g, base_full_g, data_inicio=data_inicio, data_fim=data_fim)
elif page == 'Clientes':
    pedidos, produtos, clientes, foco, inventario, base, base_full, resumo, gap, score_df, oportunidades, cancelados = _views_for(cidade_global)
    if not score_df.empty:
        with st.expander('Distribuidoras preferidas do cliente', expanded=False):
            dist_options = sorted([d for d in inventario['distribuidora'].dropna().astype(str).unique().tolist() if d]) if not inventario.empty else []
            cli_ref = score_df[['nome_fantasia', 'cnpj']].drop_duplicates().sort_values('nome_fantasia').copy()
            cli_ref['label'] = cli_ref['nome_fantasia'] + ' — ' + cli_ref['cnpj'].astype(str)
            selected_client = st.selectbox('Cliente para definir distribuidoras', ['Selecione um cliente'] + cli_ref['label'].tolist())
            if selected_client != 'Selecione um cliente':
                cnpj_sel = cli_ref.loc[cli_ref['label'] == selected_client, 'cnpj'].iloc[0]
                atual = st.session_state.dist_pref.get(cnpj_sel, [])
                pref = st.multiselect('Distribuidoras preferidas', dist_options, default=atual, key=f'pref_{cnpj_sel}', placeholder='Escolha as opções')
                if st.button('Salvar distribuidoras preferidas'):
                    st.session_state.dist_pref[cnpj_sel] = pref
                    save_user_config({
                        'foco_semana_manual': st.session_state.get('foco_semana_manual', []),
                        'foco_mes_manual': st.session_state.get('foco_mes_manual', []),
                        'visible_dists': st.session_state.get('visible_dists', []),
                        'addl_discount': st.session_state.get('addl_discount', {}),
                        'addl_discount_exclusions': st.session_state.get('addl_discount_exclusions', {}),
                        'dist_pref': st.session_state.get('dist_pref', {}),
                    })
                    st.cache_data.clear()
                    st.rerun()
    render_clientes(score_df, oportunidades, cancelados, base_full, produtos, inventario, foco, clientes)
elif page == 'Montar pedido':
    pedidos, produtos, clientes, foco, inventario, base, base_full, resumo, gap, score_df, oportunidades, cancelados = _views_for(cidade_global)
    render_pedido(score_df, oportunidades, inventario, cidade_global, base_full=base_full, produtos=produtos, foco=foco, clientes_df=clientes)
elif page == 'Pedido Inteligente':
    pedidos, produtos, clientes, foco, inventario, base, base_full, resumo, gap, score_df, oportunidades, cancelados = _views_for(cidade_global)
    render_busca_inteligente(score_df, inventario, clientes_df=clientes)
elif page == 'Carrinho':
    _, _, clientes_g, foco_g, inventario_g, _, base_full_g, _, _, score_df_g, oportunidades_g, _ = _views_for('Todas')
    render_cart(inventario_g, foco=foco_g)
elif page == 'SIP':
    _, _, clientes_g, foco_g, inventario_g, _, base_full_g, _, _, score_df_g, oportunidades_g, _ = _views_for('Todas')
    render_sip(score_df_g, clientes_g)
else:
    _, _, _, _, _, _, _, _, _, score_df_g, _, _ = _views_for('Todas')
    render_importacao(score_df_g, produtos_ref)

