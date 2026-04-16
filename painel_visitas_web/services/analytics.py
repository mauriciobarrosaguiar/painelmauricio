from __future__ import annotations
import pandas as pd
from config import STATUS_VALIDOS_FATURAMENTO

CLASS_PRIORITY = {
    'PRIORITARIOS': 4,
    'LANCAMENTOS': 3,
    'LINHA': 1,
    'COMBATE': 0,
}


def enrich_pedidos(pedidos: pd.DataFrame, produtos: pd.DataFrame, clientes: pd.DataFrame) -> pd.DataFrame:
    base = pedidos.copy()
    prod = produtos.copy()
    cli = clientes.copy()
    base = base.merge(prod, on='ean', how='left')
    base = base.merge(cli, left_on='cnpj_pdv', right_on='cnpj', how='left', suffixes=('', '_cli'))
    base['ano_mes'] = base['data_do_pedido'].dt.to_period('M').astype(str)
    base['status_pedido'] = base['status_pedido'].astype(str).str.upper().str.strip()
    base['mix_lancamentos'] = base['mix_lancamentos'].fillna('').astype(str).str.upper().str.strip()
    base['principio_ativo'] = base['principio_ativo'].fillna(base.get('produto', '')).astype(str)
    return base


def build_cliente_resumo(base_filtrada: pd.DataFrame, base_full: pd.DataFrame, clientes: pd.DataFrame) -> pd.DataFrame:
    faturados = base_filtrada[base_filtrada['status_pedido'].isin([s.upper() for s in STATUS_VALIDOS_FATURAMENTO])].copy()
    faturados_hist = base_full[base_full['status_pedido'].isin([s.upper() for s in STATUS_VALIDOS_FATURAMENTO])].copy()

    hoje_ref = pd.Timestamp.today().normalize()
    mes_atual = faturados['ano_mes'].max() if not faturados.empty else (faturados_hist['ano_mes'].max() if not faturados_hist.empty else None)
    mes_anterior = (pd.Period(mes_atual) - 1).strftime('%Y-%m') if mes_atual else None

    cadastro = clientes[['cnpj', 'nome_fantasia', 'cidade', 'uf', 'nome_contato', 'contato', 'telefone_limpo', 'endereco', 'bairro']].drop_duplicates('cnpj').set_index('cnpj')
    resumo = cadastro.copy()
    resumo['ultima_compra'] = faturados_hist.groupby('cnpj_pdv')['data_do_pedido'].max() if not faturados_hist.empty else pd.Series(dtype='datetime64[ns]')
    resumo['dias_sem_compra'] = (hoje_ref - resumo['ultima_compra']).dt.days.fillna(999)
    resumo['mix_comprado_total'] = faturados_hist.groupby('cnpj_pdv')['ean'].nunique() if not faturados_hist.empty else 0
    resumo['qtd_pedidos'] = faturados.groupby('cnpj_pdv')['pedido_id'].nunique() if not faturados.empty else 0
    resumo['total_faturado'] = faturados.groupby('cnpj_pdv')['valor_faturado'].sum() if not faturados.empty else 0
    resumo['comprou_mes_atual'] = faturados[faturados['ano_mes'] == mes_atual].groupby('cnpj_pdv')['pedido_id'].nunique().gt(0) if (not faturados.empty and mes_atual) else False

    resumo['ol_sem_combate'] = faturados[faturados['mix_lancamentos'] != 'COMBATE'].groupby('cnpj_pdv')['valor_faturado'].sum() if not faturados.empty else 0
    resumo['ol_combate'] = faturados[faturados['mix_lancamentos'] == 'COMBATE'].groupby('cnpj_pdv')['valor_faturado'].sum() if not faturados.empty else 0
    resumo['ol_prioritarios'] = faturados[faturados['mix_lancamentos'] == 'PRIORITARIOS'].groupby('cnpj_pdv')['valor_faturado'].sum() if not faturados.empty else 0
    resumo['ol_lancamentos'] = faturados[faturados['mix_lancamentos'] == 'LANCAMENTOS'].groupby('cnpj_pdv')['valor_faturado'].sum() if not faturados.empty else 0

    m_atual = faturados[faturados['ano_mes'] == mes_atual].groupby('cnpj_pdv')['valor_faturado'].sum().rename('venda_mes_atual').reset_index() if (not faturados.empty and mes_atual) else pd.DataFrame(columns=['cnpj_pdv', 'venda_mes_atual'])
    m_ant = faturados[faturados['ano_mes'] == mes_anterior].groupby('cnpj_pdv')['valor_faturado'].sum().rename('venda_mes_anterior').reset_index() if (not faturados.empty and mes_anterior) else pd.DataFrame(columns=['cnpj_pdv', 'venda_mes_anterior'])

    resumo = resumo.reset_index().rename(columns={'cnpj_pdv': 'cnpj'})
    resumo = resumo.merge(m_atual, left_on='cnpj', right_on='cnpj_pdv', how='left').drop(columns='cnpj_pdv')
    resumo = resumo.merge(m_ant, left_on='cnpj', right_on='cnpj_pdv', how='left').drop(columns='cnpj_pdv')
    cols_fill = ['venda_mes_atual', 'venda_mes_anterior', 'ol_sem_combate', 'ol_combate', 'ol_prioritarios', 'ol_lancamentos', 'total_faturado']
    resumo[cols_fill] = resumo[cols_fill].fillna(0)
    resumo['comprou_mes_atual'] = resumo['comprou_mes_atual'].fillna(False)
    resumo['comprou_mes_atual_label'] = resumo['comprou_mes_atual'].map({True: 'Sim', False: 'Não'})
    resumo['variacao_percentual'] = ((resumo['venda_mes_atual'] - resumo['venda_mes_anterior']) / resumo['venda_mes_anterior'].replace(0, pd.NA)).fillna(0)
    resumo['teve_venda_prioritarios'] = resumo['ol_prioritarios'].gt(0)
    resumo['teve_venda_lancamentos'] = resumo['ol_lancamentos'].gt(0)
    resumo['percentual_prioritarios_ol'] = (resumo['ol_prioritarios'] / resumo['ol_sem_combate'].replace(0, pd.NA)).fillna(0)
    resumo['percentual_lancamentos_ol'] = (resumo['ol_lancamentos'] / resumo['ol_sem_combate'].replace(0, pd.NA)).fillna(0)

    if resumo['total_faturado'].notna().sum() >= 3:
        resumo['potencial_categoria'] = pd.qcut(resumo['total_faturado'].rank(method='first'), q=3, labels=['Baixo', 'Medio', 'Alto']).astype(str)
    else:
        resumo['potencial_categoria'] = 'Medio'
    return resumo


def build_gap_por_cliente(base: pd.DataFrame, produtos: pd.DataFrame) -> pd.DataFrame:
    prod = produtos.copy()
    prod = prod[prod['mix_lancamentos'].isin(['PRIORITARIOS', 'LANCAMENTOS', 'LINHA'])]
    mix_total = prod.groupby('mix_lancamentos')['ean'].nunique().to_dict()
    faturados = base[base['status_pedido'].isin([s.upper() for s in STATUS_VALIDOS_FATURAMENTO])].copy()
    comprados = faturados.groupby(['cnpj_pdv', 'mix_lancamentos'])['ean'].nunique().reset_index(name='mix_cliente')
    comprados['mix_total_categoria'] = comprados['mix_lancamentos'].map(mix_total).fillna(0)
    comprados['gap_categoria'] = (comprados['mix_total_categoria'] - comprados['mix_cliente']).clip(lower=0)
    return comprados


def build_oportunidades_cliente(base: pd.DataFrame, produtos: pd.DataFrame, foco_semana: pd.DataFrame | None = None, inventario: pd.DataFrame | None = None, distrib_pref_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Retorna catálogo global de oportunidades (1 linha por EAN no melhor preço), para reduzir custo de processamento."""
    faturados = base[base['status_pedido'].isin([s.upper() for s in STATUS_VALIDOS_FATURAMENTO])].copy()
    if faturados.empty:
        return pd.DataFrame()
    ref_date = faturados['data_do_pedido'].max()
    ult_mes = faturados[faturados['data_do_pedido'].dt.to_period('M') == ref_date.to_period('M')].copy()

    ranking = ult_mes.groupby(['ean', 'principio_ativo', 'mix_lancamentos'], as_index=False).agg(
        valor_mes=('valor_faturado', 'sum'),
        qtd_mes=('quantidade_faturada', 'sum'),
        clientes_mes=('cnpj_pdv', 'nunique')
    )

    universo = produtos[produtos['mix_lancamentos'].isin(['PRIORITARIOS', 'LANCAMENTOS', 'LINHA', 'COMBATE'])][['ean', 'principio_ativo', 'mix_lancamentos']].drop_duplicates()
    ranking = universo.merge(ranking, on=['ean', 'principio_ativo', 'mix_lancamentos'], how='left').fillna({'valor_mes': 0, 'qtd_mes': 0, 'clientes_mes': 0})
    ranking['peso_categoria'] = ranking['mix_lancamentos'].map(CLASS_PRIORITY).fillna(0)

    foco = foco_semana.copy() if foco_semana is not None else pd.DataFrame(columns=['ean', 'peso_foco', 'observacao'])
    if 'peso_foco' not in foco.columns:
        foco['peso_foco'] = 1
    ranking = ranking.merge(foco[['ean', 'peso_foco', 'observacao']].drop_duplicates(), on='ean', how='left')
    ranking['peso_foco'] = ranking['peso_foco'].fillna(0)
    ranking['observacao'] = ranking['observacao'].fillna('')

    if inventario is not None and not inventario.empty:
        inv = inventario.copy()
        inv['preco_sem_imposto'] = pd.to_numeric(inv['preco_sem_imposto'], errors='coerce').fillna(0)
        inv['estoque'] = pd.to_numeric(inv['estoque'], errors='coerce').fillna(0)
        inv = inv[inv['estoque'] > 0].copy()
        inv = inv.sort_values(['ean', 'preco_sem_imposto', 'estoque'], ascending=[True, True, False])
        best_inv = inv.drop_duplicates('ean')
        ranking = ranking.merge(best_inv[['ean', 'distribuidora', 'estoque', 'preco_sem_imposto', 'preco_com_imposto']], on='ean', how='left')
    else:
        ranking['distribuidora'] = ''
        ranking['estoque'] = 0
        ranking['preco_sem_imposto'] = 0
        ranking['preco_com_imposto'] = 0

    valor_mes_max = max(pd.to_numeric(ranking['valor_mes'], errors='coerce').fillna(0).max(), 1)
    clientes_mes_max = max(pd.to_numeric(ranking['clientes_mes'], errors='coerce').fillna(0).max(), 1)
    ranking['score_sugestao'] = (
        ranking['peso_categoria'] * 25
        + ranking['peso_foco'] * 30
        + (pd.to_numeric(ranking['valor_mes'], errors='coerce').fillna(0) / valor_mes_max) * 30
        + (pd.to_numeric(ranking['clientes_mes'], errors='coerce').fillna(0) / clientes_mes_max) * 10
        + pd.to_numeric(ranking['estoque'], errors='coerce').fillna(0).gt(0).astype(int) * 5
        - pd.to_numeric(ranking['preco_sem_imposto'], errors='coerce').fillna(0).rank(pct=True) * 10
    ).round(2)

    return ranking.sort_values(['score_sugestao', 'preco_sem_imposto'], ascending=[False, True]).reset_index(drop=True)


def build_cancelados_cliente(base: pd.DataFrame) -> pd.DataFrame:
    canc = base[base['quantidade_cancelada'].fillna(0) > 0].copy()
    if canc.empty:
        return pd.DataFrame(columns=['cnpj_pdv', 'data_do_pedido', 'produto', 'quantidade_cancelada'])
    return canc[['cnpj_pdv', 'data_do_pedido', 'produto', 'quantidade_cancelada']].sort_values(['cnpj_pdv', 'data_do_pedido'], ascending=[True, False])
