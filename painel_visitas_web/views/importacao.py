from __future__ import annotations

import io
import re
import unicodedata
from html import escape

import pandas as pd
import streamlit as st

from config import CLIENTES_FILE, DATA_DIR, FOCO_SEMANA_FILE, INVENTARIO_FILE, PEDIDOS_FILE, PRODUTOS_CANONICAL_FILE, PRODUTOS_FILE
from services.client_overrides import clear_client_overrides
from services.discount_actions import action_template_dataframe, actions_to_dataframe, parse_discount_actions
from services.integrations import IntegracaoCreds, choose_low_production_cnpj, load_creds, read_last_logs, save_creds
from services.order_builder import build_order_dataframe
from services.repo_state import (
    command_to_monitor_block,
    enqueue_command,
    load_discount_actions,
    load_commands,
    load_latest_command,
    load_recent_workflow_runs,
    load_status,
    repo_save_bytes,
    save_discount_actions,
)
from views.monitoring import render_monitor


def _save_upload(uploaded_file, target_name: str):
    content = bytes(uploaded_file.getbuffer())
    ok, msg = repo_save_bytes(
        f"painel_visitas_web/data/{target_name}",
        content,
        f"Atualizar {target_name}",
    )
    return DATA_DIR / target_name, ok, msg


def _normalizar_nome_arquivo(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def _replace_produtos_upload(uploaded_file):
    for path in DATA_DIR.glob("*.xls*"):
        nome = _normalizar_nome_arquivo(path.name)
        is_produtos_mix = nome.startswith("produtos") and any(
            token in nome
            for token in ["mix", "ean", "combate", "prioritarios", "priotirarios", "lancamentos", "linha"]
        )
        if is_produtos_mix and path.resolve() != PRODUTOS_CANONICAL_FILE.resolve():
            path.unlink(missing_ok=True)
    return _save_upload(uploaded_file, PRODUTOS_CANONICAL_FILE.name)


def _replace_painel_upload(uploaded_file):
    for path in DATA_DIR.glob("*.xls*"):
        nome = _normalizar_nome_arquivo(path.name)
        if "painel" in nome and path.name.lower() != "painel.xlsx":
            path.unlink(missing_ok=True)
    path, ok, msg = _save_upload(uploaded_file, "PAINEL.xlsx")
    if ok:
        clear_client_overrides()
    return path, ok, msg


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return bio.getvalue()


def _parse_actions_upload(uploaded_file, default_distribuidora: str = "", default_validade=None) -> tuple[list[dict], list[str]]:
    if uploaded_file is None:
        return [], ["Selecione uma planilha de acoes."]
    try:
        df = pd.read_excel(uploaded_file, dtype=str).fillna("")
    except Exception as exc:
        return [], [f"Nao foi possivel ler a planilha de acoes: {exc}"]
    text = df.to_csv(index=False, sep="\t")
    return parse_discount_actions(text, default_distribuidora=default_distribuidora, default_validade=default_validade)


def _runs_df(runs: list[dict]) -> pd.DataFrame:
    if not runs:
        return pd.DataFrame()
    df = pd.DataFrame(runs).copy()
    cols = [c for c in ["ID", "Status", "Resultado", "Titulo", "Criado em", "Atualizado em", "Link"] if c in df.columns]
    return df[cols] if cols else df


def _money(value) -> str:
    try:
        return f"R$ {float(pd.to_numeric(value, errors='coerce') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _pct(value) -> str:
    try:
        return f"{float(value):.2f}%".replace(".", ",")
    except Exception:
        return "0,00%"


def _format_date(value: str) -> str:
    dt = pd.to_datetime(value, errors="coerce")
    return "-" if pd.isna(dt) else dt.strftime("%d/%m/%Y")


def _pedido_monitor_block(status: dict) -> dict:
    latest = load_latest_command({"enviar_pedido_mf", "gerar_pedido_mercado_farma", "limpar_pedido_mf"})
    latest_block = command_to_monitor_block(latest)
    status_block = dict(status.get("comandos", {}) or {})
    if latest_block:
        merged = dict(status_block)
        merged.update({k: v for k, v in latest_block.items() if v not in ("", None, [], {})})
        return merged
    return status_block


def _pedido_history() -> list[dict]:
    data = load_commands()
    comandos = list(data.get("commands", []) or [])
    permitidos = {"enviar_pedido_mf", "gerar_pedido_mercado_farma"}
    return [cmd for cmd in reversed(comandos) if str(cmd.get("acao", "")).strip() in permitidos]


def _pedido_summary_row(cmd: dict) -> dict:
    params = dict(cmd.get("params") or {})
    df = build_order_dataframe(list(params.get("cart_items") or []))
    header = df.iloc[0].to_dict() if not df.empty else {}
    distribs = sorted(df["Distribuidora"].astype(str).unique().tolist()) if not df.empty else []
    return {
        "Comando": str(cmd.get("id", ""))[-8:],
        "Status": str(cmd.get("status", "") or "-"),
        "Criado em": str(cmd.get("criado_em", "") or "-"),
        "Atualizado em": str(cmd.get("atualizado_em", "") or "-"),
        "Cliente": str(header.get("Cliente", "")),
        "CNPJ": str(header.get("CNPJ", "")),
        "Itens": int(df["Qtde"].sum()) if not df.empty else 0,
        "Produtos": int(df["EAN"].nunique()) if not df.empty else 0,
        "Distribuidoras": ", ".join(distribs[:3]) + (" ..." if len(distribs) > 3 else ""),
        "Total": _money(df["Total"].sum()) if not df.empty else _money(0),
    }


def _pedido_dist_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Distribuidora", "Produtos", "Quantidade", "Total"])
    resumo = (
        df.groupby("Distribuidora", as_index=False)
        .agg(Produtos=("EAN", "nunique"), Quantidade=("Qtde", "sum"), Total=("Total", "sum"))
        .sort_values(["Total", "Distribuidora"], ascending=[False, True])
    )
    resumo["Total"] = resumo["Total"].map(_money)
    return resumo


def _pedido_items_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Produto", "EAN", "Distribuidora", "Qtde", "Preco", "Total", "Cupom", "Acao"])
    for column in ["Cupom", "Acao"]:
        if column not in df.columns:
            df[column] = ""
    show = df[["Produto", "EAN", "Distribuidora", "Qtde", "Preco", "Total", "Cupom", "Acao"]].copy()
    show["Preco"] = show["Preco"].map(_money)
    show["Total"] = show["Total"].map(_money)
    return show


def _base_card(label: str, value: str):
    st.markdown(
        f"""
        <div class="base-mini-card">
            <div class="base-mini-title">{escape(label)}</div>
            <div class="base-mini-main">{escape(value or "-")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_importacao(score_df: pd.DataFrame | None = None, produtos: pd.DataFrame | None = None, inventario: pd.DataFrame | None = None):
    st.markdown('<h2 class="page-title">Importacao</h2>', unsafe_allow_html=True)
    st.caption("Automacoes, bases e acompanhamento das rotinas.")

    status = load_status()
    runs = load_recent_workflow_runs()
    creds = load_creds()
    cnpj_auto = choose_low_production_cnpj(score_df if score_df is not None else pd.DataFrame())

    st.markdown("### Execucoes")
    t1, t2, t3 = st.tabs(["Bussola", "Mercado Farma", "Pedido Gerado"])
    with t1:
        render_monitor("Bussola", status.get("bussola", {}), key_prefix="monitor_bussola", empty_message="Nenhuma execucao recente do Bussola.")
    with t2:
        render_monitor("Mercado Farma", status.get("mercadofarma", {}), key_prefix="monitor_mercadofarma", empty_message="Nenhuma execucao recente do Mercado Farma.")
    with t3:
        render_monitor("Pedido Gerado", _pedido_monitor_block(status), key_prefix="monitor_pedido_gerado", empty_message="Nenhum pedido recente do Mercado Farma.")
        pedidos = _pedido_history()
        if not pedidos:
            st.info("Nenhum pedido enviado ao Mercado Farma foi encontrado na fila do painel.")
        else:
            resumo_df = pd.DataFrame([_pedido_summary_row(cmd) for cmd in pedidos])
            st.dataframe(resumo_df, use_container_width=True, hide_index=True)
            st.markdown("### Lista de pedidos gerados")
            for pos, cmd in enumerate(pedidos[:20], start=1):
                params = dict(cmd.get("params") or {})
                df = build_order_dataframe(list(params.get("cart_items") or []))
                header = df.iloc[0].to_dict() if not df.empty else {}
                titulo = f"Pedido {pos} | {str(cmd.get('status', '-') or '-').upper()} | {str(cmd.get('criado_em', '-') or '-')}"
                with st.expander(titulo, expanded=pos == 1):
                    top = st.columns(4)
                    top[0].metric("Cliente", str(header.get("Cliente", "") or "-"))
                    top[1].metric("CNPJ", str(header.get("CNPJ", "") or "-"))
                    top[2].metric("Itens", str(int(df["Qtde"].sum())) if not df.empty else "0")
                    top[3].metric("Total", _money(df["Total"].sum()) if not df.empty else _money(0))
                    st.caption(f"Comando: {cmd.get('id', '-')} | Atualizado em: {cmd.get('atualizado_em', '-')}")
                    if str(cmd.get("mensagem", "")).strip():
                        st.caption(str(cmd.get("mensagem", "")))
                    st.markdown("**Resumo por distribuidora**")
                    st.dataframe(_pedido_dist_df(df), use_container_width=True, hide_index=True)
                    st.markdown("**Itens do pedido**")
                    st.dataframe(_pedido_items_df(df), use_container_width=True, hide_index=True)

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
    b1, b2, b3, b4, b5 = st.columns(5)
    with b1:
        _base_card("Pedidos", PEDIDOS_FILE.name if PEDIDOS_FILE else "-")
    with b2:
        _base_card("Painel", CLIENTES_FILE.name if CLIENTES_FILE else "-")
    with b3:
        _base_card("Produtos / Mix", PRODUTOS_CANONICAL_FILE.name if PRODUTOS_CANONICAL_FILE.exists() else (PRODUTOS_FILE.name if PRODUTOS_FILE else "-"))
    with b4:
        _base_card("Foco", "-" if FOCO_SEMANA_FILE is None else FOCO_SEMANA_FILE.name)
    with b5:
        _base_card("Estoque", INVENTARIO_FILE.name if INVENTARIO_FILE else "-")

    with st.expander("Login unico do painel", expanded=True):
        st.caption("Use o mesmo login e senha para Bussola, Mercado Farma e envio de pedido. O CNPJ abaixo fica so para a extracao do Mercado Farma.")
        c1, c2 = st.columns(2)
        with c1:
            login_unico = st.text_input("Login unico", value=creds.login or creds.bussola_login or creds.mercado_login)
        with c2:
            senha_unica = st.text_input("Senha unica", value=creds.senha or creds.bussola_senha or creds.mercado_senha, type="password")
        mercado_cnpj_ref = st.text_input("CNPJ de referencia", value=creds.mercado_cnpj or cnpj_auto, key="mercado_cnpj_referencia")
        if st.button("Salvar login e referencia", use_container_width=True):
            save_creds(
                IntegracaoCreds(
                    login=login_unico,
                    senha=senha_unica,
                    mercado_cnpj=mercado_cnpj_ref,
                )
            )
            st.success("Dados salvos.")

    st.markdown("### Atualizacao manual")
    u1, u2, u3 = st.columns(3)
    with u1:
        up_cli = st.file_uploader("Enviar painel de clientes", type=["xlsx"], key="upload_painel_clientes")
        if st.button("Salvar painel", use_container_width=True, disabled=up_cli is None, key="btn_salvar_painel_manual"):
            _, ok, msg = _replace_painel_upload(up_cli)
            st.cache_data.clear()
            (st.success if ok else st.warning)(f"Planilha de painel atualizada. {msg}")
    with u2:
        up_foco = st.file_uploader("Enviar foco da semana", type=["xlsx"], key="upload_foco_semana")
        if st.button("Salvar foco", use_container_width=True, disabled=up_foco is None, key="btn_salvar_foco_manual"):
            _, ok, msg = _save_upload(up_foco, "FOCO_SEMANA.xlsx")
            st.cache_data.clear()
            (st.success if ok else st.warning)(f"Planilha de foco atualizada. {msg}")
    with u3:
        up_prod = st.file_uploader("Enviar produtos / mix", type=["xlsx"], key="upload_produtos_mix")
        if st.button("Salvar produtos", use_container_width=True, disabled=up_prod is None, key="btn_salvar_produtos_mix"):
            _, ok, msg = _replace_produtos_upload(up_prod)
            st.cache_data.clear()
            (st.success if ok else st.warning)(f"Base de produtos atualizada. A planilha antiga foi substituida. {msg}")

    st.markdown("### Acoes de desconto")
    inv_ref = inventario.copy() if isinstance(inventario, pd.DataFrame) else pd.DataFrame()
    dist_options = ["Usar coluna da planilha"]
    if not inv_ref.empty and "distribuidora" in inv_ref.columns:
        dist_options += sorted([d for d in inv_ref["distribuidora"].dropna().astype(str).unique().tolist() if d])
    a1, a2 = st.columns([1.1, 0.9])
    default_dist = a1.selectbox("Distribuidora padrao", dist_options, key="acoes_dist_padrao")
    validade_padrao = a2.date_input("Validade padrao", format="DD/MM/YYYY", key="acoes_validade_padrao")
    replace_actions = st.checkbox("Substituir acoes atuais ao importar", value=False, key="acoes_replace_all")
    uploaded_actions = st.file_uploader("Importar planilha padrao de acoes", type=["xlsx"], key="upload_acoes_promocionais")
    pasted = st.text_area(
        "Cole aqui as acoes copiadas da planilha",
        placeholder="TIPO_ACAO\tNOME_ACAO\tEAN\tPRODUTO\tDESCONTO\tDISTRIBUIDORA\tCUPOM\tVALIDADE_DA_ACAO\tQTD_MINIMA\tQTD_DE\tQTD_ATE",
        height=170,
        key="acoes_coladas_texto",
    )
    c1, c2, c3 = st.columns([1, 1, 1])
    if c1.button("Importar planilha de acoes", use_container_width=True, key="btn_importar_planilha_acoes"):
        default = "" if default_dist == "Usar coluna da planilha" else default_dist
        records, errors = _parse_actions_upload(uploaded_actions, default_distribuidora=default, default_validade=validade_padrao)
        if records:
            state = load_discount_actions()
            current = [] if replace_actions else list(state.get("acoes", []) or [])
            current.extend(records)
            ok, msg = save_discount_actions({"acoes": current})
            st.cache_data.clear()
            (st.success if ok else st.warning)(f"{len(records)} acao(oes) importada(s). {msg}")
        if errors:
            st.warning(" | ".join(errors[:5]))
    if c2.button("Adicionar acoes coladas", use_container_width=True, key="btn_adicionar_acoes_coladas"):
        default = "" if default_dist == "Usar coluna da planilha" else default_dist
        records, errors = parse_discount_actions(pasted, default_distribuidora=default, default_validade=validade_padrao)
        if records:
            state = load_discount_actions()
            current = [] if replace_actions else list(state.get("acoes", []) or [])
            current.extend(records)
            ok, msg = save_discount_actions({"acoes": current})
            st.cache_data.clear()
            (st.success if ok else st.warning)(f"{len(records)} acao(oes) adicionada(s). {msg}")
        if errors:
            st.warning(" | ".join(errors[:5]))
    if c3.button("Remover acoes vencidas", use_container_width=True, key="btn_remover_acoes_vencidas"):
        state = load_discount_actions()
        df_actions = actions_to_dataframe(state.get("acoes", []))
        keep = df_actions[df_actions["status"] == "Vigente"].to_dict("records") if not df_actions.empty else []
        ok, msg = save_discount_actions({"acoes": keep})
        st.cache_data.clear()
        (st.success if ok else st.warning)(f"Acoes vencidas removidas. {msg}")

    actions_state = load_discount_actions()
    actions_df = actions_to_dataframe(actions_state.get("acoes", []))
    with st.expander("Acoes cadastradas", expanded=False):
        if actions_df.empty:
            st.caption("Nenhuma acao cadastrada.")
        else:
            show_actions = actions_df.copy()
            show_actions.rename(
                columns={
                    "tipo_acao": "Tipo",
                    "nome_acao": "Acao",
                    "ean": "EAN",
                    "produto": "Produto",
                    "desconto": "Desconto",
                    "distribuidora": "Distribuidora",
                    "cupom": "Cupom",
                    "validade": "Validade",
                    "qtd_minima": "Qtd minima",
                    "qtd_de": "Qtd de",
                    "qtd_ate": "Qtd ate",
                    "status": "Status",
                },
                inplace=True,
            )
            show_actions["Desconto"] = show_actions["Desconto"].map(_pct)
            show_actions["Validade"] = show_actions["Validade"].map(_format_date)
            st.dataframe(show_actions, use_container_width=True, hide_index=True)

    st.markdown("### Modelos")
    tpl_cli = pd.DataFrame(columns=["SETOR", "CNPJ", "RAZAO SOCIAL", "NOME FANTASIA", "ENDERECO", "BAIRRO", "CIDADE", "UF", "CEP", "NOME CONTATO", "CONTATO"])
    tpl_foco = pd.DataFrame(columns=["EAN", "PRINCIPIO ATIVO", "PESO FOCO", "OBSERVACAO"])
    tpl_prod = pd.DataFrame(columns=["EAN", "PRINCIPIO ATIVO", "LINHA/COMBATE/PRIORITARIOS/LANCAMENTOS"])
    tpl_acoes = action_template_dataframe()
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    d1, d2, d3, d4 = st.columns(4)
    d1.download_button("Modelo painel", _xlsx_bytes(tpl_cli), "template_painel_clientes.xlsx", mime=mime, use_container_width=True)
    d2.download_button("Modelo foco", _xlsx_bytes(tpl_foco), "template_foco_semana.xlsx", mime=mime, use_container_width=True)
    d3.download_button("Modelo produtos", _xlsx_bytes(tpl_prod), "template_produtos_mix.xlsx", mime=mime, use_container_width=True)
    d4.download_button("Modelo acoes", _xlsx_bytes(tpl_acoes), "template_acoes_promocionais.xlsx", mime=mime, use_container_width=True)

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
