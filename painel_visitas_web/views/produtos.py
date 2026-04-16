from __future__ import annotations
import pandas as pd
import streamlit as st


def _money(v):
    try:
        return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return "R$ 0,00"


def _best_rows(df: pd.DataFrame) -> pd.DataFrame:
    base = df.copy()
    base['preco_sem_imposto'] = pd.to_numeric(base['preco_sem_imposto'], errors='coerce').fillna(0)
    base['estoque'] = pd.to_numeric(base['estoque'], errors='coerce').fillna(0)
    base = base[base['estoque'] > 0]
    if base.empty:
        return base
    return base.sort_values(['cnpj_pdv', 'score_sugestao', 'preco_sem_imposto'], ascending=[True, False, True]).drop_duplicates(['cnpj_pdv', 'ean'])


def render_produtos(score_df: pd.DataFrame, oportunidades: pd.DataFrame, inventario: pd.DataFrame, cidade: str = 'Todas'):
    st.markdown('<h2 class="page-title">Produtos sugeridos</h2>', unsafe_allow_html=True)
    df = oportunidades.copy()
    cli = score_df.copy()
    if cidade != 'Todas':
        df = df[df['cidade'] == cidade]
        cli = cli[cli['cidade'] == cidade]
    if df.empty:
        st.info('Sem oportunidades calculadas.')
        return

    cli_ref = cli[['nome_fantasia', 'cnpj']].drop_duplicates().sort_values('nome_fantasia').copy()
    cli_ref['label'] = cli_ref['nome_fantasia'] + ' — ' + cli_ref['cnpj'].astype(str)
    clientes = ['Todos'] + cli_ref['label'].tolist()
    cliente = st.selectbox('Cliente', clientes)
    if cliente != 'Todos':
        cnpj = cli_ref.loc[cli_ref['label'] == cliente, 'cnpj'].iloc[0]
        df = df[df['cnpj_pdv'] == cnpj]

    show = _best_rows(df)
    if show.empty:
        st.info('Sem produtos com estoque para os filtros atuais.')
        return

    exibir = show[['nome_fantasia', 'cnpj_pdv', 'principio_ativo', 'mix_lancamentos', 'distribuidora', 'preco_sem_imposto']].copy()
    exibir.rename(columns={
        'nome_fantasia': 'Cliente', 'cnpj_pdv': 'CNPJ', 'principio_ativo': 'Produto', 'mix_lancamentos': 'Mix',
        'distribuidora': 'Distribuidora', 'preco_sem_imposto': 'Melhor preço'
    }, inplace=True)
    exibir['Melhor preço'] = exibir['Melhor preço'].map(_money)
    st.dataframe(exibir[['Cliente', 'CNPJ', 'Produto', 'Mix', 'Melhor preço', 'Distribuidora']], use_container_width=True, hide_index=True)
    st.caption('Mostra a melhor distribuidora inicial por produto. Para alterar distribuidora e quantidade, use a página Montar pedido.')
