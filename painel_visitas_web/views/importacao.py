from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from config import CLIENTES_FILE, DATA_DIR, FOCO_SEMANA_FILE, INVENTARIO_FILE, PEDIDOS_FILE
from services.integrations import IntegracaoCreds, choose_low_production_cnpj, load_creds, read_last_logs, save_creds
from services.repo_state import enqueue_command, load_recent_workflow_runs, load_status
from views.monitoring import render_monitor


def _save_upload(uploaded_file, target_name: str):
    path = DATA_DIR / target_name
    path.write_bytes(uploaded_file.getbuffer())
    return path


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return bio.getvalue()


def _runs_df(runs: list[dict]) -> pd.DataFrame:
    if not runs:
        return pd.DataFrame()
    df = pd.DataFrame(runs).copy()
    cols = [c for c in ["ID", "Status", "Resultado", "Titulo", "Criado em", "Atualizado em", "Link"] if c in df.columns]
    return df[cols] if cols else df


def render_importacao(score_df: pd.DataFrame | None = None, produtos: pd.DataFrame | None = None):
    st.markdown('<h2 class="page-title">Importacao</h2>', unsafe_allow_html=True)
    st.caption("Automacoes, bases e acompanhamento das rotinas.")

    status = load_status()
    runs = load_recent_workflow_runs()
    creds = load_creds()
    cnpj_auto = choose_low_production_cnpj(score_df if score_df is not None else pd.DataFrame())

    st.markdown("### Execucoes")
    t1, t2 = st.tabs(["Bussola", "Mercado Farma"])
    with t1:
        render_monitor("Bussola", status.get("bussola", {}), key_prefix="monitor_bussola", empty_message="Nenhuma execucao recente do Bussola.")
    with t2:
        render_monitor("Mercado Farma", status.get("mercadofarma", {}), key_prefix="monitor_mercadofarma", empty_message="Nenhuma execucao recente do Mercado Farma.")

    st.markdown("### Disparo das rotinas")
    a1, a2 = st.columns(2)
    with a1:
        headless_bussola = st.toggle("Rodar Bussola invisivel", value=True, key="headless_bussola_importacao")
        if st.button("Atualizar Bussola agora", use_container_width=True, key="btn_atualizar_bussola_importacao"):
            _, ok, msg = enqueue_command("atualizar_bussola", {"headless": headless_bussola})
            (st.success if ok else st.error)(msg)
    with a2:
        mercado_cnpj = st.text_input("CNPJ Mercado Farma", value=creds.mercado_cnpj or cnpj_auto, key="mercado_cnpj_importacao")
        headless_mef = st.toggle("Rodar Mercado Farma invisivel", value=True, key="headless_mercadofarma_importacao")
        if st.button("Atualizar Mercado Farma agora", use_container_width=True, key="btn_atualizar_mercadofarma_importacao"):
            _, ok, msg = enqueue_command("atualizar_mercado_farma", {"headless": headless_mef, "cnpj": mercado_cnpj})
            (st.success if ok else st.error)(msg)

    st.markdown("### Bases em uso")
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Pedidos", PEDIDOS_FILE.name if PEDIDOS_FILE else "-")
    b2.metric("Painel", CLIENTES_FILE.name if CLIENTES_FILE else "-")
    b3.metric("Foco", "-" if FOCO_SEMANA_FILE is None else FOCO_SEMANA_FILE.name)
    b4.metric("Estoque", INVENTARIO_FILE.name if INVENTARIO_FILE else "-")

    with st.expander("Credenciais e referencia", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            bussola_login = st.text_input("Login Bussola", value=creds.bussola_login)
            bussola_senha = st.text_input("Senha Bussola", value=creds.bussola_senha, type="password")
        with c2:
            mercado_login = st.text_input("Login Mercado Farma", value=creds.mercado_login)
            mercado_senha = st.text_input("Senha Mercado Farma", value=creds.mercado_senha, type="password")

        mercado_cnpj_ref = st.text_input("CNPJ de referencia", value=creds.mercado_cnpj or cnpj_auto, key="mercado_cnpj_referencia")
        if st.button("Salvar referencia no painel", use_container_width=True):
            save_creds(
                IntegracaoCreds(
                    bussola_login=bussola_login,
                    bussola_senha=bussola_senha,
                    mercado_login=mercado_login,
                    mercado_senha=mercado_senha,
                    mercado_cnpj=mercado_cnpj_ref,
                )
            )
            st.success("Dados salvos.")

    st.markdown("### Atualizacao manual")
    u1, u2 = st.columns(2)
    with u1:
        up_cli = st.file_uploader("Enviar painel de clientes", type=["xlsx"], key="upload_painel_clientes")
        if st.button("Salvar painel", use_container_width=True, disabled=up_cli is None, key="btn_salvar_painel_manual"):
            _save_upload(up_cli, "PAINEL.xlsx")
            st.cache_data.clear()
            st.success("Planilha de painel atualizada.")
    with u2:
        up_foco = st.file_uploader("Enviar foco da semana", type=["xlsx"], key="upload_foco_semana")
        if st.button("Salvar foco", use_container_width=True, disabled=up_foco is None, key="btn_salvar_foco_manual"):
            _save_upload(up_foco, "FOCO_SEMANA.xlsx")
            st.cache_data.clear()
            st.success("Planilha de foco atualizada.")

    st.markdown("### Modelos")
    tpl_cli = pd.DataFrame(columns=["SETOR", "CNPJ", "RAZAO SOCIAL", "NOME FANTASIA", "ENDERECO", "BAIRRO", "CIDADE", "UF", "CEP", "NOME CONTATO", "CONTATO"])
    tpl_foco = pd.DataFrame(columns=["EAN", "PRINCIPIO ATIVO", "PESO FOCO", "OBSERVACAO"])
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    d1, d2 = st.columns(2)
    d1.download_button("Modelo painel", _xlsx_bytes(tpl_cli), "template_painel_clientes.xlsx", mime=mime, use_container_width=True)
    d2.download_button("Modelo foco", _xlsx_bytes(tpl_foco), "template_foco_semana.xlsx", mime=mime, use_container_width=True)

    if runs:
        st.markdown("### GitHub Actions")
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

    with st.expander("Ultimo log tecnico", expanded=False):
        logs = read_last_logs()
        st.text_area("Mensagens", value=logs or "Sem log ainda.", height=220, key="textarea_log_integracoes")
