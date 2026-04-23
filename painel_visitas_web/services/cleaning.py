from __future__ import annotations
import re
import unicodedata

import pandas as pd

def _legacy_slug(texto: str) -> str:
    texto = str(texto).strip().lower()
    rep = {
        'ç':'c','ã':'a','á':'a','à':'a','â':'a','é':'e','ê':'e','í':'i','ó':'o','ô':'o','õ':'o','ú':'u','ü':'u',
        '/':'_',' ':'_','-':'_','(':'',')':'', '%':'percent', '.':'', ':':'', 'º':'o'
    }
    for a, b in rep.items():
        texto = texto.replace(a, b)
    while '__' in texto:
        texto = texto.replace('__', '_')
    return texto.strip('_')


def _strip_accents(texto: str) -> str:
    texto = str(texto)
    substituicoes = {
        'Ã§': 'c', 'Ã£': 'a', 'Ã¡': 'a', 'Ã ': 'a', 'Ã¢': 'a',
        'Ã©': 'e', 'Ãª': 'e', 'Ã­': 'i', 'Ã³': 'o', 'Ã´': 'o',
        'Ãµ': 'o', 'Ãº': 'u', 'Ã¼': 'u', 'Ã‡': 'c',
        'Ã': 'a', 'Ã€': 'a', 'Ã‚': 'a', 'Ãƒ': 'a', 'Ã‰': 'e',
        'ÃŠ': 'e', 'Ã': 'i', 'Ã“': 'o', 'Ã”': 'o', 'Ã•': 'o',
        'Ãš': 'u',
    }
    for old, new in substituicoes.items():
        texto = texto.replace(old, new)
    texto = unicodedata.normalize('NFKD', texto)
    return ''.join(ch for ch in texto if not unicodedata.combining(ch))


def slug(texto: str) -> str:
    texto = _strip_accents(texto).strip().lower().replace('%', ' percent ')
    texto = texto.replace('Âº', 'o')
    texto = re.sub(r'[^a-z0-9]+', '_', texto)
    return texto.strip('_')


def normalize_mix_value(value) -> str:
    texto = _strip_accents(value).upper().strip()
    texto = re.sub(r'\s+', ' ', texto)
    if texto in {'', 'NAN', 'NONE', '<NA>'}:
        return 'LINHA'
    if 'PRIORIT' in texto or 'PRIOTIR' in texto:
        return 'PRIORITARIOS'
    if 'LANC' in texto:
        return 'LANCAMENTOS'
    if 'COMBATE' in texto:
        return 'COMBATE'
    if 'LINHA' in texto:
        return 'LINHA'
    return texto

def normalize_cnpj(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace(r'\D', '', regex=True).str.zfill(14)

def normalize_ean(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace(r'\.0$', '', regex=True).str.replace(r'\D', '', regex=True).str.strip()

def normalize_phone(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace(r'\D', '', regex=True).replace({'nan':'', 'None':'', '<NA>':''})

def br_to_float(series: pd.Series) -> pd.Series:
    def conv(v):
        if pd.isna(v):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace('R$', '').replace(' ', '')
        if s in {'', 'nan', 'None', '<NA>'}:
            return None
        if ',' in s and '.' in s:
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '.')
        return pd.to_numeric(s, errors='coerce')
    return series.apply(conv)

def normalize_percent(series: pd.Series) -> pd.Series:
    vals = br_to_float(series).fillna(0)
    return vals.apply(lambda x: float(x) * 100 if float(x) <= 1 else float(x))

def _padronizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [slug(c) for c in df.columns]
    return df

def clean_pedidos(df: pd.DataFrame) -> pd.DataFrame:
    df = _padronizar_colunas(df)
    df['cnpj_pdv'] = normalize_cnpj(df['cnpj_pdv'])
    df['ean'] = normalize_ean(df['ean'])
    for col in [
        'preco_unitario_com_imposto', 'preco_unitario_sem_imposto', 'desconto_digitado',
        'desconto_aplicado_em_nota', 'valor_total_solicitado_com_imposto',
        'valor_total_solicitado_sem_imposto', 'total_atendido_sem_imposto',
        'total_atendido_com_imposto', 'valor_faturado'
    ]:
        if col in df.columns:
            df[col] = br_to_float(df[col]).fillna(0)
    for col in ['quantidade_solicitada', 'quantidade_atendida', 'quantidade_faturada', 'quantidade_cancelada']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    for col in ['data_do_pedido', 'data_de_faturamento']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')
    return df

def clean_produtos(df: pd.DataFrame) -> pd.DataFrame:
    df = _padronizar_colunas(df)
    rename_map = {
        'nome_do_produto': 'principio_ativo',
        'nome_produto': 'principio_ativo',
        'descricao': 'principio_ativo',
        'principio': 'principio_ativo',
        'principio_ativo': 'principio_ativo',
        'produto': 'principio_ativo',
        'mix': 'mix_lancamentos',
        'mix_lancamentos': 'mix_lancamentos',
        'mix_lancamento': 'mix_lancamentos',
        'linha_combate_priotirarios_lancamentos': 'mix_lancamentos',
        'linha_combate_prioritarios_lancamentos': 'mix_lancamentos',
        'linha_combate_prioritario_lancamentos': 'mix_lancamentos',
        'linha_combate_lancamentos_prioritarios': 'mix_lancamentos',
        'molecula': 'principio_ativo',
    }
    for old, new in rename_map.items():
        if old in df.columns and new not in df.columns:
            df.rename(columns={old: new}, inplace=True)
    if 'ean' not in df.columns:
        df['ean'] = ''
    if 'principio_ativo' not in df.columns:
        df['principio_ativo'] = ''
    if 'mix_lancamentos' not in df.columns:
        df['mix_lancamentos'] = 'LINHA'
    df['ean'] = normalize_ean(df['ean'])
    df['principio_ativo'] = df['principio_ativo'].astype(str).str.strip()
    df['mix_lancamentos'] = df['mix_lancamentos'].astype(str).str.upper().str.strip()
    df['mix_lancamentos'] = df['mix_lancamentos'].replace({'PRIORITÁRIOS':'PRIORITARIOS', 'LANÇAMENTO':'LANCAMENTOS', 'LANCAMENTO':'LANCAMENTOS'})
    df['mix_lancamentos'] = df['mix_lancamentos'].apply(normalize_mix_value)
    df = df[df['ean'].ne('') | df['principio_ativo'].ne('')].copy()
    return df[['ean', 'principio_ativo', 'mix_lancamentos']].drop_duplicates()

def clean_clientes(df: pd.DataFrame) -> pd.DataFrame:
    df = _padronizar_colunas(df)
    if 'cnpj' not in df.columns:
        raise ValueError('A planilha PAINEL precisa ter a coluna CNPJ.')
    df['cnpj'] = normalize_cnpj(df['cnpj'])
    for col in ['contato', 'cep', 'cidade', 'uf', 'nome_contato', 'nome_fantasia', 'razao_social', 'endereco', 'bairro']:
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].astype(str).replace('nan', '')
    df['telefone_limpo'] = normalize_phone(df['contato'])
    return df

def clean_foco_semana(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=['ean', 'principio_ativo', 'peso_foco', 'observacao'])
    df = _padronizar_colunas(df)
    if 'nome_do_produto' in df.columns and 'principio_ativo' not in df.columns:
        df.rename(columns={'nome_do_produto': 'principio_ativo'}, inplace=True)
    if 'produto' in df.columns and 'principio_ativo' not in df.columns:
        df.rename(columns={'produto': 'principio_ativo'}, inplace=True)
    for col in ['ean', 'principio_ativo', 'observacao']:
        if col not in df.columns:
            df[col] = ''
    if 'peso_foco' not in df.columns:
        df['peso_foco'] = 1
    df['ean'] = normalize_ean(df['ean'])
    df['peso_foco'] = pd.to_numeric(df['peso_foco'], errors='coerce').fillna(1)
    return df[['ean', 'principio_ativo', 'peso_foco', 'observacao']]

def clean_inventario(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=['ean', 'principio_ativo', 'mix_lancamentos', 'estoque', 'distribuidora', 'preco_sem_imposto', 'preco_com_imposto', 'data', 'desconto', 'pf_dist', 'pf_fabrica'])
    df = df.copy()
    if all(str(c).startswith('Unnamed') for c in df.columns):
        header = df.iloc[0].tolist()
        df = df.iloc[1:].copy()
        df.columns = header
    df = _padronizar_colunas(df)
    ren = {
        'nome_do_produto': 'principio_ativo',
        'produto': 'principio_ativo',
        'mix': 'mix_lancamentos',
        'preco_final': 'preco_com_imposto',
        'preco_final_r': 'preco_com_imposto',
        'preco_final_r_': 'preco_com_imposto',
        'preco_final_r$': 'preco_com_imposto',
        'sem_imposto_r': 'preco_sem_imposto',
        'sem_imposto': 'preco_sem_imposto',
        'sem_imposto_r_': 'preco_sem_imposto',
        'sem_imposto_r$': 'preco_sem_imposto',
        'pf_dist_r': 'pf_dist',
        'pf_dist_r$': 'pf_dist',
        'pf_fabrica_r': 'pf_fabrica',
        'pf_fabrica_r$': 'pf_fabrica',
        'pf_fabrica': 'pf_fabrica',
        'preco_fabrica': 'pf_fabrica',
        'desconto_percent': 'desconto',
        'desconto_percent_': 'desconto',
        'desconto_percentual': 'desconto',
        'desconto': 'desconto',
    }
    for old, new in ren.items():
        if old in df.columns and new not in df.columns:
            df.rename(columns={old: new}, inplace=True)
    for col in ['ean', 'principio_ativo', 'mix_lancamentos', 'estoque', 'distribuidora', 'preco_sem_imposto', 'preco_com_imposto', 'data', 'desconto', 'pf_dist', 'pf_fabrica']:
        if col not in df.columns:
            df[col] = '' if col not in {'estoque', 'preco_sem_imposto', 'preco_com_imposto', 'desconto', 'pf_dist', 'pf_fabrica'} else 0
    df['ean'] = normalize_ean(df['ean'])
    df['principio_ativo'] = df['principio_ativo'].astype(str).str.strip()
    df['mix_lancamentos'] = df['mix_lancamentos'].astype(str).str.upper().str.strip().replace({'PRIORITÁRIOS':'PRIORITARIOS', 'LANÇAMENTO':'LANCAMENTOS', 'LANCAMENTO':'LANCAMENTOS', 'NAN': ''})
    df['estoque'] = pd.to_numeric(df['estoque'], errors='coerce').fillna(0)
    df['preco_sem_imposto'] = br_to_float(df['preco_sem_imposto']).fillna(0)
    df['preco_com_imposto'] = br_to_float(df['preco_com_imposto']).fillna(0)
    df['desconto'] = normalize_percent(df['desconto']).fillna(0)
    desconto_decimal = (df['desconto'] / 100).clip(lower=0, upper=0.9999)
    pf_lido = br_to_float(df['pf_dist']).fillna(0)
    df['pf_fabrica'] = br_to_float(df['pf_fabrica']).fillna(0).round(2)
    base_sem = (df['preco_sem_imposto'] / (1 - desconto_decimal)).replace([pd.NA, pd.NaT], 0)
    base_com = (df['preco_com_imposto'] / (1 - desconto_decimal)).replace([pd.NA, pd.NaT], 0)
    pf_calc = base_sem.where(base_sem > 0, base_com)
    df['pf_dist'] = pf_lido.where(pf_lido > 0, pf_calc).fillna(0).round(2)
    df['data'] = pd.to_datetime(df['data'], errors='coerce', dayfirst=True)
    return df[['ean', 'principio_ativo', 'mix_lancamentos', 'estoque', 'distribuidora', 'preco_sem_imposto', 'preco_com_imposto', 'data', 'desconto', 'pf_dist', 'pf_fabrica']]
