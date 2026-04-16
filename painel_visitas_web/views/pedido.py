from __future__ import annotations
import math
import re
import pandas as pd
import streamlit as st

def _money(v):
    try:
        return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return "R$ 0,00"

def _norm(s: str) -> str:
    s = str(s).lower()
    rep = str.maketrans('áàâãäéèêëíìîïóòôõöúùûüç', 'aaaaaeeeeiiiiooooouuuuc')
    s = s.translate(rep)
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()

def _search_match(name: str, ean: str, query: str) -> bool:
    q = _norm(query)
    if not q:
        return True
    n = _norm(name)
    e = str(ean)
    tokens = [t for t in q.split() if t]
    return all(t in n or t in e for t in tokens)

def _calc_preco_sem(escolha):
    return float(pd.to_numeric(escolha.get('preco_sem_imposto', 0), errors='coerce') or 0)

def _catalogo_cliente(cnpj: str, base_full: pd.DataFrame, produtos: pd.DataFrame, inventario: pd.DataFrame, oportunidades: pd.DataFrame) -> pd.DataFrame:
    if base_full is None or not isinstance(base_full, pd.DataFrame):
        base_full = pd.DataFrame()
    if produtos is None or not isinstance(produtos, pd.DataFrame):
        produtos = pd.DataFrame()
    if oportunidades is None or not isinstance(oportunidades, pd.DataFrame):
        oportunidades = pd.DataFrame()

    if not base_full.empty and {'cnpj_pdv', 'status_pedido', 'ean'}.issubset(base_full.columns):
        comprados = base_full[
            (base_full['cnpj_pdv'].astype(str) == str(cnpj))
            & (base_full['status_pedido'].isin(['FATURADO', 'FATURADO PARCIAL']))
        ][['ean']].drop_duplicates()
    else:
        comprados = pd.DataFrame(columns=['ean'])

    catalogo = oportunidades.copy() if not oportunidades.empty else produtos.copy()
    if catalogo is None or not isinstance(catalogo, pd.DataFrame):
        catalogo = pd.DataFrame()
    if catalogo.empty and not produtos.empty:
        catalogo = produtos.copy()

    if 'principio_ativo' not in catalogo.columns and not produtos.empty:
        catalogo = produtos.copy()

    if 'score_sugestao' not in catalogo.columns:
        catalogo['score_sugestao'] = 0

    cols_merge = [c for c in ['ean', 'principio_ativo', 'mix_lancamentos'] if c in produtos.columns]
    if cols_merge:
        base_prod = produtos[cols_merge].drop_duplicates().copy()
        for col in cols_merge:
            if col not in catalogo.columns:
                catalogo[col] = ''
        catalogo = catalogo.merge(base_prod, on=cols_merge, how='outer')

    if 'ean' not in catalogo.columns:
        catalogo['ean'] = ''
    if 'principio_ativo' not in catalogo.columns:
        catalogo['principio_ativo'] = ''
    if 'mix_lancamentos' not in catalogo.columns:
        catalogo['mix_lancamentos'] = 'LINHA'

    catalogo = catalogo.merge(comprados.assign(comprado=1), on='ean', how='left')
    catalogo['comprado'] = catalogo['comprado'].fillna(0)

    if 'preco_sem_imposto' not in catalogo.columns:
        catalogo['preco_sem_imposto'] = 0.0
    if 'estoque' not in catalogo.columns:
        catalogo['estoque'] = 0.0

    catalogo['preco_sem_imposto'] = pd.to_numeric(catalogo['preco_sem_imposto'], errors='coerce').fillna(0)
    catalogo['estoque'] = pd.to_numeric(catalogo['estoque'], errors='coerce').fillna(0)

    return catalogo.sort_values(
        ['comprado', 'score_sugestao', 'preco_sem_imposto', 'principio_ativo'],
        ascending=[True, False, True, True],
    ).drop_duplicates('ean')

def _add_to_cart(itens):
    carrinho = st.session_state.setdefault('cart_items', [])
    existentes = {(str(i['CNPJ']), str(i['EAN']), str(i['Distribuidora'])): idx for idx, i in enumerate(carrinho)}
    adicionados = 0
    for item in itens:
        key = (str(item['CNPJ']), str(item['EAN']), str(item['Distribuidora']))
        if key in existentes:
            carrinho[existentes[key]].update(item)
        else:
            carrinho.append(item)
            adicionados += 1
    st.session_state.cart_items = carrinho
    return adicionados

def _wa_link(phone: str, msg: str=''):
    digits = re.sub(r'\D','',str(phone or ''))
    if not digits:
        return ''
    return f"https://wa.me/55{digits}?text={msg}" if len(digits)<=11 else f"https://wa.me/{digits}?text={msg}"

def render_pedido(score_df: pd.DataFrame, oportunidades: pd.DataFrame, inventario: pd.DataFrame, cidade: str = 'Todas', base_full: pd.DataFrame | None = None, produtos: pd.DataFrame | None = None, foco: pd.DataFrame | None = None, clientes_df: pd.DataFrame | None = None):
    st.markdown('<h2 class="page-title">Montar pedido</h2>', unsafe_allow_html=True)
    clientes = score_df.copy()
    if cidade != 'Todas':
        clientes = clientes[clientes['cidade'] == cidade]
    if clientes.empty:
        st.info('Nenhum cliente disponível para montar pedido.')
        return
    cli_ref = clientes[['nome_fantasia', 'cnpj', 'cidade', 'uf']].drop_duplicates().sort_values('nome_fantasia').copy()
    cli_ref['label'] = cli_ref['nome_fantasia'] + ' — ' + cli_ref['cnpj'].astype(str)
    labels = cli_ref['label'].tolist()
    default_idx = 0
    if st.session_state.get('pedido_cliente_cnpj') is not None:
        m = cli_ref.index[cli_ref['cnpj'] == st.session_state.get('pedido_cliente_cnpj')]
        if len(m):
            default_idx = cli_ref.index.get_loc(m[0])
    cliente_label = st.selectbox('Cliente (nome + CNPJ)', labels, index=default_idx)
    cliente_row = cli_ref[cli_ref['label'] == cliente_label].iloc[0]
    cnpj = cliente_row['cnpj']
    st.session_state.pedido_cliente_cnpj = cnpj
    base_cli = (base_full if base_full is not None else pd.DataFrame()).copy()
    base_cli = base_cli[base_cli['cnpj_pdv'] == cnpj] if not base_cli.empty else pd.DataFrame()
    cli_cad = clientes_df[clientes_df['cnpj']==cnpj].head(1) if clientes_df is not None and not clientes_df.empty and 'cnpj' in clientes_df.columns else pd.DataFrame()
    def _cad(col, default=''):
        if not cli_cad.empty and col in cli_cad.columns and not cli_cad[col].dropna().empty:
            return str(cli_cad.iloc[0][col])
        if col in base_cli.columns and not base_cli[col].dropna().empty:
            return str(base_cli[col].dropna().iloc[0])
        return default
    cadastro = {'empresa': cliente_row['nome_fantasia'],'razao_social': _cad('razao_social', cliente_row['nome_fantasia']),'nome_fantasia': _cad('nome_fantasia', cliente_row['nome_fantasia']),'nome_comprador': _cad('nome_contato',''),'tel_comprador': _cad('contato',''),'endereco': _cad('endereco',''),'bairro': _cad('bairro',''),'cidade': _cad('cidade',cliente_row.get('cidade','')),'uf': _cad('uf',cliente_row.get('uf',''))}
    wa = _wa_link(cadastro['tel_comprador'])
    st.markdown(f"<div class='detail-card'><div class='detail-title'>{cadastro['razao_social']}</div><div class='detail-sub'>{cadastro['nome_fantasia']} • CNPJ: {cliente_row['cnpj']}</div><div class='detail-grid'><div><span>Cidade</span><b>{cadastro['cidade']} - {cadastro['uf']}</b></div><div><span>Endereço</span><b>{cadastro['endereco']}</b></div><div><span>Bairro</span><b>{cadastro['bairro']}</b></div><div><span>Responsável</span><b>{cadastro['nome_comprador']}</b></div><div><span>Contato</span><b>{cadastro['tel_comprador']}</b></div><div><span>WhatsApp</span><b>{'Disponível' if wa else 'Sem contato'}</b></div></div></div>", unsafe_allow_html=True)
    if wa:
        st.link_button('Abrir WhatsApp do cliente', wa, use_container_width=True)

    linha_score = score_df[score_df['cnpj'].astype(str)==str(cnpj)].head(1) if score_df is not None and not score_df.empty else pd.DataFrame()
    if not linha_score.empty:
        r = linha_score.iloc[0]
        cols_m = st.columns(4)
        with cols_m[0]: st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>OL sem combate</div><div class='metric-value'>{_money(r.get('ol_sem_combate',0))}</div></div>", unsafe_allow_html=True)
        with cols_m[1]: st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>Prioritários</div><div class='metric-value'>{_money(r.get('ol_prioritarios',0))}</div></div>", unsafe_allow_html=True)
        with cols_m[2]: st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>Lançamentos</div><div class='metric-value'>{_money(r.get('ol_lancamentos',0))}</div></div>", unsafe_allow_html=True)
        with cols_m[3]: st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>Combate</div><div class='metric-value'>{_money(r.get('ol_combate',0))}</div></div>", unsafe_allow_html=True)

    catalogo = _catalogo_cliente(cnpj, base_full if base_full is not None else pd.DataFrame(), produtos if produtos is not None else pd.DataFrame(), inventario, oportunidades)
    if catalogo.empty:
        st.info('Sem catálogo disponível para este cliente.')
        return
    pre_map = st.session_state.get('preselected_products', {})
    preselected = set(pre_map.keys()) if isinstance(pre_map, dict) else set()
    if preselected:
        catalogo['prioridade_manual'] = catalogo['ean'].astype(str).isin(preselected).astype(int)
        catalogo = catalogo.sort_values(['prioridade_manual', 'comprado', 'score_sugestao', 'preco_sem_imposto'], ascending=[False, True, False, True])

    c1, c2, c3 = st.columns([1.4, 1.0, .8])
    busca = c1.text_input('Buscar produto por nome ou EAN')
    mix_filtro = c2.selectbox('Mix', ['Todos', 'LANCAMENTOS', 'PRIORITARIOS', 'LINHA', 'COMBATE'])
    per_page = c3.selectbox('Produtos por página', [10, 15, 20], index=1)
    if busca:
        catalogo = catalogo[catalogo.apply(lambda r: _search_match(r.get('principio_ativo',''), r.get('ean',''), busca), axis=1)]
    if mix_filtro != 'Todos':
        catalogo = catalogo[catalogo['mix_lancamentos'] == mix_filtro]
    total_pages = max(1, math.ceil(len(catalogo) / per_page))
    page_key = f'pedido_page_{cnpj}'
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    st.session_state[page_key] = max(1, min(int(st.session_state[page_key]), total_pages))
    nav1, nav2, nav3 = st.columns([.9, 1.2, .9])
    with nav1:
        if st.button('◀ Anterior', use_container_width=True, disabled=st.session_state[page_key] <= 1, key=f'prev_{cnpj}'):
            st.session_state[page_key] = max(1, st.session_state[page_key] - 1)
            st.rerun()
    with nav2:
        st.number_input('Página', min_value=1, max_value=total_pages, key=page_key, step=1)
    with nav3:
        if st.button('Próxima ▶', use_container_width=True, disabled=st.session_state[page_key] >= total_pages, key=f'next_{cnpj}'):
            st.session_state[page_key] = min(total_pages, st.session_state[page_key] + 1)
            st.rerun()
    page = int(st.session_state[page_key])
    catalogo = catalogo.iloc[(page - 1) * per_page: page * per_page].copy()

    st.markdown('<div class="section-title">Produtos sugeridos e catálogo completo</div>', unsafe_allow_html=True)
    st.caption('PRODUTO / DISTRIBUIDORA / PREÇO SEM IMPOSTO / ESTOQUE / MIX / QTDE')
    inv = inventario.copy() if inventario is not None else pd.DataFrame()
    if not inv.empty:
        inv['preco_sem_imposto'] = pd.to_numeric(inv.get('preco_sem_imposto', 0), errors='coerce').fillna(0)
        inv['estoque'] = pd.to_numeric(inv.get('estoque', 0), errors='coerce').fillna(0)
        inv['pf_dist'] = pd.to_numeric(inv.get('pf_dist', 0), errors='coerce').fillna(0)
        inv['desconto'] = pd.to_numeric(inv.get('desconto', 0), errors='coerce').fillna(0)
        inv = inv[inv['estoque'] > 0]

    def collect_items():
        itens_local = []
        for _, row in catalogo.iterrows():
            ean = row['ean']
            qty = st.session_state.get(f'qty_{cnpj}_{ean}', 0)
            if qty and qty > 0:
                variantes = inv[inv['ean'] == ean].copy().sort_values(['preco_sem_imposto', 'estoque'], ascending=[True, False]) if not inv.empty else pd.DataFrame()
                if variantes.empty:
                    variantes = pd.DataFrame([{'distribuidora': row.get('distribuidora', 'Sem distribuidora') or 'Sem distribuidora', 'preco_sem_imposto': row.get('preco_sem_imposto', 0), 'estoque': row.get('estoque', 0), 'mix_lancamentos': row.get('mix_lancamentos', 'LINHA'), 'pf_dist': 0, 'desconto': 0}])
                variantes['distribuidora'] = variantes['distribuidora'].fillna('Sem distribuidora').astype(str).replace({'nan': 'Sem distribuidora'})
                opcoes = [d for d in variantes['distribuidora'].tolist() if str(d).strip() and str(d).lower() != 'nan'] or ['Sem distribuidora']
                dist = st.session_state.get(f'dist_{cnpj}_{ean}', opcoes[0])
                escolha_df = variantes[variantes['distribuidora'] == dist]
                escolha = escolha_df.iloc[0] if not escolha_df.empty else variantes.iloc[0]
                preco = _calc_preco_sem(escolha)
                foco_flag = False if foco is None or foco.empty else str(ean) in foco['ean'].astype(str).tolist()
                itens_local.append({'Cliente': cliente_row['nome_fantasia'], 'CNPJ': str(cnpj), 'Empresa': cadastro['empresa'], 'Razao social': cadastro['razao_social'], 'Nome do comprador': cadastro['nome_comprador'], 'Tel do comprador': cadastro['tel_comprador'], 'EAN': str(ean), 'Produto': row['principio_ativo'], 'Distribuidora': dist, 'Preço': preco, 'Estoque': int(pd.to_numeric(escolha.get('estoque', 0), errors='coerce') or 0), 'Mix': row.get('mix_lancamentos', 'LINHA'), 'Qtde': int(qty), 'Foco': foco_flag})
        return itens_local

    if st.button('Adicionar selecionados ao carrinho', use_container_width=True, key='add_top'):
        itens = collect_items()
        if itens:
            adicionados = _add_to_cart(itens); st.success(f'{adicionados if adicionados else len(itens)} produto(s) adicionado(s) ao carrinho.')
        else:
            st.warning('Selecione ao menos um produto com quantidade maior que zero.')

    for _, row in catalogo.iterrows():
        ean = row['ean']
        variantes = inv[inv['ean'] == ean].copy().sort_values(['preco_sem_imposto', 'estoque'], ascending=[True, False]) if not inv.empty else pd.DataFrame()
        if variantes.empty:
            variantes = pd.DataFrame([{'distribuidora': row.get('distribuidora', 'Sem distribuidora') or 'Sem distribuidora', 'preco_sem_imposto': row.get('preco_sem_imposto', 0), 'estoque': row.get('estoque', 0), 'mix_lancamentos': row.get('mix_lancamentos', 'LINHA'), 'pf_dist': 0, 'desconto': 0}])
        variantes['distribuidora'] = variantes['distribuidora'].fillna('Sem distribuidora').astype(str).replace({'nan': 'Sem distribuidora'})
        opcoes = [d for d in variantes['distribuidora'].tolist() if str(d).strip() and str(d).lower() != 'nan'] or ['Sem distribuidora']
        c_prod, c_dist, c_preco, c_est, c_mix, c_qtd = st.columns([4.6, 2.0, 1.4, 1.0, 1.2, 1.0])
        c_prod.markdown(f"**{row['principio_ativo']}**")
        dist = c_dist.selectbox('Distribuidora', opcoes, key=f'dist_{cnpj}_{ean}', label_visibility='collapsed')
        escolha_df = variantes[variantes['distribuidora'] == dist]
        escolha = escolha_df.iloc[0] if not escolha_df.empty else variantes.iloc[0]
        c_preco.markdown(_money(_calc_preco_sem(escolha)))
        c_est.markdown(str(int(pd.to_numeric(escolha.get('estoque', 0), errors='coerce') or 0)))
        c_mix.markdown(str(row.get('mix_lancamentos', 'LINHA')))
        c_qtd.number_input('Qtde', min_value=0, step=1, key=f'qty_{cnpj}_{ean}', label_visibility='collapsed')
    if st.button('Adicionar selecionados ao carrinho', use_container_width=True, key='add_bottom'):
        itens = collect_items()
        if itens:
            adicionados = _add_to_cart(itens); st.success(f'{adicionados if adicionados else len(itens)} produto(s) adicionado(s) ao carrinho.')
        else:
            st.warning('Selecione ao menos um produto com quantidade maior que zero.')
