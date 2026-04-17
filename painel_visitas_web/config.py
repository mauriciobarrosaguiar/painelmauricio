from pathlib import Path
import re

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

def _normalizar(texto: str) -> str:
    texto = str(texto)
    substituicoes = {
        'á':'a','à':'a','â':'a','ã':'a','ä':'a',
        'é':'e','è':'e','ê':'e','ë':'e',
        'í':'i','ì':'i','î':'i','ï':'i',
        'ó':'o','ò':'o','ô':'o','õ':'o','ö':'o',
        'ú':'u','ù':'u','û':'u','ü':'u',
        'ç':'c',
        'Á':'a','À':'a','Â':'a','Ã':'a','Ä':'a',
        'É':'e','È':'e','Ê':'e','Ë':'e',
        'Í':'i','Ì':'i','Î':'i','Ï':'i',
        'Ó':'o','Ò':'o','Ô':'o','Õ':'o','Ö':'o',
        'Ú':'u','Ù':'u','Û':'u','Ü':'u',
        'Ç':'c',
    }
    for a, b in substituicoes.items():
        texto = texto.replace(a, b)
    texto = texto.lower()
    texto = re.sub(r'[^a-z0-9]+', ' ', texto)
    return texto.strip()

def localizar_arquivo(*palavras_chave: str, required: bool = True) -> Path | None:
    candidatos = sorted(DATA_DIR.glob('*.xlsx')) + sorted(DATA_DIR.glob('*.xls')) + sorted(DATA_DIR.glob('*.xlsm'))
    if not candidatos:
        if required:
            raise FileNotFoundError(f'Nenhuma planilha encontrada em {DATA_DIR}')
        return None
    chaves = [_normalizar(p) for p in palavras_chave]
    for arq in candidatos:
        nome = _normalizar(arq.name)
        if all(ch in nome for ch in chaves):
            return arq
    if required:
        raise FileNotFoundError(
            'Não encontrei a planilha esperada em data/. '
            f'Arquivos encontrados: {[a.name for a in candidatos]}'
        )
    return None

PEDIDOS_FILE = localizar_arquivo('pedidos')
PRODUTOS_FILE = localizar_arquivo('produtos', 'ean')
CLIENTES_FILE = localizar_arquivo('painel')
FOCO_SEMANA_FILE = localizar_arquivo('foco', required=False)
INVENTARIO_FILE = localizar_arquivo('estoque', 'distribuidora', required=False) or localizar_arquivo('estoque', required=False) or localizar_arquivo('preco', required=False)

STATUS_VALIDOS_FATURAMENTO = ['Faturado', 'Faturado parcial']

PESOS_SCORE = {
    'dias_sem_compra': 0.45,
    'queda_faturamento': 22,
    'nao_compra_prioritario': 20,
    'nao_compra_lancamento': 16,
    'gap_mix': 1.0,
    'potencial_carteira': 20,
    'sem_compra_mes_atual': 20,
}

COR_FUNDO = '#F8F6ED'
COR_CARD = '#FFFFFF'
COR_BORDA = '#D8E2D9'
COR_PRIMARIA = '#0F3B2B'
COR_SECUNDARIA = '#2D7A55'
COR_AZUL = '#2D7A55'
COR_VERDE = '#2D7A55'
COR_LARANJA = '#D9A441'
COR_VERMELHO = '#C95C42'
COR_TEXTO = '#14251C'
