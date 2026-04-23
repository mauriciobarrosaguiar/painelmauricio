from __future__ import annotations
import json, subprocess, sys, traceback
from datetime import datetime
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from config import PRODUTOS_CANONICAL_FILE, PRODUTOS_FILE
from services.cleaning import clean_produtos
from services.integrations import (
    run_bussola_download, run_mercadofarma_inventory,
    clear_mercadofarma_mass_order, run_mercadofarma_mass_order,
    load_creds,
)
from services.repo_state import load_commands, save_commands, load_status, save_status

DATA = ROOT / 'data'


def now():
    return datetime.now().strftime('%d/%m/%Y %H:%M:%S')


def git_sync(commit_msg='Atualizar status da automação'):
    try:
        subprocess.run(['git','-C', str(ROOT), 'pull', 'origin', 'main'], check=False)
        subprocess.run(['git','-C', str(ROOT), 'add', 'data'], check=False)
        res = subprocess.run(['git','-C', str(ROOT), 'diff', '--cached', '--quiet'])
        if res.returncode == 0:
            return
        subprocess.run(['git','-C', str(ROOT), 'commit', '-m', commit_msg], check=False)
        subprocess.run(['git','-C', str(ROOT), 'push', 'origin', 'main'], check=False)
    except Exception:
        pass


def set_status(chave: str, status_text: str, mensagem=''):
    status = load_status()
    status[chave] = {'ultimo_sucesso': now(), 'status': status_text, 'mensagem': mensagem}
    save_status(status)


def _produtos_df():
    candidatos = [
        PRODUTOS_CANONICAL_FILE,
        PRODUTOS_FILE,
        DATA / 'PRODUTOS COM EAN - POR LANCAMENTOS-PRIORITARIOS-LINHA.xlsx',
    ]
    vistos = set()
    for path in candidatos:
        if path is None:
            continue
        path = Path(path)
        key = str(path.resolve()) if path.exists() else str(path)
        if key in vistos or not path.exists():
            continue
        vistos.add(key)
        try:
            df = clean_produtos(pd.read_excel(path))
            if not df.empty and df['ean'].astype(str).str.strip().ne('').any():
                return df
        except Exception:
            continue
    return pd.DataFrame(columns=['ean', 'principio_ativo', 'mix_lancamentos'])


def execute_command(cmd):
    acao = cmd.get('acao')
    params = cmd.get('params', {}) or {}
    creds = load_creds()
    if acao == 'atualizar_bussola':
        out = DATA / 'Pedidos.xlsx'
        run_bussola_download(creds.bussola_login, creds.bussola_senha, out, headless=bool(params.get('headless', True)))
        set_status('bussola', 'ok', 'Atualizado pelo agente local')
        return True, 'Bússola atualizado.'
    if acao == 'atualizar_mercadofarma':
        out = DATA / 'Estoque_preco_distribuidora.xlsx'
        cnpj = params.get('cnpj') or creds.mercado_cnpj
        run_mercadofarma_inventory(creds.mercado_login, creds.mercado_senha, cnpj, _produtos_df(), out, headless=bool(params.get('headless', True)))
        set_status('mercadofarma', 'ok', 'Mercado Farma atualizado pelo agente local')
        return True, 'Mercado Farma atualizado.'
    if acao == 'limpar_pedido_mf':
        cnpj = params.get('cnpj') or creds.mercado_cnpj
        clear_mercadofarma_mass_order(creds.mercado_login, creds.mercado_senha, cnpj, headless=bool(params.get('headless', True)))
        set_status('comandos', 'ok', 'Último pedido MF limpo')
        return True, 'Pedido MF limpo.'
    if acao == 'enviar_pedido_mf':
        cart_items = params.get('cart_items', [])
        run_mercadofarma_mass_order(creds.mercado_login, creds.mercado_senha, cart_items, headless=bool(params.get('headless', True)), cupom=params.get('cupom',''))
        set_status('comandos', 'ok', 'Pedido enviado ao Mercado Farma')
        return True, 'Pedido enviado ao Mercado Farma.'
    return False, f'Ação desconhecida: {acao}'


def process_once():
    git_sync('Sincronizar comandos')
    cmds = load_commands()
    changed = False
    for cmd in cmds.get('commands', []):
        if cmd.get('status') != 'pendente':
            continue
        cmd['status'] = 'executando'
        cmd['atualizado_em'] = now()
        save_commands(cmds)
        git_sync(f"Comando {cmd.get('acao')} em execução")
        try:
            ok, msg = execute_command(cmd)
            cmd['status'] = 'concluido' if ok else 'erro'
            cmd['mensagem'] = msg
        except Exception as e:
            cmd['status'] = 'erro'
            cmd['mensagem'] = str(e) + '\n' + traceback.format_exc()[-700:]
        cmd['atualizado_em'] = now()
        changed = True
    if changed:
        save_commands(cmds)
        git_sync('Atualizar retorno dos comandos')


if __name__ == '__main__':
    process_once()
