from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

MF_URL = "https://www.mercadofarma.com.br/"
CONFIG_FILE = "config.ini"
DEFAULT_INPUT_NAME = "PRODUTOS COM EAN - POR LANCAMENTOS-PRIORITARIOS-LINHA.xlsx"
DEFAULT_OUTPUT_NAME = "Estoque_preco_distribuidora.xlsx"
DEFAULT_CRED_JSON = "credenciais_integracao.json"
DEFAULT_CRED_INI = "integracoes.ini"
DEFAULT_DEBUG_DIR_NAME = "debug_mercadofarma"
FLUSH_BUFFER_EVERY = 10
DEFAULT_TIMEOUT = 25
COLUNAS = [
    "EAN",
    "NOME DO PRODUTO",
    "DISTRIBUIDORA",
    "ESTOQUE",
    "DESCONTO (%)",
    "PF DIST. (R$)",
    "PREÇO FINAL (R$)",
    "SEM IMPOSTO (R$)",
    "DATA",
    "STATUS",
    "ERRO",
]


def log(msg: str) -> None:
    print(f"[MERCADOFARMA] {msg}", flush=True)



def now_str() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")



def wait(driver: WebDriver, seconds: int = DEFAULT_TIMEOUT) -> WebDriverWait:
    return WebDriverWait(driver, seconds)



def normalize_text(texto: str) -> str:
    s = str(texto or "").strip().lower()
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    return s



def clean_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", str(cnpj or ""))



def extrair_valor_numerico(texto: str, tipo: str = "valor"):
    if not texto:
        return 0.0 if tipo != "estoque" else 0

    texto_limpo = str(texto).replace("R$", "").replace("\xa0", " ").strip()

    if tipo == "estoque":
        nums = re.findall(r"\d+", texto_limpo)
        return int(nums[0]) if nums else 0

    if tipo == "desconto":
        val_temp = texto_limpo.replace("%", "").strip()
        try:
            return float(val_temp.replace(",", ".")) / 100.0
        except Exception:
            return 0.0

    match = re.search(r"[\d\.,]+", texto_limpo)
    if not match:
        return 0.0

    try:
        return float(match.group(0).replace(".", "").replace(",", "."))
    except Exception:
        return 0.0



def script_dir() -> Path:
    return Path(__file__).resolve().parent



def data_dir() -> Path:
    return script_dir() / "data"



def default_input_path() -> Path:
    return data_dir() / DEFAULT_INPUT_NAME



def default_output_path() -> Path:
    return data_dir() / DEFAULT_OUTPUT_NAME



def default_debug_dir() -> Path:
    return script_dir() / DEFAULT_DEBUG_DIR_NAME



def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)



def save_debug(driver: Optional[WebDriver], debug_dir: Path, prefix: str) -> None:
    if driver is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png = debug_dir / f"{prefix}_{stamp}.png"
    html = debug_dir / f"{prefix}_{stamp}.html"
    try:
        driver.save_screenshot(str(png))
    except Exception:
        pass
    try:
        html.write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass



def write_formatted_excel(df: pd.DataFrame, path: Path) -> None:
    ensure_parent_dir(path)
    df_temp = df.copy()
    if "EAN" in df_temp.columns:
        df_temp["EAN"] = df_temp["EAN"].astype(str)

    try:
        with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
            df_temp.to_excel(writer, sheet_name="Resultados", index=False, startrow=2, header=False)
            workbook = writer.book
            worksheet = writer.sheets["Resultados"]

            header_f = workbook.add_format({"bold": True, "bg_color": "#AAB2BD", "border": 1, "align": "center"})
            money_f = workbook.add_format({"num_format": "R$ #,##0.00", "align": "center"})
            pct_f = workbook.add_format({"num_format": "0.00%", "align": "center"})
            center_f = workbook.add_format({"align": "center"})
            text_f = workbook.add_format({"align": "left"})

            for col_num, value in enumerate(df_temp.columns.tolist()):
                worksheet.write(1, col_num, value, header_f)

            worksheet.set_column("A:A", 18, center_f)
            worksheet.set_column("B:B", 50, text_f)
            worksheet.set_column("C:C", 28, text_f)
            worksheet.set_column("D:D", 12, center_f)
            worksheet.set_column("E:E", 14, pct_f)
            worksheet.set_column("F:H", 18, money_f)
            worksheet.set_column("I:I", 22, center_f)
            worksheet.set_column("J:J", 18, center_f)
            worksheet.set_column("K:K", 50, text_f)
    except Exception:
        df_temp.to_excel(path, index=False)



def save_results(rows: List[dict], output_path: Path) -> None:
    df = pd.DataFrame(rows)
    for col in COLUNAS:
        if col not in df.columns:
            df[col] = ""
    df = df[COLUNAS]
    write_formatted_excel(df, output_path)



def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}



def _read_ini_credentials(path: Path) -> dict:
    if not path.exists():
        return {}
    cfg = configparser.ConfigParser()
    try:
        cfg.read(path, encoding="utf-8")
    except Exception:
        return {}

    data = {}
    if cfg.has_section("integracoes"):
        data.update(dict(cfg.items("integracoes")))
    elif cfg.has_section("CREDENTIALS"):
        data["mercado_login"] = cfg.get("CREDENTIALS", "usuario", fallback="")
        data["mercado_senha"] = cfg.get("CREDENTIALS", "senha", fallback="")
        data["mercado_cnpj"] = cfg.get("CREDENTIALS", "cnpj", fallback="")
    return data



def load_panel_creds() -> dict:
    # Ordem: JSON do painel -> INI do painel -> config.ini local -> variáveis de ambiente/args
    data = {}

    cred_json = data_dir() / DEFAULT_CRED_JSON
    cred_ini = data_dir() / DEFAULT_CRED_INI
    local_cfg = script_dir() / CONFIG_FILE

    data.update(_read_json(cred_json))
    data.update(_read_ini_credentials(cred_ini))

    cfg = configparser.ConfigParser()
    if local_cfg.exists():
        try:
            cfg.read(local_cfg, encoding="utf-8")
            if cfg.has_section("CREDENTIALS"):
                data.setdefault("mercado_login", cfg.get("CREDENTIALS", "usuario", fallback=""))
                data.setdefault("mercado_senha", cfg.get("CREDENTIALS", "senha", fallback=""))
                data.setdefault("mercado_cnpj", cfg.get("CREDENTIALS", "cnpj", fallback=""))
            if cfg.has_section("PATHS"):
                data.setdefault("input", cfg.get("PATHS", "input", fallback=""))
                data.setdefault("output", cfg.get("PATHS", "output", fallback=""))
                data.setdefault("debug_dir", cfg.get("PATHS", "debug_dir", fallback=""))
        except Exception:
            pass

    env_map = {
        "mercado_login": os.getenv("MERCADOFARMA_LOGIN") or os.getenv("MERCADOFARMA_USUARIO") or os.getenv("MF_USUARIO") or "",
        "mercado_senha": os.getenv("MERCADOFARMA_SENHA") or os.getenv("MF_SENHA") or "",
        "mercado_cnpj": os.getenv("MERCADOFARMA_CNPJ") or os.getenv("MF_CNPJ") or "",
        "input": os.getenv("MERCADOFARMA_INPUT") or "",
        "output": os.getenv("MERCADOFARMA_OUTPUT") or "",
        "debug_dir": os.getenv("MERCADOFARMA_DEBUG_DIR") or "",
    }
    for k, v in env_map.items():
        if v:
            data[k] = v

    return {
        "usuario": data.get("mercado_login", "").strip(),
        "senha": data.get("mercado_senha", "").strip(),
        "cnpj": clean_cnpj(data.get("mercado_cnpj", "")),
        "input": data.get("input", "").strip(),
        "output": data.get("output", "").strip(),
        "debug_dir": data.get("debug_dir", "").strip(),
    }



def derive_default_output(cfg_output: str) -> Path:
    if cfg_output:
        return Path(cfg_output)
    return default_output_path()



def detect_ean_column(df: pd.DataFrame) -> pd.Series:
    normalized = {normalize_text(c): c for c in df.columns}
    for candidate in ["ean", "cod ean", "codigo de barras", "código de barras", "codigo", "código"]:
        if candidate in normalized:
            return df[normalized[candidate]]
    return df.iloc[:, 0]



def load_eans(input_path: Path) -> List[str]:
    if not input_path.exists():
        raise FileNotFoundError(f"Arquivo de entrada não encontrado: {input_path}")

    suffix = input_path.suffix.lower()
    eans: List[str] = []

    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(input_path)
        col = detect_ean_column(df)
        eans = [str(v).strip() for v in col.tolist() if str(v).strip() and str(v).strip().lower() != "nan"]
    elif suffix == ".csv":
        df = pd.read_csv(input_path)
        col = detect_ean_column(df)
        eans = [str(v).strip() for v in col.tolist() if str(v).strip() and str(v).strip().lower() != "nan"]
    elif suffix == ".txt":
        with input_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    eans.append(line)
    else:
        raise ValueError(f"Formato de entrada não suportado: {suffix}")

    eans_limpos = []
    seen = set()
    for ean in eans:
        ean_limpo = re.sub(r"\D", "", ean)
        if not ean_limpo:
            continue
        if ean_limpo not in seen:
            seen.add(ean_limpo)
            eans_limpos.append(ean_limpo)
    return eans_limpos



def build_driver(headless: bool) -> WebDriver:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=pt-BR")
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1600,1200")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)



def safe_click(driver: WebDriver, element) -> None:
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)



def click_first(driver: WebDriver, selectors: Iterable[tuple], timeout: int, desc: str):
    last = None
    for by, sel in selectors:
        try:
            el = wait(driver, timeout).until(EC.element_to_be_clickable((by, sel)))
            safe_click(driver, el)
            return el
        except Exception as exc:
            last = exc
    raise TimeoutException(f"Não encontrei/clicável: {desc}. Último erro: {last}")



def wait_visible(driver: WebDriver, selectors: Iterable[tuple], timeout: int, desc: str):
    last = None
    for by, sel in selectors:
        try:
            return wait(driver, timeout).until(EC.visibility_of_element_located((by, sel)))
        except Exception as exc:
            last = exc
    raise TimeoutException(f"Não encontrei visível: {desc}. Último erro: {last}")



def clear_and_type(element, text: str) -> None:
    element.click()
    element.send_keys(Keys.CONTROL, "a")
    element.send_keys(Keys.DELETE)
    element.send_keys(text)



def login_mercadofarma(driver: WebDriver, usuario: str, senha: str) -> None:
    log("Abrindo Mercado Farma...")
    driver.get(MF_URL)

    try:
        click_first(driver, [(By.XPATH, "//button[contains(., 'Rejeitar')]")], timeout=4, desc="rejeitar cookies")
    except Exception:
        pass

    click_first(
        driver,
        [
            (By.XPATH, "//button[.//span[contains(text(), 'Entrar como representante')]]"),
            (By.XPATH, "//*[contains(., 'Entrar como representante') and (self::button or self::a or @role='button') ]"),
        ],
        timeout=30,
        desc="Entrar como representante",
    )

    click_first(driver, [(By.ID, "social-saml")], timeout=30, desc="Active Directory")

    user = wait_visible(driver, [(By.ID, "userNameInput")], timeout=60, desc="usuário")
    pwd = wait_visible(driver, [(By.ID, "passwordInput")], timeout=20, desc="senha")
    clear_and_type(user, usuario)
    clear_and_type(pwd, senha)

    click_first(driver, [(By.ID, "submitButton")], timeout=20, desc="entrar")
    wait(driver, 90).until(lambda d: "mercadofarma.com.br" in d.current_url.lower() and "adfs" not in d.current_url.lower())
    log("Login concluído.")



def selecionar_cnpj(driver: WebDriver, cnpj: str) -> None:
    log(f"Selecionando CNPJ {cnpj}...")
    campo = wait_visible(
        driver,
        [
            (By.CSS_SELECTOR, "input[placeholder*='CNPJ']"),
            (By.XPATH, "//input[contains(@placeholder,'CNPJ')]"),
        ],
        timeout=40,
        desc="campo CNPJ",
    )
    clear_and_type(campo, cnpj)
    time.sleep(2)

    item = wait(driver, 30).until(
        EC.element_to_be_clickable((By.XPATH, f"//div[contains(@data-value, '{cnpj}') or contains(., '{cnpj}')]"))
    )
    safe_click(driver, item)
    time.sleep(2)

    click_first(
        driver,
        [
            (By.XPATH, "//a[contains(., 'Catálogo A a Z') ]"),
            (By.XPATH, "//*[normalize-space()='Catálogo A a Z']"),
        ],
        timeout=40,
        desc="Catálogo A a Z",
    )
    wait_visible(driver, [(By.NAME, "term")], timeout=30, desc="campo de busca do catálogo")
    log("Catálogo carregado.")



def limpar_busca_catalogo(driver: WebDriver):
    campo = wait_visible(
        driver,
        [
            (By.NAME, "term"),
            (By.CSS_SELECTOR, "input[placeholder*='EAN']"),
            (By.CSS_SELECTOR, "input[placeholder*='Procure']"),
        ],
        timeout=20,
        desc="campo de busca do catálogo",
    )
    try:
        campo.click()
        campo.send_keys(Keys.CONTROL, "a")
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
    return campo



def buscar_ean(driver: WebDriver, ean: str) -> None:
    campo = limpar_busca_catalogo(driver)
    campo.send_keys(ean)
    campo.send_keys(Keys.ENTER)
    time.sleep(1.5)



def fechar_popover(driver: WebDriver) -> None:
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.4)
    except Exception:
        pass



def obter_nome_produto(driver: WebDriver) -> str:
    selectors = [
        (By.XPATH, "//small[@role='button' and contains(@class, 'cursor-pointer')]"),
        (By.XPATH, "//small[contains(@class, 'cursor-pointer')]"),
        (By.XPATH, "//h5[contains(@class,'font-semibold')]"),
    ]
    el = wait_visible(driver, selectors, timeout=10, desc="nome do produto")
    return (el.text or "").strip()



def abrir_popover_distribuidores(driver: WebDriver) -> None:
    click_first(
        driver,
        [
            (By.CSS_SELECTOR, "[data-slot='popover-trigger']"),
            (By.XPATH, "//*[@data-slot='popover-trigger']"),
        ],
        timeout=10,
        desc="popover de distribuidores",
    )
    wait_visible(
        driver,
        [
            (By.CSS_SELECTOR, "[data-slot='popover-content']"),
            (By.XPATH, "//*[@data-slot='popover-content']"),
        ],
        timeout=8,
        desc="conteúdo do popover",
    )
    time.sleep(0.6)



def rows_buybox(driver: WebDriver):
    selectors = [
        (By.CSS_SELECTOR, "[data-test-id='buybox-item']"),
        (By.CSS_SELECTOR, "[data-testid='buybox-item']"),
    ]
    for by, sel in selectors:
        rows = [e for e in driver.find_elements(by, sel) if e.is_displayed()]
        if rows:
            return rows
    return []



def safe_text(root, selectors: Iterable[tuple]) -> str:
    for by, sel in selectors:
        try:
            el = root.find_element(by, sel)
            text = (el.text or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""



def extract_buybox_row(row, ean: str, nome_produto: str) -> Optional[dict]:
    try:
        nome_dist = safe_text(
            row,
            [
                (By.CSS_SELECTOR, "p.font-open-sans"),
                (By.XPATH, ".//p[contains(@class,'font-open-sans') or contains(@class,'text-black') ]"),
            ],
        )
        if not nome_dist:
            nome_dist = "DISTRIBUIDORA NÃO IDENTIFICADA"

        estoque_txt = safe_text(row, [(By.CSS_SELECTOR, "small.text-primary"), (By.XPATH, ".//small[contains(@class,'text-primary')]")])
        pf_dist_txt = safe_text(row, [(By.XPATH, ".//small[contains(text(), 'PF Dist.')]")])
        preco_final_txt = safe_text(row, [(By.CSS_SELECTOR, "h5"), (By.XPATH, ".//h5")])
        sem_imposto_txt = safe_text(row, [(By.XPATH, ".//small[contains(text(), 'Sem imposto')]")])
        desconto_txt = safe_text(row, [(By.XPATH, ".//span[contains(text(), '%')]")])

        return {
            "EAN": ean,
            "NOME DO PRODUTO": nome_produto,
            "DISTRIBUIDORA": nome_dist,
            "ESTOQUE": extrair_valor_numerico(estoque_txt, "estoque"),
            "DESCONTO (%)": extrair_valor_numerico(desconto_txt, "desconto"),
            "PF DIST. (R$)": extrair_valor_numerico(pf_dist_txt),
            "PREÇO FINAL (R$)": extrair_valor_numerico(preco_final_txt),
            "SEM IMPOSTO (R$)": extrair_valor_numerico(sem_imposto_txt),
            "DATA": now_str(),
            "STATUS": "OK",
            "ERRO": "",
        }
    except StaleElementReferenceException:
        return None



def processar_ean(driver: WebDriver, ean: str, debug_dir: Path) -> List[dict]:
    buscar_ean(driver, ean)

    try:
        nome_produto = obter_nome_produto(driver)
        abrir_popover_distribuidores(driver)
        items = rows_buybox(driver)
        if not items:
            raise TimeoutException("Nenhuma linha de buybox encontrada.")

        registros = []
        for item in items:
            row = extract_buybox_row(item, ean, nome_produto)
            if row:
                registros.append(row)

        if not registros:
            raise TimeoutException("Nenhuma distribuidora pôde ser lida.")

        fechar_popover(driver)
        return registros
    except Exception as exc:
        save_debug(driver, debug_dir, f"ean_{ean}")
        fechar_popover(driver)
        return [{
            "EAN": ean,
            "NOME DO PRODUTO": "PRODUTO NÃO ENCONTRADO",
            "DISTRIBUIDORA": "",
            "ESTOQUE": 0,
            "DESCONTO (%)": 0.0,
            "PF DIST. (R$)": 0.0,
            "PREÇO FINAL (R$)": 0.0,
            "SEM IMPOSTO (R$)": 0.0,
            "DATA": now_str(),
            "STATUS": "ERRO",
            "ERRO": str(exc),
        }]



def run_extraction(usuario: str, senha: str, cnpj: str, input_path: Path, output_path: Path, debug_dir: Path, headless: bool) -> int:
    eans = load_eans(input_path)
    if not eans:
        raise RuntimeError("Nenhum EAN válido foi encontrado no arquivo de entrada.")

    log(f"Total de EANs para extrair: {len(eans)}")
    log(f"Arquivo de entrada: {input_path}")
    log(f"Arquivo de saída: {output_path}")
    log(f"Usando credenciais salvas no sistema para o CNPJ: {cnpj}")

    rows: List[dict] = []
    driver: Optional[WebDriver] = None

    try:
        driver = build_driver(headless=headless)
        login_mercadofarma(driver, usuario, senha)
        selecionar_cnpj(driver, cnpj)

        for idx, ean in enumerate(eans, start=1):
            log(f"[{idx}/{len(eans)}] Extraindo EAN {ean}...")
            rows.extend(processar_ean(driver, ean, debug_dir))

            if len(rows) % FLUSH_BUFFER_EVERY == 0:
                save_results(rows, output_path)
                log(f"Parcial salva com {len(rows)} linhas.")

        save_results(rows, output_path)
        log(f"Extração concluída. Linhas salvas: {len(rows)}")
        return 0
    except Exception as exc:
        if driver:
            save_debug(driver, debug_dir, "falha_geral")
        if rows:
            save_results(rows, output_path)
        log(f"Falha geral: {exc}")
        return 1
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extrator Mercado Farma integrado ao painel.")
    parser.add_argument("--input", dest="input_path", help="Arquivo de entrada com os EANs.")
    parser.add_argument("--output", dest="output_path", help="Arquivo Excel de saída.")
    parser.add_argument("--usuario", dest="usuario", help="Usuário do Mercado Farma.")
    parser.add_argument("--senha", dest="senha", help="Senha do Mercado Farma.")
    parser.add_argument("--cnpj", dest="cnpj", help="CNPJ do cliente.")
    parser.add_argument("--headless", action="store_true", help="Executa oculto.")
    parser.add_argument("--show-browser", action="store_true", help="Mostra o navegador durante a extração.")
    parser.add_argument("--debug-dir", dest="debug_dir", help="Pasta para salvar prints e HTML de erro.")
    return parser.parse_args()



def main() -> int:
    args = parse_args()
    cfg = load_panel_creds()

    usuario = (args.usuario or cfg["usuario"]).strip()
    senha = (args.senha or cfg["senha"]).strip()
    cnpj = clean_cnpj(args.cnpj or cfg["cnpj"])

    if not usuario or not senha or not cnpj:
        log("Credenciais incompletas no sistema. Abra o painel > Importação > salve login, senha e CNPJ do Mercado Farma.")
        return 1

    input_path = Path(args.input_path).expanduser().resolve() if args.input_path else Path(cfg["input"]).expanduser().resolve() if cfg["input"] else default_input_path().resolve()
    output_path = Path(args.output_path).expanduser().resolve() if args.output_path else derive_default_output(cfg["output"]).expanduser().resolve()
    debug_dir = Path(args.debug_dir).expanduser().resolve() if args.debug_dir else Path(cfg["debug_dir"]).expanduser().resolve() if cfg["debug_dir"] else default_debug_dir().resolve()

    if not input_path.exists():
        log(f"Arquivo base não encontrado: {input_path}")
        log("Confirme se existe a planilha data/PRODUTOS COM EAN - POR LANCAMENTOS-PRIORITARIOS-LINHA.xlsx dentro da pasta do painel.")
        return 1

    headless = True if args.headless else False
    if args.show_browser:
        headless = False

    return run_extraction(usuario, senha, cnpj, input_path, output_path, debug_dir, headless)



if __name__ == "__main__":
    sys.exit(main())
