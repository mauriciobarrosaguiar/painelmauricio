import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, ElementClickInterceptedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


BUSSOLA_URL = "https://bussolaweb.bussola.mercadofarma.com.br/login"
BUSSOLA_ANALISE_URL = "https://bussolaweb.bussola.mercadofarma.com.br/"


def log(msg: str):
    print(f"[BUSSOLA] {msg}", flush=True)


def build_driver(download_dir: Path, headless: bool = False):
    download_dir.mkdir(parents=True, exist_ok=True)
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    prefs = {
        "download.default_directory": str(download_dir.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    options.add_experimental_option("prefs", prefs)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(90)
    return driver


def wait(driver, seconds=30):
    return WebDriverWait(driver, seconds)


def js_click(driver, element):
    driver.execute_script("arguments[0].click();", element)


def safe_click(driver, element):
    try:
        element.click()
    except Exception:
        js_click(driver, element)


def click_first(driver, selectors, timeout=25, desc="elemento"):
    last_err = None
    for by, sel in selectors:
        try:
            el = wait(driver, timeout).until(EC.element_to_be_clickable((by, sel)))
            safe_click(driver, el)
            return el
        except Exception as e:
            last_err = e
    raise TimeoutException(f"Não encontrei/clickável: {desc}. Último erro: {last_err}")


def wait_visible(driver, selectors, timeout=25, desc="elemento"):
    last_err = None
    for by, sel in selectors:
        try:
            return wait(driver, timeout).until(EC.visibility_of_element_located((by, sel)))
        except Exception as e:
            last_err = e
    raise TimeoutException(f"Não encontrei visível: {desc}. Último erro: {last_err}")


def esperar_download(download_dir: Path, timeout=120) -> Path:
    log("Aguardando download terminar...")
    start = time.time()
    while time.time() - start < timeout:
        arquivos = [p for p in download_dir.iterdir() if p.is_file()]
        temporarios = [p for p in arquivos if p.suffix.lower() in {".crdownload", ".tmp"}]
        csvs = [p for p in arquivos if p.suffix.lower() == ".csv"]
        if csvs and not temporarios:
            mais_recente = max(csvs, key=lambda p: p.stat().st_mtime)
            time.sleep(1)
            return mais_recente
        time.sleep(1)
    raise TimeoutException("Download não concluído no tempo esperado.")


def entrar_bussola(driver, usuario: str, senha: str):
    log("Abrindo login do Bússola...")
    driver.get(BUSSOLA_URL)

    click_first(driver, [
        (By.XPATH, "//a[normalize-space()='Entrar']"),
        (By.XPATH, "//a[contains(., 'Entrar')]"),
    ], desc="botão Entrar")

    click_first(driver, [
        (By.XPATH, "//*[contains(@class,'kc-social-provider-name') and contains(., 'Active Directory')]"),
        (By.XPATH, "//*[normalize-space()='Active Directory']"),
    ], timeout=40, desc="Active Directory")

    user_input = wait_visible(driver, [
        (By.ID, "userNameInput"),
    ], timeout=60, desc="campo usuário")
    pass_input = wait_visible(driver, [
        (By.ID, "passwordInput"),
    ], timeout=30, desc="campo senha")

    user_input.clear()
    user_input.send_keys(usuario)
    pass_input.clear()
    pass_input.send_keys(senha)

    click_first(driver, [
        (By.ID, "submitButton"),
        (By.XPATH, "//*[normalize-space()='Entrar' and @id='submitButton']"),
    ], desc="submit login")

    wait(driver, 90).until(
        lambda d: "bussolaweb.bussola.mercadofarma.com.br" in d.current_url
        and "login" not in d.current_url.lower()
    )
    log("Login concluído.")


def periodo_90_dias():
    hoje = datetime.now().date()
    inicio = hoje - timedelta(days=89)
    return inicio.strftime("%d/%m/%Y"), hoje.strftime("%d/%m/%Y")


def abrir_analise_com_periodo(driver):
    data_inicial, data_final = periodo_90_dias()
    url = f"{BUSSOLA_ANALISE_URL}?page=1&from={quote(data_inicial)}&to={quote(data_final)}"
    log(f"Abrindo análise direto com período: {data_inicial} até {data_final}")
    driver.get(url)

    wait_visible(driver, [
        (By.XPATH, "//*[contains(., 'Análise de pedidos') ]"),
        (By.XPATH, "//button[contains(., 'Exportar')]"),
        (By.XPATH, "//*[@data-slot='dropdown-menu-trigger' and contains(., 'Exportar') ]"),
    ], timeout=60, desc="tela Análise de pedidos")

    time.sleep(2)


def abrir_menu_exportar(driver):
    log("Abrindo menu Exportar...")
    click_first(driver, [
        (By.XPATH, "//button[contains(., 'Exportar')]"),
        (By.XPATH, "//*[@data-slot='dropdown-menu-trigger' and contains(., 'Exportar')]"),
    ], timeout=35, desc="Exportar")

    wait(driver, 20).until(
        lambda d: len([el for el in d.find_elements(By.XPATH, "//div[@role='menuitem']") if el.is_displayed()]) >= 1
    )
    time.sleep(0.8)


def selecionar_csv(driver):
    log("Selecionando CSV...")
    ultima_ex = None

    for tentativa in range(1, 6):
        try:
            abrir_menu_exportar(driver)

            itens = [el for el in driver.find_elements(By.XPATH, "//div[@role='menuitem']") if el.is_displayed()]
            for item in itens:
                txt = (item.text or "").strip().lower()
                if "csv" in txt:
                    safe_click(driver, item)
                    time.sleep(2)
                    return

            menus = [el for el in driver.find_elements(By.XPATH, "//div[@role='menu' and @data-state='open']") if el.is_displayed()]
            if menus:
                itens2 = [el for el in menus[-1].find_elements(By.XPATH, ".//*[@role='menuitem']") if el.is_displayed()]
                for item in itens2:
                    txt = (item.text or "").strip().lower()
                    if "csv" in txt:
                        safe_click(driver, item)
                        time.sleep(2)
                        return

            raise TimeoutException("Menu abriu, mas não localizei CSV visível.")
        except (StaleElementReferenceException, ElementClickInterceptedException, TimeoutException) as e:
            ultima_ex = e
            log(f"Tentativa {tentativa}/5 falhou ao clicar em CSV: {e}")
            time.sleep(1.5)

    raise TimeoutException(f"Não encontrei a opção CSV após várias tentativas. Último erro: {ultima_ex}")


def normalizar_csv(csv_path: Path, saida_dir: Path):
    log(f"Lendo arquivo baixado: {csv_path.name}")
    saida_dir.mkdir(parents=True, exist_ok=True)

    df = None
    for sep in [";", ",", "\t"]:
        for enc in ["utf-8-sig", "latin1", "cp1252"]:
            try:
                df = pd.read_csv(csv_path, sep=sep, encoding=enc, dtype=str)
                if df.shape[1] > 1:
                    break
            except Exception:
                pass
        if df is not None and df.shape[1] > 1:
            break

    if df is None or df.shape[1] <= 1:
        raise RuntimeError("Não consegui ler o CSV baixado de forma válida.")

    df = df.dropna(axis=1, how="all")

    bruto = saida_dir / f"bruto_{csv_path.name}"
    df.to_csv(bruto, index=False, sep=";", encoding="utf-8-sig")

    csv_dest = saida_dir / "Pedidos_bussola.csv"
    xlsx_dest = saida_dir / "Pedidos.xlsx"

    df.to_csv(csv_dest, index=False, sep=";", encoding="utf-8-sig")
    df.to_excel(xlsx_dest, index=False)

    log(f"CSV salvo em: {csv_dest}")
    log(f"XLSX salvo em: {xlsx_dest}")
    return csv_dest, xlsx_dest


def executar(usuario: str, senha: str, saida: str = "data", downloads: Optional[str] = None, headless: bool = False):
    saida_dir = Path(saida)
    download_dir = Path(downloads) if downloads else (Path.cwd() / "downloads_bussola")

    driver = build_driver(download_dir, headless=headless)
    try:
        entrar_bussola(driver, usuario, senha)
        abrir_analise_com_periodo(driver)
        selecionar_csv(driver)
        arquivo = esperar_download(download_dir)
        normalizar_csv(arquivo, saida_dir)
        log("Extração concluída com sucesso.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Extrator de pedidos do Bússola")
    parser.add_argument("--usuario", required=True)
    parser.add_argument("--senha", required=True)
    parser.add_argument("--saida", default="data")
    parser.add_argument("--downloads", default=None)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    executar(
        usuario=args.usuario,
        senha=args.senha,
        saida=args.saida,
        downloads=args.downloads,
        headless=args.headless,
    )


if __name__ == "__main__":
    main()
