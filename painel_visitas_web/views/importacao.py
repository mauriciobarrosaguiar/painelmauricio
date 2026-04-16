from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from config import CLIENTES_FILE, FOCO_SEMANA_FILE, DATA_DIR, PEDIDOS_FILE, INVENTARIO_FILE
from services.integrations import IntegracaoCreds, load_creds, save_creds, choose_low_production_cnpj, read_last_logs
from services.repo_state import enqueue_command, load_status, load_recent_workflow_runs


def _save_upload(uploaded_file, target_name: str):
    path = DATA_DIR / target_name
    path.write_bytes(uploaded_file.getbuffer())
    return path


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return bio.getvalue()


def _status_text(bloco: dict, nome: str) -> str:
    ultimo = bloco.get("ultimo_sucesso") or bloco.get("atualizado_em") or "—"
    status = bloco.get("status") or "—"
    msg = bloco.get("mensagem") or ""
    base = f"{nome}: {ultimo} • {status}"
    return f"{base} • {msg}" if msg else base


def _bases_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Base": "Base Pedidos", "Arquivo atual": PEDIDOS_FILE.name if PEDIDOS_FILE else "-"},
            {"Base": "Painel clientes", "Arquivo atual": CLIENTES_FILE.name if CLIENTES_FILE else "-"},
            {"Base": "Foco da semana", "Arquivo atual": "-" if FOCO_SEMANA_FILE is None else FOCO_SEMANA_FILE.name},
            {"Base": "Estoque/Preços", "Arquivo atual": INVENTARIO_FILE.name if INVENTARIO_FILE else "-"},
        ]
    )


def _runs_df(runs: list[dict]) -> pd.DataFrame:
    if not runs:
        return pd.DataFrame()
    df = pd.DataFrame(runs).copy()

    rename_map = {
        "id": "ID",
        "status": "Status",
        "resultado": "Resultado",
        "titulo": "Título",
        "título": "Título",
        "criado_em": "Criado em",
        "atualizado_em": "Atualizado em",
        "link": "Link",
    }
    for src, dst in rename_map.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    cols = [c for c in ["ID", "Status", "Resultado", "Título", "Criado em", "Atualizado em", "Link"] if c in df.columns]
    if not cols:
        return df
    return df[cols]


def render_importacao(score_df: pd.DataFrame | None = None, produtos: pd.DataFrame | None = None):
    st.markdown('<h2 class="page-title">Importação</h2>', unsafe_allow_html=True)
    st.success("Modo web direto ativo: os botões abaixo disparam o GitHub Actions e atualizam o painel sem usar seu PC.")

    status = load_status()
    s1, s2 = st.columns(2)

    with s1:
        st.caption(_status_text(status.get("bussola", {}), "Bússola"))

    with s2:
        st.caption(_status_text(status.get("mercadofarma", {}), "Mercado Farma"))

    gh = status.get("github_actions", {})
    gh_msg = f"GitHub Actions: {gh.get('status', '—')}"
    if gh.get("mensagem"):
        gh_msg += f" • {gh.get('mensagem')}"
    st.info(gh_msg)

    st.markdown("### Bases atuais")
    st.dataframe(_bases_df(), use_container_width=True, hide_index=True)

    creds = load_creds()
    st.markdown("### Credenciais dos sistemas")
    st.warning("As automações web usam as credenciais salvas nos GitHub Secrets. Os campos abaixo servem como referência e backup local do painel.")

    c1, c2 = st.columns(2)
    with c1:
        bussola_login = st.text_input("Login Bússola", value=creds.bussola_login)
        bussola_senha = st.text_input("Senha Bússola", value=creds.bussola_senha, type="password")
    with c2:
        mercado_login = st.text_input("Login Mercado Farma", value=creds.mercado_login)
        mercado_senha = st.text_input("Senha Mercado Farma", value=creds.mercado_senha, type="password")

    cnpj_auto = choose_low_production_cnpj(score_df if score_df is not None else pd.DataFrame())
    mercado_cnpj = st.text_input("CNPJ para acessar o Mercado Farma", value=creds.mercado_cnpj or cnpj_auto)

    if st.button("Salvar referência no painel", use_container_width=True):
        save_creds(
            IntegracaoCreds(
                bussola_login=bussola_login,
                bussola_senha=bussola_senha,
                mercado_login=mercado_login,
                mercado_senha=mercado_senha,
                mercado_cnpj=mercado_cnpj,
            )
        )
        st.success("Dados de referência salvos no painel.")

    st.markdown("### Automatizar direto pelo GitHub")
    a1, a2 = st.columns(2)

    with a1:
        headless_bussola = st.toggle("Bússola invisível", value=True, key="headless_bussola_importacao")
        if st.button("Atualizar Bússola agora", use_container_width=True, key="btn_atualizar_bussola_importacao"):
            _, ok, msg = enqueue_command("atualizar_bussola", {"headless": headless_bussola})
            (st.success if ok else st.error)(msg)

    with a2:
        headless_mef = st.toggle("Mercado Farma invisível", value=True, key="headless_mercadofarma_importacao")
        if st.button("Atualizar Mercado Farma agora", use_container_width=True, key="btn_atualizar_mercadofarma_importacao"):
            _, ok, msg = enqueue_command(
                "atualizar_mercado_farma",
                {"headless": headless_mef, "cnpj": mercado_cnpj},
            )
            (st.success if ok else st.error)(msg)

    runs = load_recent_workflow_runs()
    if runs:
        st.markdown("### Últimas execuções do GitHub Actions")
        runs_df = _runs_df(runs)
        try:
            st.dataframe(
                runs_df,
                use_container_width=True,
                hide_index=True,
                column_config={"Link": st.column_config.LinkColumn("Link")},
            )
        except Exception:
            st.dataframe(runs_df, use_container_width=True, hide_index=True)

    st.markdown("### Enviar planilhas manuais")
    c1, c2 = st.columns(2)
    with c1:
        up_cli = st.file_uploader("Painel clientes", type=["xlsx"], key="upload_painel_clientes")
    with c2:
        up_foco = st.file_uploader("Foco da semana", type=["xlsx"], key="upload_foco_semana")

    b1, b2 = st.columns(2)
    if b1.button("Salvar painel", use_container_width=True, disabled=up_cli is None, key="btn_salvar_painel_manual"):
        _save_upload(up_cli, "PAINEL.xlsx")
        st.cache_data.clear()
        st.success("Planilha de painel atualizada.")
    if b2.button("Salvar foco", use_container_width=True, disabled=up_foco is None, key="btn_salvar_foco_manual"):
        _save_upload(up_foco, "FOCO_SEMANA.xlsx")
        st.cache_data.clear()
        st.success("Planilha de foco atualizada.")

    st.markdown("### Modelos padrão")
    tpl_cli = pd.DataFrame(columns=["SETOR", "CNPJ", "RAZÃO SOCIAL", "NOME FANTASIA", "ENDEREÇO", "BAIRRO", "CIDADE", "UF", "CEP", "NOME CONTATO", "CONTATO"])
    tpl_foco = pd.DataFrame(columns=["EAN", "PRINCIPIO ATIVO", "PESO FOCO", "OBSERVACAO"])
    d1, d2 = st.columns(2)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    d1.download_button("Modelo painel", _xlsx_bytes(tpl_cli), "template_painel_clientes.xlsx", mime=mime, use_container_width=True, key="btn_download_modelo_painel")
    d2.download_button("Modelo foco", _xlsx_bytes(tpl_foco), "template_foco_semana.xlsx", mime=mime, use_container_width=True, key="btn_download_modelo_foco")

    st.markdown("### Log da integração")
    logs = read_last_logs()
    st.text_area("Últimas mensagens", value=logs or "Sem log ainda.", height=220, key="textarea_log_integracoes")
