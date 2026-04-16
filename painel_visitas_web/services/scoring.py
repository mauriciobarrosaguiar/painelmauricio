from __future__ import annotations

import pandas as pd
from config import PESOS_SCORE


def _series_from_gap(gap: pd.DataFrame, filtro_mix: str | None, nome: str) -> pd.Series:
    if gap is None or gap.empty or 'cnpj_pdv' not in gap.columns:
        return pd.Series(name=nome, dtype=float)

    base = gap.copy()
    if 'gap_categoria' not in base.columns:
        base['gap_categoria'] = 0

    if filtro_mix:
        if 'mix_lancamentos' not in base.columns:
            return pd.Series(name=nome, dtype=float)
        base = base[base['mix_lancamentos'].astype(str).str.upper().str.strip() == filtro_mix]
        if base.empty:
            return pd.Series(name=nome, dtype=float)
        serie = base.groupby('cnpj_pdv')['gap_categoria'].sum()
    else:
        serie = base.groupby('cnpj_pdv')['gap_categoria'].sum()

    return pd.to_numeric(serie, errors='coerce').fillna(0).rename(nome)


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    defaults = {
        'cnpj': '',
        'total_faturado': 0.0,
        'potencial_categoria': 'Medio',
        'variacao_percentual': 0.0,
        'dias_sem_compra': 999,
        'comprou_mes_atual': False,
        'teve_venda_prioritarios': False,
        'teve_venda_lancamentos': False,
        'venda_mes_atual': 0.0,
        'venda_mes_anterior': 0.0,
        'ol_sem_combate': 0.0,
        'ol_prioritarios': 0.0,
        'ol_lancamentos': 0.0,
    }

    for col, default in defaults.items():
        if col not in out.columns:
            out[col] = default

    num_cols = [
        'total_faturado',
        'variacao_percentual',
        'dias_sem_compra',
        'venda_mes_atual',
        'venda_mes_anterior',
        'ol_sem_combate',
        'ol_prioritarios',
        'ol_lancamentos',
    ]
    for col in num_cols:
        out[col] = pd.to_numeric(out[col], errors='coerce').fillna(0)

    bool_cols = ['comprou_mes_atual', 'teve_venda_prioritarios', 'teve_venda_lancamentos']
    for col in bool_cols:
        out[col] = out[col].fillna(False).astype(bool)

    out['potencial_categoria'] = out['potencial_categoria'].fillna('Medio').astype(str)
    return out


def score_clientes(resumo: pd.DataFrame, gap: pd.DataFrame) -> pd.DataFrame:
    if resumo is None or resumo.empty:
        return pd.DataFrame(columns=list(resumo.columns) + ['gap_total', 'gap_prioritarios', 'gap_lancamentos', 'flag_queda', 'flag_sem_compra_mes', 'flag_sem_prioritario', 'flag_sem_lancamento', 'score_visita', 'motivo_principal'] if isinstance(resumo, pd.DataFrame) else [])

    df = _ensure_columns(resumo)

    gap_total = _series_from_gap(gap, None, 'gap_total')
    prioritario_gap = _series_from_gap(gap, 'PRIORITARIOS', 'gap_prioritarios')
    lancamento_gap = _series_from_gap(gap, 'LANCAMENTOS', 'gap_lancamentos')

    df = df.merge(gap_total, left_on='cnpj', right_index=True, how='left')
    df = df.merge(prioritario_gap, left_on='cnpj', right_index=True, how='left')
    df = df.merge(lancamento_gap, left_on='cnpj', right_index=True, how='left')

    for c in ['gap_total', 'gap_prioritarios', 'gap_lancamentos']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    potencial_peso = df['potencial_categoria'].str.upper().map({'BAIXO': 5, 'MEDIO': 12, 'ALTO': 20}).fillna(10)

    df['flag_queda'] = df['variacao_percentual'].lt(0).astype(int)
    df['flag_sem_compra_mes'] = (~df['comprou_mes_atual']).astype(int)
    df['flag_sem_prioritario'] = (~df['teve_venda_prioritarios']).astype(int)
    df['flag_sem_lancamento'] = (~df['teve_venda_lancamentos']).astype(int)

    dias_sem_compra = pd.to_numeric(df['dias_sem_compra'], errors='coerce').fillna(999).clip(0, 60)

    df['score_visita'] = (
        dias_sem_compra * PESOS_SCORE['dias_sem_compra']
        + df['flag_queda'] * PESOS_SCORE['queda_faturamento']
        + df['flag_sem_prioritario'] * PESOS_SCORE['nao_compra_prioritario']
        + df['flag_sem_lancamento'] * PESOS_SCORE['nao_compra_lancamento']
        + df['gap_total'] * PESOS_SCORE['gap_mix']
        + potencial_peso
        + df['flag_sem_compra_mes'] * PESOS_SCORE['sem_compra_mes_atual']
    ).round(1)

    def motivo(r):
        partes = []
        if r['flag_sem_compra_mes']:
            partes.append('sem venda no mês')
        if r['flag_sem_prioritario']:
            partes.append('sem prioritários')
        if r['flag_sem_lancamento']:
            partes.append('sem lançamentos')
        if r['flag_queda']:
            partes.append('queda vs mês anterior')
        if float(r.get('dias_sem_compra', 0) or 0) > 15:
            partes.append(f"{int(r['dias_sem_compra'])} dias sem compra")
        return ' | '.join(partes[:4]) if partes else 'cliente com oportunidade de expansão'

    df['motivo_principal'] = df.apply(motivo, axis=1)
    return df.sort_values(['score_visita', 'total_faturado'], ascending=[False, False]).reset_index(drop=True)
