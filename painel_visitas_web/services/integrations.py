from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from config import DATA_DIR
from services.mercadofarma_inventory import (
    build_not_found_row,
    login_mercadofarma as mf_login_catalogo,
    processar_ean_catalogo,
    selecionar_cnpj_catalogo,
)

CRED_FILE = DATA_DIR / 'credenciais_integracao.json'
CONFIG_INI_FILE = DATA_DIR / 'integracoes.ini'
LOG_FILE = DATA_DIR / 'integracoes.log'
BUSSOLA_URL = 'https://bussolaweb.bussola.mercadofarma.com.br/login'


@dataclass
class IntegracaoCreds:
    bussola_login: str = ''
    bussola_senha: str = ''
    mercado_login: str = ''
    mercado_senha: str = ''
    mercado_cnpj: str = ''


def _log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(f'[{stamp}] {msg}\n')


def _notify(
    status_cb: Optional[Callable[..., None]] = None,
    *,
    status: str = 'executando',
    mensagem: str = '',
    etapa: str = '',
    atual: int | None = None,
    total: int | None = None,
    erro: str = '',
    resumo: dict | None = None,
    nivel: str = 'info',
):
    if not callable(status_cb):
        return
    payload = {
        'status': status,
        'mensagem': mensagem,
        'etapa': etapa,
        'atual': atual,
        'total': total,
        'erro': erro,
        'resumo': resumo or {},
        'nivel': nivel,
    }
    try:
        status_cb(**payload)
    except TypeError:
        status_cb(payload)




def _cleanup_old_bussola_files(data_dir: Path, download_dir: Path):
    # Remove arquivos temporários/antigos para o repositório não crescer sem necessidade.
    for p in data_dir.glob("bruto_Pedidos_*.csv"):
        try:
            p.unlink()
        except Exception:
            pass
    if download_dir.exists():
        for p in download_dir.glob("*"):
            try:
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

def read_last_logs(limit: int = 80) -> str:
    if not LOG_FILE.exists():
        return ''
    lines = LOG_FILE.read_text(encoding='utf-8', errors='ignore').splitlines()
    return '\n'.join(lines[-limit:])


def _clean_cnpj(cnpj: str) -> str:
    return re.sub(r'\D', '', str(cnpj or ''))


def load_creds() -> IntegracaoCreds:
    data = {}
    if CRED_FILE.exists():
        try:
            data.update(json.loads(CRED_FILE.read_text(encoding='utf-8')))
        except Exception:
            pass
    if CONFIG_INI_FILE.exists():
        cfg = configparser.ConfigParser()
        cfg.read(CONFIG_INI_FILE, encoding='utf-8')
        if cfg.has_section('integracoes'):
            data.update(dict(cfg.items('integracoes')))
    env_map = {
        'bussola_login': os.getenv('BUSSOLA_LOGIN', ''),
        'bussola_senha': os.getenv('BUSSOLA_SENHA', ''),
        'mercado_login': os.getenv('MERCADOFARMA_LOGIN', ''),
        'mercado_senha': os.getenv('MERCADOFARMA_SENHA', ''),
        'mercado_cnpj': os.getenv('MERCADOFARMA_CNPJ', ''),
    }
    for k, v in env_map.items():
        if v:
            data[k] = v
    return IntegracaoCreds(
        bussola_login=data.get('bussola_login', ''),
        bussola_senha=data.get('bussola_senha', ''),
        mercado_login=data.get('mercado_login', ''),
        mercado_senha=data.get('mercado_senha', ''),
        mercado_cnpj=_clean_cnpj(data.get('mercado_cnpj', '')),
    )


def save_creds(creds: IntegracaoCreds):
    payload = {
        'bussola_login': creds.bussola_login,
        'bussola_senha': creds.bussola_senha,
        'mercado_login': creds.mercado_login,
        'mercado_senha': creds.mercado_senha,
        'mercado_cnpj': _clean_cnpj(creds.mercado_cnpj),
    }
    CRED_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    cfg = configparser.ConfigParser()
    cfg['integracoes'] = payload
    with CONFIG_INI_FILE.open('w', encoding='utf-8') as f:
        cfg.write(f)
    _log('Credenciais salvas/atualizadas.')


def choose_low_production_cnpj(score_df: pd.DataFrame) -> str:
    if score_df is None or score_df.empty:
        return ''
    base = score_df.copy()
    for col in ['venda_mes_atual', 'ol_sem_combate', 'score_visita']:
        if col not in base.columns:
            base[col] = 0
    if 'comprou_mes_atual' not in base.columns:
        base['comprou_mes_atual'] = False
    base = base.sort_values(
        ['comprou_mes_atual', 'venda_mes_atual', 'ol_sem_combate', 'score_visita'],
        ascending=[True, True, True, False],
    )
    cnpj = base['cnpj'].astype(str).iloc[0]
    return _clean_cnpj(cnpj)


def _make_driver(download_dir: Path, headless: bool = False) -> webdriver.Chrome:
    download_dir.mkdir(parents=True, exist_ok=True)
    prefs = {
        'download.default_directory': str(download_dir.resolve()),
        'download.prompt_for_download': False,
        'download.directory_upgrade': True,
        'safebrowsing.enabled': True,
        'profile.default_content_setting_values.automatic_downloads': 1,
    }
    options = webdriver.ChromeOptions()
    options.add_experimental_option('prefs', prefs)
    options.add_argument('--start-maximized')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-popup-blocking')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    if headless:
        options.add_argument('--headless=new')
        options.add_argument('--window-size=1920,1400')
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(90)
    return driver


def _cleanup_download_dir(download_dir: Path, suffixes: set[str]):
    download_dir.mkdir(parents=True, exist_ok=True)
    for p in download_dir.glob('*'):
        if p.is_file() and p.suffix.lower() in suffixes:
            try:
                p.unlink()
            except Exception:
                pass


def _wait_download(download_dir: Path, timeout: int = 180, allowed_suffixes: set[str] | None = None) -> Path:
    allowed_suffixes = allowed_suffixes or {'.xlsx', '.xls', '.csv'}
    start = time.time()
    while time.time() - start < timeout:
        files = [p for p in download_dir.iterdir() if p.is_file()]
        partial = [p for p in files if p.suffix.lower() in {'.crdownload', '.tmp', '.part'}]
        new_files = [p for p in files if p.suffix.lower() in allowed_suffixes]
        if new_files and not partial:
            return max(new_files, key=lambda p: p.stat().st_mtime)
        time.sleep(1)
    raise TimeoutError('Download não foi concluído dentro do tempo esperado.')


def _wait(driver, seconds=30):
    return WebDriverWait(driver, seconds)


def _js_click(driver, element):
    driver.execute_script('arguments[0].click();', element)


def _safe_click(driver, element):
    try:
        element.click()
    except Exception:
        _js_click(driver, element)


def _click_first(driver, _wait_obj_unused, xpaths: list[str], timeout: int = 10):
    last_error = None
    for xp in xpaths:
        try:
            elem = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xp)))
            _safe_click(driver, elem)
            return elem
        except Exception as e:
            last_error = e
    raise last_error or RuntimeError('Elemento não encontrado para clique.')


def _find_first(driver, xpaths: list[str], timeout: int = 10):
    last_error = None
    for xp in xpaths:
        try:
            return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.XPATH, xp)))
        except Exception as e:
            last_error = e
    raise last_error or RuntimeError('Elemento não encontrado.')


def _wait_visible(driver, xpaths: list[str], timeout: int = 25):
    last_error = None
    for xp in xpaths:
        try:
            return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located((By.XPATH, xp)))
        except Exception as e:
            last_error = e
    raise last_error or RuntimeError('Elemento visível não encontrado.')


def _safe_text(element, by: By, selector: str, default: str = '') -> str:
    try:
        return element.find_element(by, selector).text.strip()
    except Exception:
        return default


def _normalize_downloaded_tabular(path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    if path.suffix.lower() == '.csv':
        df = None
        for sep in [';', ',', '\t']:
            for enc in ['utf-8-sig', 'latin1', 'cp1252', 'utf-8']:
                try:
                    temp = pd.read_csv(path, sep=sep, encoding=enc, dtype=str)
                    if temp.shape[1] > 1:
                        df = temp
                        break
                except Exception:
                    continue
            if df is not None:
                break
        if df is None:
            df = pd.read_csv(path, sep=None, engine='python', dtype=str)
        df = df.dropna(axis=1, how='all')
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        return output_path
    shutil.copy2(path, output_path)
    return output_path


def _write_bussola_outputs(downloaded: Path, output_xlsx: Path) -> Path:
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    output_csv = output_xlsx.parent / 'Pedidos_bussola.csv'
    bruto_csv = output_xlsx.parent / f'bruto_{downloaded.name}'
    for p in [output_xlsx, output_csv, bruto_csv]:
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    df = None
    for sep in [';', ',', '\t']:
        for enc in ['utf-8-sig', 'latin1', 'cp1252', 'utf-8']:
            try:
                temp = pd.read_csv(downloaded, sep=sep, encoding=enc, dtype=str)
                if temp.shape[1] > 1:
                    df = temp
                    break
            except Exception:
                continue
        if df is not None:
            break
    if df is None:
        raise RuntimeError('Não consegui ler o CSV baixado do Bússola.')

    df = df.dropna(axis=1, how='all')
    df.to_csv(bruto_csv, index=False, sep=';', encoding='utf-8-sig')
    df.to_csv(output_csv, index=False, sep=';', encoding='utf-8-sig')
    with pd.ExcelWriter(output_xlsx, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)

    _log(f'Base Pedidos substituída por nova extração: {output_xlsx.name}')
    return output_xlsx


def _abrir_menu_exportar_bussola(driver):
    _log('Abrindo menu Exportar...')
    _click_first(driver, None, [
        "//button[contains(., 'Exportar')]",
        "//*[@data-slot='dropdown-menu-trigger' and contains(., 'Exportar')]",
    ], timeout=35)
    _wait(driver, 20).until(lambda d: len([el for el in d.find_elements(By.XPATH, "//div[@role='menuitem']") if el.is_displayed()]) >= 2)
    time.sleep(0.8)


def _selecionar_csv_bussola(driver):
    _log('Selecionando CSV no Bússola...')
    ultima_ex = None
    for tentativa in range(1, 6):
        try:
            _abrir_menu_exportar_bussola(driver)
            itens = [el for el in driver.find_elements(By.XPATH, "//div[@role='menuitem']") if el.is_displayed()]
            for item in itens:
                txt = (item.text or '').strip().lower()
                if 'csv' in txt:
                    _safe_click(driver, item)
                    time.sleep(1)
                    return
            menus = [el for el in driver.find_elements(By.XPATH, "//div[@role='menu' and @data-state='open']") if el.is_displayed()]
            if menus:
                itens2 = [el for el in menus[-1].find_elements(By.XPATH, ".//*[@role='menuitem']") if el.is_displayed()]
                if len(itens2) >= 2:
                    _safe_click(driver, itens2[1])
                    time.sleep(1)
                    return
            raise TimeoutException('Menu abriu, mas não localizei CSV visível.')
        except (StaleElementReferenceException, ElementClickInterceptedException, TimeoutException) as e:
            ultima_ex = e
            _log(f'Tentativa {tentativa}/5 falhou ao clicar em CSV: {e}')
            time.sleep(1.5)
    raise TimeoutException(f'Não encontrei a opção CSV após várias tentativas. Último erro: {ultima_ex}')


def run_bussola_download(
    login: str,
    senha: str,
    output_path: Path,
    headless: bool = False,
    status_cb: Optional[Callable[..., None]] = None,
) -> Path:
    if not login or not senha:
        raise ValueError('Informe login e senha do Bússola.')

    _log('Iniciando extração do Bússola via extrator de 90 dias.')

    try:
        _notify(status_cb, mensagem='Preparando extracao do Bussola.', etapa='Preparacao', atual=1, total=5)
        try:
            from bussola_extrator import executar as executar_bussola
        except Exception:
            from scripts.bussola_extrator import executar as executar_bussola

        saida_dir = output_path.parent
        download_dir = DATA_DIR / 'downloads_bussola'
        saida_dir.mkdir(parents=True, exist_ok=True)
        download_dir.mkdir(parents=True, exist_ok=True)

        _cleanup_old_bussola_files(saida_dir, download_dir)
        _notify(status_cb, mensagem='Ambiente limpo e pronto para iniciar.', etapa='Preparacao', atual=2, total=5)

        executar_bussola(
            usuario=login,
            senha=senha,
            saida=str(saida_dir),
            downloads=str(download_dir),
            headless=headless,
            log_fn=lambda msg: _notify(status_cb, mensagem=msg, etapa='Extracao', atual=3, total=5),
        )

        final_path = output_path if output_path.exists() else (saida_dir / 'Pedidos.xlsx')
        if not final_path.exists():
            raise FileNotFoundError(f'Arquivo final não encontrado após extração: {final_path}')

        _cleanup_old_bussola_files(saida_dir, download_dir)

        _log(f'Base Pedidos atualizada em: {final_path.name}')
        _notify(status_cb, status='ok', mensagem=f'Bussola atualizado com sucesso: {final_path.name}', etapa='Concluido', atual=5, total=5, resumo={'arquivo': final_path.name})
        return final_path
    except Exception as e:
        _log(f'Falha na extração do Bússola: {e}')
        _notify(status_cb, status='erro', mensagem='Falha na extracao do Bussola.', etapa='Falha', erro=str(e), nivel='error')
        raise


COLUNAS_MEF = ['EAN', 'NOME DO PRODUTO', 'DISTRIBUIDORA', 'ESTOQUE', 'DESCONTO (%)', 'PF DIST. (R$)', 'PF FABRICA (R$)', 'PREÇO FINAL (R$)', 'SEM IMPOSTO (R$)', 'DATA']


def _extrair_valor_numerico(texto, tipo='valor'):
    if texto is None:
        return 0.0
    texto_limpo = str(texto).replace('R$', '').replace('\xa0', ' ').strip()
    if tipo == 'estoque':
        nums = re.findall(r'\d+', texto_limpo)
        return int(nums[0]) if nums else 0
    if tipo == 'desconto':
        val_temp = texto_limpo.replace('%', '').strip()
        try:
            return float(val_temp.replace(',', '.')) / 100.0
        except Exception:
            return 0.0
    match = re.search(r'[\d\.,]+', texto_limpo)
    if not match:
        return 0.0
    valor_sujo = match.group(0)
    if ',' in valor_sujo:
        valor_sujo = valor_sujo.replace('.', '').replace(',', '.')
    try:
        return float(valor_sujo)
    except Exception:
        return 0.0


def _write_inventory_excel(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)


def _ean_list_from_produtos(produtos_df: pd.DataFrame) -> list[str]:
    if produtos_df is None or produtos_df.empty:
        return []

    colunas = {str(c).strip().lower(): c for c in produtos_df.columns}

    coluna_ean = None
    for nome in ['ean', 'cod ean', 'codigo de barras', 'código de barras', 'codigo', 'código']:
        if nome in colunas:
            coluna_ean = colunas[nome]
            break

    if coluna_ean is None:
        coluna_ean = produtos_df.columns[0]

    eans = produtos_df[coluna_ean].dropna().astype(str)
    eans = [re.sub(r'\D', '', x) for x in eans if re.sub(r'\D', '', x)]
    return sorted(dict.fromkeys(eans))


def _select_marketfarma_client(driver: webdriver.Chrome, wait: WebDriverWait, cnpj: str):
    campo = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='CNPJ']")))
    campo.clear()
    campo.send_keys(cnpj)
    time.sleep(1)
    candidatos = [
        f"//div[contains(@data-value, '{cnpj}')]",
        f"//*[contains(@data-value, '{cnpj}')]",
        f"//*[contains(translate(., './- ', ''), '{cnpj}')]",
    ]
    for xp in candidatos:
        try:
            item = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, xp)))
            driver.execute_script('arguments[0].click();', item)
            return
        except Exception:
            continue
    raise TimeoutException(f'Não consegui selecionar o CNPJ {cnpj} no Mercado Farma.')


def _open_catalogo(driver: webdriver.Chrome, wait: WebDriverWait):
    _click_first(driver, wait, [
        "//a[contains(., 'Catálogo A a Z')]",
        "//*[text()='Catálogo A a Z']",
        "//a[contains(., 'Catalogo A a Z')]",
    ], timeout=25)
    wait.until(EC.presence_of_element_located((By.NAME, 'term')))


def run_mercadofarma_inventory(
    login: str,
    senha: str,
    cnpj: str,
    produtos_df: pd.DataFrame,
    output_path: Path,
    headless: bool = False,
    status_cb: Optional[Callable[..., None]] = None,
) -> Path:
    if not login or not senha:
        raise ValueError('Informe login e senha do Mercado Farma.')
    cnpj = _clean_cnpj(cnpj)
    if not cnpj:
        raise ValueError('Informe um CNPJ para entrar no Mercado Farma.')
    eans = _ean_list_from_produtos(produtos_df)
    if not eans:
        raise ValueError('Nenhum EAN encontrado na base de produtos.')

    output_rows: list[dict] = []
    download_dir = DATA_DIR / 'downloads_mercadofarma'
    _cleanup_download_dir(download_dir, {'.xlsx', '.xls', '.csv', '.crdownload', '.tmp', '.part'})
    driver = _make_driver(download_dir, headless=headless)
    total_passos = len(eans) + 3
    _log(f'Iniciando extração do Mercado Farma com {len(eans)} EANs.')
    _notify(status_cb, mensagem=f'Preparando consulta de {len(eans)} EANs.', etapa='Preparacao', atual=1, total=len(eans) + 3)
    try:
        driver.get('https://www.mercadofarma.com.br/')
        _log('Página inicial do Mercado Farma aberta.')
        try:
            rejeitar = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Rejeitar')]")))
            driver.execute_script('arguments[0].click();', rejeitar)
            _log('Banner de cookies rejeitado.')
        except Exception:
            pass

        _click_first(driver, wait, [
            "//button[.//span[contains(text(), 'Entrar como representante')]]",
            "//button[contains(., 'Entrar como representante')]",
        ], timeout=25)
        _log('Entrada como representante acionada.')
        wait.until(EC.element_to_be_clickable((By.ID, 'social-saml'))).click()
        _log('Login social SAML acionado.')

        usuario = wait.until(EC.presence_of_element_located((By.ID, 'userNameInput')))
        usuario.clear()
        usuario.send_keys(login)
        senha_el = driver.find_element(By.ID, 'passwordInput')
        senha_el.clear()
        senha_el.send_keys(senha)
        driver.find_element(By.ID, 'submitButton').click()
        _log('Credenciais do Mercado Farma enviadas.')

        _select_marketfarma_client(driver, wait, cnpj)
        _log(f'CNPJ {cnpj} selecionado no Mercado Farma.')
        _open_catalogo(driver, wait)
        _log('Catálogo A a Z aberto.')

        for i, ean in enumerate(eans, start=1):
            _log(f'Consultando EAN {i}/{len(eans)}: {ean}')
            try:
                campo_busca = wait.until(EC.presence_of_element_located((By.NAME, 'term')))
                driver.execute_script("arguments[0].value='';", campo_busca)
                campo_busca.send_keys(Keys.CONTROL, 'a')
                campo_busca.send_keys(Keys.BACKSPACE)
                campo_busca.send_keys(ean + Keys.ENTER)

                nome_prod_elem = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//small[@role='button' and contains(@class, 'cursor-pointer')]"))
                )
                nome_produto = nome_prod_elem.text.strip()

                trigger = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-slot='popover-trigger']")))
                driver.execute_script('arguments[0].click();', trigger)
                WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-slot='popover-content']")))
                time.sleep(0.8)

                distribuidoras = driver.find_elements(By.CSS_SELECTOR, "[data-test-id='buybox-item']")
                if not distribuidoras:
                    raise NoSuchElementException('Nenhuma distribuidora exibida para o EAN.')

                for dist in distribuidoras:
                    nome_dist = _safe_text(dist, By.CSS_SELECTOR, 'p.font-open-sans', '')
                    estoque_txt = _safe_text(dist, By.CSS_SELECTOR, 'small.text-primary', '0')
                    pf_dist_txt = _safe_text(dist, By.XPATH, ".//small[contains(text(), 'PF Dist.')]", 'R$ 0,00')
                    preco_f_txt = _safe_text(dist, By.CSS_SELECTOR, 'h5', 'R$ 0,00')
                    sem_imp_txt = _safe_text(dist, By.XPATH, ".//small[contains(text(), 'Sem imposto')]", 'R$ 0,00')
                    desc_txt = _safe_text(dist, By.XPATH, ".//span[contains(text(), '%')]", '0%')

                    output_rows.append({
                        'EAN': ean,
                        'NOME DO PRODUTO': nome_produto,
                        'DISTRIBUIDORA': nome_dist,
                        'ESTOQUE': _extrair_valor_numerico(estoque_txt, 'estoque'),
                        'DESCONTO (%)': _extrair_valor_numerico(desc_txt, 'desconto'),
                        'PF DIST. (R$)': _extrair_valor_numerico(pf_dist_txt),
                        'PREÇO FINAL (R$)': _extrair_valor_numerico(preco_f_txt),
                        'SEM IMPOSTO (R$)': _extrair_valor_numerico(sem_imp_txt),
                        'DATA': datetime.now().strftime('%d/%m/%Y %H:%M'),
                    })
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.4)
            except (TimeoutException, NoSuchElementException) as e:
                _log(f'EAN {ean} não encontrado ou sem oferta: {e}')
                output_rows.append({
                    'EAN': ean,
                    'NOME DO PRODUTO': 'PRODUTO NÃO ENCONTRADO',
                    'DISTRIBUIDORA': '',
                    'ESTOQUE': 0,
                    'DESCONTO (%)': 0,
                    'PF DIST. (R$)': 0,
                    'PREÇO FINAL (R$)': 0,
                    'SEM IMPOSTO (R$)': 0,
                    'DATA': datetime.now().strftime('%d/%m/%Y %H:%M'),
                })

        df = pd.DataFrame(output_rows, columns=COLUNAS_MEF)
        _write_inventory_excel(df, output_path)
        # substitui também versão xlsm antiga, se existir, para evitar base duplicada/desatualizada
        alt_xlsm = output_path.with_suffix('.xlsm')
        if alt_xlsm.exists():
            try:
                alt_xlsm.unlink()
            except Exception:
                pass
        _log(f'Base de estoque/preço substituída por nova extração: {output_path.name}')
        return output_path
    except Exception as e:
        _notify(status_cb, status='erro', mensagem='Falha na extracao do Mercado Farma.', etapa='Falha', erro=str(e), nivel='error')
        raise
    finally:
        try:
            driver.quit()
        except Exception:
            pass



def _normalize_dist_mf(texto: str) -> str:
    s = str(texto or '').strip().lower()
    s = s.replace('–', '-').replace('—', '-')
    s = re.sub(r'\s+', ' ', s)
    return s


def run_mercadofarma_inventory(
    login: str,
    senha: str,
    cnpj: str,
    produtos_df: pd.DataFrame,
    output_path: Path,
    headless: bool = False,
    status_cb: Optional[Callable[..., None]] = None,
) -> Path:
    if not login or not senha:
        raise ValueError('Informe login e senha do Mercado Farma.')
    cnpj = _clean_cnpj(cnpj)
    if not cnpj:
        raise ValueError('Informe um CNPJ para entrar no Mercado Farma.')

    eans = _ean_list_from_produtos(produtos_df)
    if not eans:
        raise ValueError('Nenhum EAN encontrado na base de produtos.')

    output_rows: list[dict] = []
    download_dir = DATA_DIR / 'downloads_mercadofarma'
    _cleanup_download_dir(download_dir, {'.xlsx', '.xls', '.csv', '.crdownload', '.tmp', '.part'})
    driver = _make_driver(download_dir, headless=headless)
    _log(f'Iniciando extraÃ§Ã£o do Mercado Farma com {len(eans)} EANs.')

    try:
        _notify(status_cb, mensagem=f'Preparando consulta de {len(eans)} EANs.', etapa='Preparacao', atual=1, total=len(eans) + 3, resumo={'ean_total': len(eans)})
        mf_login_catalogo(
            driver,
            login,
            senha,
            log_fn=lambda msg: (_log(msg), _notify(status_cb, mensagem=msg, etapa='Login', atual=2, total=len(eans) + 3)),
        )
        selecionar_cnpj_catalogo(
            driver,
            cnpj,
            log_fn=lambda msg: (_log(msg), _notify(status_cb, mensagem=msg, etapa='Selecao de CNPJ', atual=3, total=len(eans) + 3)),
        )

        for i, ean in enumerate(eans, start=1):
            _log(f'Consultando EAN {i}/{len(eans)}: {ean}')
            _notify(status_cb, mensagem=f'Consultando EAN {i}/{len(eans)}: {ean}', etapa='Extracao por EAN', atual=i + 3, total=len(eans) + 3, resumo={'ean_atual': ean, 'ean_total': len(eans)})
            try:
                output_rows.extend(processar_ean_catalogo(driver, ean))
            except Exception as e:
                _log(f'EAN {ean} nÃ£o encontrado ou sem oferta: {e}')
                output_rows.append(build_not_found_row(ean))
                _notify(status_cb, mensagem=f'EAN {ean} sem oferta ou com falha.', etapa='Extracao por EAN', atual=i + 3, total=len(eans) + 3, erro=str(e), nivel='warning')

            if i % 10 == 0 and output_rows:
                _write_inventory_excel(pd.DataFrame(output_rows, columns=COLUNAS_MEF), output_path)
                _log(f'Parcial salva com {len(output_rows)} linhas.')
                _notify(status_cb, mensagem=f'Parcial salva com {len(output_rows)} linhas.', etapa='Salvando parcial', atual=i + 3, total=len(eans) + 3)

        df = pd.DataFrame(output_rows, columns=COLUNAS_MEF)
        _write_inventory_excel(df, output_path)

        alt_xlsm = output_path.with_suffix('.xlsm')
        if alt_xlsm.exists():
            try:
                alt_xlsm.unlink()
            except Exception:
                pass

        _log(f'Base de estoque/preÃ§o substituÃ­da por nova extraÃ§Ã£o: {output_path.name}')
        _notify(status_cb, status='ok', mensagem=f'Mercado Farma atualizado com sucesso: {output_path.name}', etapa='Concluido', atual=len(eans) + 3, total=len(eans) + 3, resumo={'arquivo': output_path.name, 'linhas': len(df)})
        return output_path
    except Exception as e:
        _log(f'Falha na extração do Mercado Farma: {e}')
        _notify(status_cb, status='erro', mensagem='Falha na extracao do Mercado Farma.', etapa='Falha', erro=str(e), nivel='error')
        raise
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _mercadofarma_login_and_select_client(driver: webdriver.Chrome, login: str, senha: str, cnpj: str):
    driver.get('https://www.mercadofarma.com.br/')
    try:
        rejeitar = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Rejeitar')]")))
        driver.execute_script('arguments[0].click();', rejeitar)
    except Exception:
        pass
    _click_first(driver, None, [
        "//button[.//span[contains(text(), 'Entrar como representante')]]",
        "//*[contains(., 'Entrar como representante')]",
    ], timeout=30)
    _click_first(driver, None, ["//*[@id='social-saml']"], timeout=30)
    usuario = _wait_visible(driver, ["//*[@id='userNameInput']"], timeout=60)
    usuario.clear(); usuario.send_keys(login)
    senha_el = _wait_visible(driver, ["//*[@id='passwordInput']"], timeout=20)
    senha_el.clear(); senha_el.send_keys(senha)
    _click_first(driver, None, ["//*[@id='submitButton']"], timeout=20)

    campo = _wait_visible(driver, [
        "//input[contains(@placeholder,'CNPJ')]",
        "//input[contains(@placeholder,'cnpj')]",
    ], timeout=60)
    campo.clear()
    campo.send_keys(_clean_cnpj(cnpj))
    time.sleep(2)
    item = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((By.XPATH, f"//div[contains(@data-value, '{_clean_cnpj(cnpj)}') or contains(., '{_clean_cnpj(cnpj)}')]"))
    )
    _safe_click(driver, item)
    WebDriverWait(driver, 90).until(
        lambda d: (
            'pedido-massivo' in d.current_url.lower()
            or len([e for e in d.find_elements(By.XPATH, "//*[contains(., 'Selecionar distribuidores')]") if e.is_displayed()]) > 0
            or len([e for e in d.find_elements(By.XPATH, "//*[@role='radio' and .//*[contains(., 'Distribuidor')]]") if e.is_displayed()]) > 0
        )
    )
    time.sleep(2)


def _mf_prepare_mass_order_screen(driver: webdriver.Chrome):
    try:
        _click_first(driver, None, [
            "//button[@role='radio' and .//span[contains(., 'Distribuidor')]]",
            "//*[contains(., 'Distribuidor') and @role='radio']",
        ], timeout=8)
    except Exception:
        pass
    time.sleep(1)


def _mf_mark_distributor_left(driver: webdriver.Chrome, nome_dist: str) -> bool:
    alvo = _normalize_dist_mf(nome_dist)
    labels = [el for el in driver.find_elements(By.XPATH, "//label[contains(@class,'cursor-pointer')]") if el.is_displayed()]
    for lab in labels:
        try:
            txt = _normalize_dist_mf(lab.text)
            if txt and (alvo in txt or txt in alvo):
                _safe_click(driver, lab)
                time.sleep(0.5)
                return True
        except Exception:
            continue
    return False



def _mf_clear_previous_selection(driver: webdriver.Chrome):
    """Limpa seleção antiga de distribuidores/produtos no pedido massivo, se existir."""
    try:
        _click_first(driver, None, [
            "//button[.//span[normalize-space()='Limpar seleção']]",
            "//span[normalize-space()='Limpar seleção']/ancestor::button[1]",
            "//*[normalize-space()='Limpar seleção']/ancestor::button[1]",
        ], timeout=6)
    except Exception:
        return False

    time.sleep(1)
    try:
        # espera o modal abrir
        WebDriverWait(driver, 12).until(
            EC.visibility_of_element_located((
                By.XPATH,
                "//div[@role='dialog' and .//h2[contains(., 'Deseja limpar a seleção de distribuidores?')]]"
            ))
        )

        btn_confirmar = _click_first(driver, None, [
            "//div[@role='dialog']//button[@type='submit' and .//span[normalize-space()='Limpar seleção']]",
            "//div[@role='dialog']//button[contains(@class,'bg-error') and .//span[normalize-space()='Limpar seleção']]",
            "//div[@role='dialog']//button[.//span[normalize-space()='Limpar seleção']]",
        ], timeout=12)
        try:
            _safe_click(driver, btn_confirmar)
        except Exception:
            pass
        time.sleep(2)
        WebDriverWait(driver, 12).until(
            EC.invisibility_of_element_located((
                By.XPATH,
                "//div[@role='dialog' and .//h2[contains(., 'Deseja limpar a seleção de distribuidores?')]]"
            ))
        )
        _log('Seleção antiga do Mercado Farma limpa.')
        return True
    except Exception as e:
        _log(f'Falha ao confirmar limpeza do Mercado Farma: {e}')
        return False


def _mf_select_distributors(driver: webdriver.Chrome, distribs: list[str]):
    desejados = []
    for d in distribs:
        nd = _normalize_dist_mf(d)
        if nd and nd not in desejados:
            desejados.append(nd)
    if not desejados:
        raise ValueError('Nenhuma distribuidora informada para o pedido.')

    selecionadas = []
    for nd in desejados:
        if _mf_mark_distributor_left(driver, nd):
            selecionadas.append(nd)
            _log(f'Distribuidora selecionada no Mercado Farma: {nd}')
        else:
            _log(f'Não encontrei na lista esquerda a distribuidora: {nd}')

    if not selecionadas:
        raise TimeoutException('Nenhuma distribuidora foi selecionada na tela do Mercado Farma.')

    _click_first(driver, None, [
        "//button[normalize-space()='Selecionar distribuidores']",
        "//*[normalize-space()='Selecionar distribuidores']",
    ], timeout=20)
    time.sleep(3)


def _mf_clear_search(driver: webdriver.Chrome):
    campo = _wait_visible(driver, [
        "//input[contains(@placeholder,'Procure por nome') or contains(@placeholder,'EAN')]",
        "//input[contains(@placeholder,'EAN')]",
    ], timeout=25)
    campo.click()
    try:
        campo.send_keys(Keys.CONTROL, 'a')
        campo.send_keys(Keys.DELETE)
    except Exception:
        pass
    try:
        driver.execute_script(
            "arguments[0].value=''; arguments[0].dispatchEvent(new Event('input', {bubbles:true})); arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            campo,
        )
    except Exception:
        pass
    time.sleep(0.8)
    return campo


def _mf_search_product(driver: webdriver.Chrome, termo: str):
    campo = _mf_clear_search(driver)
    campo.send_keys(termo)
    time.sleep(2.5)


def _mf_visible_cards(driver: webdriver.Chrome):
    return [el for el in driver.find_elements(By.XPATH, "//div[contains(@class,'bg-white') and .//input[@data-testid='quantity-input-input']]") if el.is_displayed()]


def _mf_extract_card_distributor(card) -> str:
    xps = [
        ".//p[contains(@class,'text-black')]",
        ".//div[@id]//p[1]",
        ".//*[contains(text(),' - ')]",
    ]
    for xp in xps:
        try:
            els = card.find_elements(By.XPATH, xp)
            for el in els:
                txt = (el.text or '').strip()
                if txt and ' - ' in txt and len(txt) < 40:
                    return txt
        except Exception:
            pass
    return ''


def _mf_choose_card(cards, distribuidora: str):
    alvo = _normalize_dist_mf(distribuidora)
    for card in cards:
        try:
            txt = _normalize_dist_mf(card.text)
            dist_card = _normalize_dist_mf(_mf_extract_card_distributor(card))
            if alvo and ((dist_card and (alvo in dist_card or dist_card in alvo)) or (alvo in txt)):
                return card
        except Exception:
            continue
    return None


def _mf_set_qty_on_card(driver: webdriver.Chrome, card, qtde: int):
    inp = None
    for xp in [
        ".//input[@aria-label='Quantidade']",
        ".//*[@data-testid='quantity-input-input']",
        ".//input[@type='text' and @maxlength='99999']",
    ]:
        try:
            inp = card.find_element(By.XPATH, xp)
            break
        except Exception:
            pass
    if inp is None:
        raise TimeoutException('Campo de quantidade não encontrado no card.')
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
    time.sleep(0.4)
    try:
        inp.click()
    except Exception:
        driver.execute_script("arguments[0].focus();", inp)
        time.sleep(0.2)
    try:
        inp.send_keys(Keys.CONTROL, 'a')
        inp.send_keys(Keys.DELETE)
        time.sleep(0.2)
        inp.send_keys(str(qtde))
    except Exception:
        driver.execute_script("arguments[0].value='';", inp)
        inp.send_keys(str(qtde))
    time.sleep(0.6)


def _mf_add_card_to_cart(driver: webdriver.Chrome, card):
    btn = None
    for xp in [
        ".//button[contains(., 'R$')]",
        ".//button[@role='button' and .//*[local-name()='svg']]",
        ".//button[contains(@class,'shopping-cart') or contains(@class,'bg-primary')]",
    ]:
        try:
            btn = card.find_element(By.XPATH, xp)
            break
        except Exception:
            pass
    if btn is None:
        raise TimeoutException('Botão de adicionar ao carrinho não encontrado.')
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    time.sleep(0.4)
    try:
        btn.click()
    except Exception:
        driver.execute_script('arguments[0].click();', btn)
    time.sleep(1.5)



def _mf_apply_coupon_if_any(driver: webdriver.Chrome, cupom: str):
    cupom = str(cupom or '').strip()
    if not cupom:
        return False
    try:
        campo = _wait_visible(driver, [
            "//input[contains(@placeholder,'cupom') or contains(@placeholder,'Cupom') or contains(@placeholder,'código do cupom')]",
            "//input[contains(@placeholder,'Insira o código do cupom')]",
        ], timeout=10)
        campo.click(); campo.clear(); campo.send_keys(cupom)
        _click_first(driver, None, [
            "//button[.//span[normalize-space()='Adicionar']]",
            "//*[normalize-space()='Adicionar']/ancestor::button[1]",
        ], timeout=8)
        time.sleep(1.5)
        _log(f'Cupom aplicado no Mercado Farma: {cupom}')
        return True
    except Exception as e:
        _log(f'Não foi possível aplicar cupom {cupom}: {e}')
        return False


def _mf_unique_coupons(cupom: str = "", items: list[dict] | None = None) -> list[str]:
    cupons = []
    raw_values = [cupom]
    raw_values.extend([item.get('Cupom', '') for item in (items or [])])
    for raw in raw_values:
        for token in re.split(r"[,;|\s]+", str(raw or "").strip()):
            token = token.strip()
            if token and token not in cupons:
                cupons.append(token)
    return cupons


def _mf_click_send_order(driver: webdriver.Chrome):
    _click_first(driver, None, [
        "//button[.//span[normalize-space()='Enviar pedido']]",
        "//*[normalize-space()='Enviar pedido']/ancestor::button[1]",
    ], timeout=15)
    time.sleep(1.5)


def _mf_confirm_send_even_if_overstock(driver: webdriver.Chrome):
    try:
        WebDriverWait(driver, 6).until(EC.visibility_of_element_located((By.XPATH, "//div[@role='dialog' and .//h2[contains(., 'Pedido com quantidade acima do estoque')]]")))
        _click_first(driver, None, [
            "//div[@role='dialog']//button[@type='submit' and .//span[normalize-space()='Enviar mesmo assim']]",
            "//div[@role='dialog']//button[.//span[normalize-space()='Enviar mesmo assim']]",
        ], timeout=10)
        time.sleep(1.5)
        _log('Confirmado envio mesmo com quantidade acima do estoque.')
        return True
    except Exception:
        return False


def _mf_finish_payment_and_send(driver: webdriver.Chrome):
    WebDriverWait(driver, 20).until(EC.visibility_of_element_located((By.XPATH, "//div[@role='dialog' and .//h2[contains(., 'Informações de pagamento')]] | //div[@data-slot='drawer-content' and .//h2[contains(., 'Informações de pagamento')]]")))
    try:
        chk = _find_first(driver, [
            "//*[@id='hasPurchaseOrder' and @role='checkbox']",
            "//button[@role='checkbox' and @id='hasPurchaseOrder']",
        ], timeout=10)
        try:
            aria = chk.get_attribute('aria-checked')
        except Exception:
            aria = None
        if aria != 'true':
            _safe_click(driver, chk)
            time.sleep(0.8)
    except Exception as e:
        _log(f'Não consegui marcar checkbox de ordem de compra: {e}')
    _click_first(driver, None, [
        "//div[@role='dialog']//button[not(@disabled) and .//span[normalize-space()='Enviar pedido']]",
        "//div[@data-slot='drawer-content']//button[not(@disabled) and .//span[normalize-space()='Enviar pedido']]",
        "//button[not(@disabled) and .//span[normalize-space()='Enviar pedido']]",
    ], timeout=15)
    time.sleep(2)
    _log('Pedido enviado na etapa final de pagamento.')
    return True

def clear_mercadofarma_mass_order(
    login: str,
    senha: str,
    cnpj: str,
    headless: bool = True,
    status_cb: Optional[Callable[..., None]] = None,
):
    if not login or not senha:
        raise ValueError('Informe login e senha do Mercado Farma.')
    cnpj = _clean_cnpj(cnpj)
    if not cnpj:
        raise ValueError('Informe um CNPJ válido.')
    driver = _make_driver(DATA_DIR / 'downloads_mercadofarma_pedido', headless=headless)
    try:
        _log(f'Limpando seleção anterior no Mercado Farma - CNPJ {cnpj}')
        _notify(status_cb, mensagem=f'Limpando selecao anterior para o CNPJ {cnpj}.', etapa='Limpeza', atual=1, total=3)
        _mercadofarma_login_and_select_client(driver, login, senha, cnpj)
        _notify(status_cb, mensagem='Cliente autenticado no Mercado Farma.', etapa='Limpeza', atual=2, total=3)
        _mf_prepare_mass_order_screen(driver)
        cleaned = _mf_clear_previous_selection(driver)
        if not cleaned:
            _log('Nenhuma seleção antiga encontrada para limpar.')
        _notify(status_cb, status='ok', mensagem='Limpeza do pedido Mercado Farma finalizada.', etapa='Concluido', atual=3, total=3, resumo={'cnpj': cnpj, 'limpo': bool(cleaned)})
        return cleaned
    except Exception as e:
        _log(f'Falha ao limpar pedido Mercado Farma - CNPJ {cnpj}: {e}')
        _notify(status_cb, status='erro', mensagem=f'Falha ao limpar pedido do CNPJ {cnpj}.', etapa='Falha', atual=3, total=3, erro=str(e), nivel='error')
        raise
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def run_mercadofarma_mass_order(
    login: str,
    senha: str,
    cart_items: list[dict],
    headless: bool = True,
    cupom: str = "",
    status_cb: Optional[Callable[..., None]] = None,
):
    if not login or not senha:
        raise ValueError('Informe login e senha do Mercado Farma.')
    if not cart_items:
        raise ValueError('Carrinho vazio.')

    por_cnpj: dict[str, list[dict]] = {}
    for item in cart_items:
        por_cnpj.setdefault(_clean_cnpj(item.get('CNPJ', '')), []).append(item)

    relatorio = []
    for cnpj, itens_cnpj in por_cnpj.items():
        if not cnpj:
            continue
        driver = _make_driver(DATA_DIR / 'downloads_mercadofarma_pedido', headless=headless)
        try:
            _log(f'Enviando carrinho para Mercado Farma - CNPJ {cnpj}')
            _notify(status_cb, mensagem=f'Iniciando envio do pedido para o CNPJ {cnpj}.', etapa='Preparacao', atual=1, total=len(itens_cnpj) + 5, resumo={'cnpj': cnpj, 'itens': len(itens_cnpj)})
            _mercadofarma_login_and_select_client(driver, login, senha, cnpj)
            _notify(status_cb, mensagem='Cliente autenticado no Mercado Farma.', etapa='Login', atual=2, total=len(itens_cnpj) + 5)
            _mf_prepare_mass_order_screen(driver)
            _mf_clear_previous_selection(driver)

            distribs_unicas = []
            for item in itens_cnpj:
                d = str(item.get('Distribuidora','')).strip()
                if d and d not in distribs_unicas:
                    distribs_unicas.append(d)
            _mf_select_distributors(driver, distribs_unicas)
            _notify(status_cb, mensagem=f'{len(distribs_unicas)} distribuidora(s) selecionada(s).', etapa='Selecao de distribuidoras', atual=3, total=len(itens_cnpj) + 5, resumo={'distribuidoras': len(distribs_unicas)})

            for posicao, item in enumerate(itens_cnpj, start=1):
                ean = re.sub(r'\D','', str(item.get('EAN','')))
                dist_desejada = str(item.get('Distribuidora','')).strip()
                qtd = int(pd.to_numeric(item.get('Qtde',1), errors='coerce') or 1)
                try:
                    _notify(status_cb, mensagem=f'Enviando item {posicao}/{len(itens_cnpj)}: {ean} / {dist_desejada}', etapa='Montagem do pedido', atual=posicao + 3, total=len(itens_cnpj) + 5, resumo={'ean_atual': ean, 'distribuidora': dist_desejada})
                    _mf_search_product(driver, ean)
                    WebDriverWait(driver, 20).until(lambda d: len(_mf_visible_cards(d)) > 0)
                    cards = _mf_visible_cards(driver)
                    card = _mf_choose_card(cards, dist_desejada)
                    if card is None:
                        raise TimeoutException(f'Não encontrei card da distribuidora {dist_desejada} para o EAN {ean}')
                    _mf_set_qty_on_card(driver, card, qtd)
                    _mf_add_card_to_cart(driver, card)
                    relatorio.append({'cnpj': cnpj, 'ean': ean, 'distribuidora': dist_desejada, 'qtd': qtd, 'status': 'ok', 'erro': ''})
                    _log(f'Item enviado ao carrinho Mercado Farma: {ean} / {dist_desejada} / qtd {qtd}')
                except Exception as e:
                    relatorio.append({'cnpj': cnpj, 'ean': ean, 'distribuidora': dist_desejada, 'qtd': qtd, 'status': 'erro', 'erro': str(e)})
                    _log(f'Falha ao enviar item ao Mercado Farma: {ean} / {dist_desejada} / qtd {qtd} - {e}')
                    _notify(status_cb, mensagem=f'Falha no item {ean} / {dist_desejada}.', etapa='Montagem do pedido', atual=posicao + 3, total=len(itens_cnpj) + 5, erro=str(e), nivel='warning')
            try:
                for cupom_item in _mf_unique_coupons(cupom, itens_cnpj):
                    _mf_apply_coupon_if_any(driver, cupom_item)
                _notify(status_cb, mensagem='Pedido montado. Enviando para confirmacao final.', etapa='Confirmacao final', atual=len(itens_cnpj) + 4, total=len(itens_cnpj) + 5)
                _mf_click_send_order(driver)
                _mf_confirm_send_even_if_overstock(driver)
                _mf_finish_payment_and_send(driver)
                _log(f'Pedido finalizado no Mercado Farma - CNPJ {cnpj}')
                _notify(status_cb, status='ok', mensagem=f'Pedido enviado ao Mercado Farma para o CNPJ {cnpj}.', etapa='Concluido', atual=len(itens_cnpj) + 5, total=len(itens_cnpj) + 5, resumo={'cnpj': cnpj, 'itens': len(itens_cnpj)})
            except Exception as e:
                relatorio.append({'cnpj': cnpj, 'ean': '', 'distribuidora': '', 'qtd': 0, 'status': 'erro_envio', 'erro': str(e)})
                _log(f'Falha ao finalizar pedido no Mercado Farma - CNPJ {cnpj}: {e}')
                _notify(status_cb, status='erro', mensagem=f'Falha ao finalizar o pedido do CNPJ {cnpj}.', etapa='Falha', atual=len(itens_cnpj) + 5, total=len(itens_cnpj) + 5, erro=str(e), nivel='error')
        except Exception as e:
            relatorio.append({'cnpj': cnpj, 'ean': '', 'distribuidora': '', 'qtd': 0, 'status': 'erro_geral', 'erro': str(e)})
            _log(f'Falha geral no envio ao Mercado Farma - CNPJ {cnpj}: {e}')
            _notify(status_cb, status='erro', mensagem=f'Falha no envio do pedido para o CNPJ {cnpj}.', etapa='Falha', atual=len(itens_cnpj) + 5, total=len(itens_cnpj) + 5, erro=str(e), nivel='error')
            raise
        finally:
            try:
                driver.quit()
            except Exception:
                pass
    return relatorio
