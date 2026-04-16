from __future__ import annotations
import pandas as pd
import streamlit as st
from io import StringIO
from services.integrations import load_creds
from services.repo_state import enqueue_command, load_status

MINIMOS = {
    'Total - TO': 300.0,
    'Panpharma - GO': None,
    'Nazaria - MA - Imperatriz': 200.0,
    'Profarma - DF': 200.0,
}


def _money(v):
    try:
        return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return "R$ 0,00"


def _plain_digits(v: str) -> str:
    return ''.join(ch for ch in str(v or '') if ch.isdigit())


def _pedidos_minimos(df: pd.DataFrame):
    grp = df.groupby('Distribuidora', as_index=False)['Total'].sum()
    rows = []
    ok = True
    for _, r in grp.iterrows():
        minimo = MINIMOS.get(r['Distribuidora'])
        atingido = True if minimo is None else r['Total'] >= minimo
        if not atingido:
            ok = False
        status = 'Sem pedido mínimo' if minimo is None else ('Atingido' if atingido else f'Faltam {_money(minimo-r["Total"])}')
        rows.append({'Distribuidora': r['Distribuidora'], 'Total pedido': _money(r['Total']), 'Pedido mínimo': 'Sem pedido mínimo' if minimo is None else _money(minimo), 'Status': status})
    return ok, rows


def render_cart(inventario: pd.DataFrame | None = None, foco: pd.DataFrame | None = None):
    st.markdown('<h2 class="page-title">Carrinho</h2>', unsafe_allow_html=True)
    items = st.session_state.get('cart_items', [])
    foco_eans = set(foco['ean'].astype(str).tolist()) if foco is not None and not foco.empty else set()
    if not items:
        st.info('Seu carrinho está vazio.')
        return
    inv = inventario.copy() if inventario is not None else pd.DataFrame()
    if not inv.empty:
        inv['preco_sem_imposto'] = pd.to_numeric(inv.get('preco_sem_imposto', 0), errors='coerce').fillna(0)
        inv['estoque'] = pd.to_numeric(inv.get('estoque', 0), errors='coerce').fillna(0)

    updated = [item.copy() for item in items]
    df = pd.DataFrame(updated)
    cab = df[['Empresa', 'Razao social', 'Nome do comprador', 'Tel do comprador', 'Cliente', 'CNPJ']].fillna('').drop_duplicates().head(1)
    if not cab.empty:
        h = cab.iloc[0]
        st.markdown(f"<div class='detail-card'><div class='detail-title'>{h.get('Empresa','')}</div><div class='detail-sub'>Razão social: {h.get('Razao social','')} • CNPJ: {_plain_digits(h.get('CNPJ',''))}</div><div class='detail-sub'>Comprador: {h.get('Nome do comprador','')} • Tel: {_plain_digits(h.get('Tel do comprador',''))}</div></div>", unsafe_allow_html=True)

    remover_idx = None
    st.caption('PRODUTO / DISTRIBUIDORA / PREÇO / ESTOQUE / MIX / QTDE')
    for idx, item in enumerate(updated):
        ean = str(item.get('EAN', ''))
        variantes = inv[inv['ean'].astype(str) == ean].copy().sort_values(['preco_sem_imposto', 'estoque'], ascending=[True, False]) if not inv.empty else pd.DataFrame()
        if variantes.empty:
            variantes = pd.DataFrame([{'distribuidora': item.get('Distribuidora', 'Sem distribuidora'), 'preco_sem_imposto': item.get('Preço', 0), 'estoque': item.get('Estoque', 0)}])
        variantes['distribuidora'] = variantes['distribuidora'].fillna('Sem distribuidora').astype(str)
        opcoes = variantes['distribuidora'].tolist() or ['Sem distribuidora']
        atual = item.get('Distribuidora', opcoes[0])
        default_idx = opcoes.index(atual) if atual in opcoes else 0
        destaque = "<div style='color:#C1121F;font-size:.78rem;font-weight:800'>FOCO</div>" if ean in foco_eans or item.get('Foco') else ''
        cols = st.columns([4.0, 2.1, 1.3, 1.0, 1.2, 1.0, .8])
        cols[0].markdown(f"**{item.get('Produto','')}**{destaque}", unsafe_allow_html=True)
        nova_dist = cols[1].selectbox('Distribuidora', opcoes, index=default_idx, key=f'cart_dist_{idx}', label_visibility='collapsed')
        escolha = variantes[variantes['distribuidora'] == nova_dist].iloc[0] if not variantes[variantes['distribuidora'] == nova_dist].empty else variantes.iloc[0]
        novo_preco = float(pd.to_numeric(escolha.get('preco_sem_imposto', item.get('Preço', 0)), errors='coerce') or 0)
        novo_est = int(pd.to_numeric(escolha.get('estoque', item.get('Estoque', 0)), errors='coerce') or 0)
        cols[2].markdown(_money(novo_preco))
        cols[3].markdown(str(novo_est))
        cols[4].markdown(str(item.get('Mix','')))
        nova_qtd = cols[5].number_input('Qtde', min_value=0, step=1, value=int(pd.to_numeric(item.get('Qtde', 1), errors='coerce') or 0), key=f'cart_qtd_{idx}', label_visibility='collapsed')
        if cols[6].button('🗑', key=f'del_{idx}', use_container_width=True):
            remover_idx = idx
        updated[idx]['Distribuidora'] = nova_dist
        updated[idx]['Preço'] = novo_preco
        updated[idx]['Estoque'] = novo_est
        updated[idx]['Qtde'] = int(nova_qtd)
    if remover_idx is not None:
        updated.pop(remover_idx)
        st.session_state.cart_items = updated
        st.rerun()
    updated = [x for x in updated if int(pd.to_numeric(x.get('Qtde', 0), errors='coerce') or 0) > 0]
    st.session_state.cart_items = updated
    if not updated:
        st.info('Seu carrinho ficou vazio.')
        return
    df = pd.DataFrame(updated)
    df['Total'] = pd.to_numeric(df['Preço'], errors='coerce').fillna(0) * pd.to_numeric(df['Qtde'], errors='coerce').fillna(0)
    st.success(f'Total estimado do carrinho: {_money(df["Total"].sum())}')
    pode_baixar, rows = _pedidos_minimos(df)
    st.markdown('<div class="section-title">Mínimo por distribuidora</div>', unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    c1, c2, c3, c4, c5 = st.columns(5)
    if c1.button('Limpar carrinho', use_container_width=True):
        st.session_state.cart_items = []
        st.rerun()
    export_df = df[['Cliente','CNPJ','Empresa','Razao social','Nome do comprador','Tel do comprador','EAN','Produto','Distribuidora','Preço','Estoque','Mix','Qtde','Total']].copy()
    export_df['CNPJ'] = export_df['CNPJ'].map(_plain_digits)
    export_df['Tel do comprador'] = export_df['Tel do comprador'].map(_plain_digits)
    export_df['EAN'] = export_df['EAN'].map(_plain_digits)
    export_df['Preço'] = export_df['Preço'].map(_money)
    export_df['Total'] = export_df['Total'].map(_money)
    csv_out = export_df.to_csv(index=False, sep=';', encoding='utf-8-sig')
    c2.download_button('Baixar carrinho em CSV', data=csv_out.encode('utf-8-sig'), file_name='carrinho_pedido.csv', mime='text/csv', use_container_width=True, disabled=not pode_baixar)
    txt = StringIO()
    if not cab.empty:
        h = cab.iloc[0]
        txt.write(f"EMPRESA: {h.get('Empresa','')}\nRAZAO SOCIAL: {h.get('Razao social','')}\nCOMPRADOR: {h.get('Nome do comprador','')}\nTEL: {_plain_digits(h.get('Tel do comprador',''))}\nCNPJ: {_plain_digits(h.get('CNPJ',''))}\n\n")
    for _, item in export_df.iterrows():
        txt.write(f"{item['CNPJ']}; {item['EAN']}; {item['Produto']}; {item['Distribuidora']}; {item['Qtde']}; {item['Preço']}; {item['Total']}\n")
    c3.download_button('Baixar carrinho em TXT', data=txt.getvalue().encode('utf-8-sig'), file_name='carrinho_pedido.txt', mime='text/plain', use_container_width=True, disabled=not pode_baixar)
    creds = load_creds()
    status = load_status()
    headless = c4.toggle('Enviar invisível', value=True)
    st.caption(f"Último comando GitHub: {status.get('comandos',{}).get('ultimo_resultado','—')}")
    cnpj_envio = _plain_digits(cab.iloc[0].get('CNPJ','')) if not cab.empty else ''
    cupom = st.text_input('Cupom de desconto (opcional)', value=st.session_state.get('mf_cupom',''))
    st.session_state['mf_cupom'] = cupom
    if c5.button('Limpar pedido MF', use_container_width=True, disabled=not bool(cnpj_envio)):
        _, ok, msg = enqueue_command('limpar_pedido_mf', {'cnpj': cnpj_envio, 'headless': headless})
        (st.success if ok else st.error)('Limpeza enviada ao GitHub Actions.' if ok else msg)
    confirmar_envio = st.checkbox('Tem certeza que deseja enviar pedido ao Mercado Farma?')
    if st.button('Enviar pedido para Mercado Farma', use_container_width=True, disabled=(not pode_baixar) or (not confirmar_envio)):
        _, ok, msg = enqueue_command('enviar_pedido_mf', {'cart_items': updated, 'headless': headless, 'cupom': cupom})
        (st.success if ok else st.error)('Envio do pedido disparado no GitHub Actions.' if ok else msg)
