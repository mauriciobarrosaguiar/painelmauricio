from __future__ import annotations

from io import BytesIO
import re

import pandas as pd
import streamlit as st

from views.sip import load_sip_groups


def _wa_link(phone: str, msg: str = "") -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return ""
    return f"https://wa.me/55{digits}?text={msg}" if len(digits) <= 11 else f"https://wa.me/{digits}?text={msg}"


def _money(value):
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
        worksheet = writer.sheets[sheet_name[:31]]
        for idx, column in enumerate(df.columns):
            largura = max(len(str(column)), min(44, int(df[column].astype(str).str.len().fillna(0).max()) + 2 if not df.empty else 14))
            worksheet.set_column(idx, idx, largura)
    output.seek(0)
    return output.getvalue()


def _best_inv_all(inventario: pd.DataFrame) -> pd.DataFrame:
    if inventario is None or inventario.empty:
        return pd.DataFrame(columns=["ean", "distribuidora", "preco_sem_imposto", "estoque"])
    inv = inventario.copy()
    inv["preco_sem_imposto"] = pd.to_numeric(inv["preco_sem_imposto"], errors="coerce").fillna(0)
    inv["estoque"] = pd.to_numeric(inv["estoque"], errors="coerce").fillna(0)
    inv = inv[inv["estoque"] > 0].sort_values(["ean", "preco_sem_imposto", "estoque"], ascending=[True, True, False])
    return inv.drop_duplicates("ean")[["ean", "distribuidora", "preco_sem_imposto", "estoque"]]


def _cliente_header(base_cli: pd.DataFrame, nome_fantasia: str, cnpj: str) -> dict:
    base_cli = base_cli if base_cli is not None else pd.DataFrame()

    def get_first(col, default=""):
        return base_cli[col].dropna().astype(str).iloc[0] if col in base_cli.columns and not base_cli[col].dropna().empty else default

    return {
        "Cliente": nome_fantasia,
        "CNPJ": str(cnpj),
        "Empresa": nome_fantasia,
        "Razao social": get_first("razao_social", nome_fantasia),
        "Nome do comprador": get_first("nome_contato", ""),
        "Tel do comprador": get_first("contato", ""),
    }


def _add_to_cart(itens: list[dict]) -> int:
    carrinho = st.session_state.setdefault("cart_items", [])
    existentes = {(str(i.get("CNPJ", "")), str(i.get("EAN", "")), str(i.get("Distribuidora", ""))): idx for idx, i in enumerate(carrinho)}
    adicionados = 0
    for item in itens:
        key = (str(item.get("CNPJ", "")), str(item.get("EAN", "")), str(item.get("Distribuidora", "")))
        if key in existentes:
            carrinho[existentes[key]].update(item)
            carrinho[existentes[key]]["Qtde"] = int(pd.to_numeric(item.get("Qtde", 1), errors="coerce") or 1)
        else:
            carrinho.append(item)
            adicionados += 1
    st.session_state.cart_items = carrinho
    return adicionados


def _safe_int(value, default=0):
    number = pd.to_numeric(value, errors="coerce")
    return int(default if pd.isna(number) else number)


def _safe_float(value, default=0.0):
    number = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(number) else number)


def _build_cart_items(df: pd.DataFrame, cabecalho: dict) -> list[dict]:
    itens = []
    for _, row in df.iterrows():
        itens.append(
            {
                **cabecalho,
                "EAN": str(row.get("ean", "")),
                "Produto": row.get("principio_ativo", ""),
                "Distribuidora": row.get("distribuidora", "Sem distribuidora") or "Sem distribuidora",
                "Preco": _safe_float(row.get("preco_sem_imposto", 0), 0.0),
                "Estoque": _safe_int(row.get("estoque", 0), 0),
                "Mix": row.get("mix_lancamentos", "LINHA"),
                "Qtde": max(1, _safe_int(row.get("Qtde", 1), 1)),
                "Foco": bool(row.get("foco", False)),
            }
        )
    return itens


def _choose_df(title: str, df: pd.DataFrame, cabecalho: dict, key_prefix: str):
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
    if df is None or df.empty:
        st.info("Nenhum produto nesta lista.")
        return
    work = df.copy().drop_duplicates("ean")
    work["Selecionar"] = st.checkbox("Selecionar todos os produtos", key=f"all_{key_prefix}")
    work["Qtde"] = 1
    show = work[["Selecionar", "ean", "principio_ativo", "mix_lancamentos", "Qtde"]].copy()
    show.columns = ["Sel.", "EAN", "Produto", "Mix", "Qtde"]
    edited = st.data_editor(
        show,
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        height=min(300, 36 * (len(show) + 1)),
        key=f"editor_{key_prefix}",
        column_config={"Sel.": st.column_config.CheckboxColumn("Sel."), "Qtde": st.column_config.NumberColumn("Qtde", min_value=1, step=1), "EAN": None},
    )
    selected = edited[edited["Sel."]].copy()
    c1, c2 = st.columns([1, 1])
    if c1.button("Enviar para carrinho", key=f"btn_all_{key_prefix}", use_container_width=True):
        if selected.empty:
            st.warning("Selecione ao menos um produto.")
        else:
            subset = work[work["ean"].astype(str).isin(selected["EAN"].astype(str))].copy()
            qtd_map = dict(zip(selected["EAN"].astype(str), selected["Qtde"]))
            subset["Qtde"] = subset["ean"].astype(str).map(qtd_map).fillna(1).astype(int)
            adicionados = _add_to_cart(_build_cart_items(subset, cabecalho))
            st.success(f"{adicionados if adicionados else len(subset)} produto(s) enviado(s) ao carrinho.")
    one = c2.selectbox("Adicionar um por um", ["Escolha a opcao"] + work["principio_ativo"].tolist(), key=f"one_{key_prefix}")
    if one != "Escolha a opcao" and c2.button("Adicionar item", key=f"btn_one_{key_prefix}", use_container_width=True):
        row = work[work["principio_ativo"] == one].head(1)
        _add_to_cart(_build_cart_items(row, cabecalho))
        st.success("Produto adicionado ao carrinho.")


def _merge_contacts(score_df: pd.DataFrame, clientes_df: pd.DataFrame | None) -> pd.DataFrame:
    df = score_df.copy()
    if clientes_df is None or clientes_df.empty or "cnpj" not in clientes_df.columns:
        for column in ["nome_contato", "contato", "telefone_limpo", "razao_social", "endereco", "bairro", "uf"]:
            if column not in df.columns:
                df[column] = ""
        return df
    cols = [col for col in ["cnpj", "nome_contato", "contato", "telefone_limpo", "razao_social", "endereco", "bairro", "uf"] if col in clientes_df.columns]
    ref = clientes_df[cols].drop_duplicates("cnpj").copy()
    ref["cnpj"] = ref["cnpj"].astype(str)
    df["cnpj"] = df["cnpj"].astype(str)
    return df.merge(ref, on="cnpj", how="left", suffixes=("", "_cad"))


def render_clientes(
    score_df: pd.DataFrame,
    oportunidades: pd.DataFrame,
    cancelados: pd.DataFrame,
    base_full: pd.DataFrame,
    produtos: pd.DataFrame,
    inventario: pd.DataFrame,
    foco: pd.DataFrame | None = None,
    clientes_df: pd.DataFrame | None = None,
):
    st.markdown('<h2 class="page-title">Clientes</h2>', unsafe_allow_html=True)
    df = _merge_contacts(score_df.copy(), clientes_df)

    groups = load_sip_groups()
    rede_opts = ["Todos"] + [g["nome"] for g in groups]
    rede_sel = st.selectbox("Rede / SIP", rede_opts)
    if rede_sel != "Todos":
        grp = next((g for g in groups if g["nome"] == rede_sel), None)
        if grp:
            rede_cnpjs = [str(x) for x in grp.get("cnpjs", [])]
            df = df[df["cnpj"].astype(str).isin(rede_cnpjs)]

    filtro1, filtro2, filtro3 = st.columns(3)
    busca_cliente = filtro1.text_input("Buscar cliente ou CNPJ")
    busca_comprador = filtro2.text_input("Buscar comprador")
    busca_contato = filtro3.text_input("Buscar telefone / contato")

    c1, c2, c3, c4 = st.columns([1.1, 1.0, 0.9, 0.9])
    status_mes = c1.selectbox("Compra no mes", ["Todos", "Com compra no mes", "Sem compra no mes"])
    potencial = c2.selectbox("Potencial", ["Todos", "Alto", "Medio", "Baixo"])
    sem_prior = c3.toggle("Sem prioritario", value=False)
    sem_lanc = c4.toggle("Sem lancamentos", value=False)

    if busca_cliente:
        termo = busca_cliente.strip().lower()
        mask = (
            df["nome_fantasia"].astype(str).str.lower().str.contains(termo, na=False)
            | df["cnpj"].astype(str).str.contains(re.sub(r"\D", "", termo), na=False)
        )
        df = df[mask]
    if busca_comprador:
        termo = busca_comprador.strip().lower()
        df = df[df["nome_contato"].astype(str).str.lower().str.contains(termo, na=False)]
    if busca_contato:
        digits = re.sub(r"\D", "", busca_contato)
        mask = df["contato"].astype(str).str.contains(busca_contato.strip(), case=False, na=False)
        if digits:
            mask = mask | df["telefone_limpo"].astype(str).str.contains(digits, na=False)
        df = df[mask]

    if status_mes == "Com compra no mes":
        df = df[df["comprou_mes_atual"]]
    elif status_mes == "Sem compra no mes":
        df = df[~df["comprou_mes_atual"]]
    if potencial != "Todos":
        df = df[df["potencial_categoria"] == potencial]
    if sem_prior:
        df = df[~df["teve_venda_prioritarios"]]
    if sem_lanc:
        df = df[~df["teve_venda_lancamentos"]]

    st.caption(f"Clientes visiveis no painel: {len(df)}")
    export_df = df[
        [
            "cnpj",
            "nome_fantasia",
            "cidade",
            "nome_contato",
            "contato",
            "ol_sem_combate",
            "ol_prioritarios",
            "ol_lancamentos",
            "ol_combate",
            "total_faturado",
        ]
    ].copy()
    export_df.columns = [
        "CNPJ",
        "Cliente",
        "Cidade",
        "Comprador",
        "Telefone",
        "OL sem combate",
        "OL prioritarios",
        "OL lancamentos",
        "Combate",
        "Faturado no periodo",
    ]
    for column in ["OL sem combate", "OL prioritarios", "OL lancamentos", "Combate", "Faturado no periodo"]:
        export_df[column] = export_df[column].map(_money)
    st.download_button(
        "Extrair base de clientes",
        data=_excel_bytes(export_df, "Clientes"),
        file_name="base_clientes_painel.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    tabela = df[
        [
            "nome_fantasia",
            "cnpj",
            "cidade",
            "nome_contato",
            "contato",
            "comprou_mes_atual_label",
            "teve_venda_prioritarios",
            "teve_venda_lancamentos",
            "ol_sem_combate",
            "ol_prioritarios",
            "ol_lancamentos",
            "ol_combate",
        ]
    ].copy()
    tabela.columns = [
        "Cliente",
        "CNPJ",
        "Cidade",
        "Comprador",
        "Telefone",
        "Compra mes",
        "Prioritarios",
        "Lancamentos",
        "OL sem combate",
        "OL prioritarios",
        "OL lancamentos",
        "Combate",
    ]
    tabela["Prioritarios"] = tabela["Prioritarios"].map({True: "Sim", False: "Nao"})
    tabela["Lancamentos"] = tabela["Lancamentos"].map({True: "Sim", False: "Nao"})
    for column in ["OL sem combate", "OL prioritarios", "OL lancamentos", "Combate"]:
        tabela[column] = tabela[column].map(_money)

    evento = st.dataframe(tabela, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row")
    if df.empty:
        return

    cli_ref = df[["nome_fantasia", "cnpj", "nome_contato", "contato"]].drop_duplicates().sort_values("nome_fantasia").copy()
    cli_ref["label"] = cli_ref["nome_fantasia"] + " - " + cli_ref["cnpj"].astype(str) + " - " + cli_ref["nome_contato"].fillna("").astype(str)
    idx_default = 0
    try:
        rows = getattr(evento, "selection", {}).get("rows", [])
        if rows:
            cnpj_sel = tabela.iloc[rows[0]]["CNPJ"]
            idx_default = max(0, cli_ref.index.get_loc(cli_ref[cli_ref["cnpj"].astype(str) == str(cnpj_sel)].index[0]))
    except Exception:
        pass
    cliente_label = st.selectbox("Cliente", cli_ref["label"].tolist(), index=idx_default)
    cnpj = cli_ref.loc[cli_ref["label"] == cliente_label, "cnpj"].iloc[0]
    nome_cli = cli_ref.loc[cli_ref["label"] == cliente_label, "nome_fantasia"].iloc[0]

    base_cli = base_full[base_full["cnpj_pdv"].astype(str) == str(cnpj)].copy() if base_full is not None and not base_full.empty else pd.DataFrame()
    cabecalho = _cliente_header(base_cli, nome_cli, cnpj)
    cli_cad = clientes_df[clientes_df["cnpj"].astype(str) == str(cnpj)].head(1) if clientes_df is not None and not clientes_df.empty and "cnpj" in clientes_df.columns else pd.DataFrame()

    def _cad(col, default=""):
        if not cli_cad.empty and col in cli_cad.columns and not cli_cad[col].dropna().empty:
            return str(cli_cad.iloc[0][col])
        if col in base_cli.columns and not base_cli[col].dropna().empty:
            return str(base_cli[col].dropna().iloc[0])
        return default

    msg = f"Ola, {_cad('nome_contato', '') or nome_cli}"
    wa = _wa_link(_cad("telefone_limpo", _cad("contato", "")), msg)
    st.markdown(
        (
            f"<div class='detail-card'><div class='detail-title'>{_cad('razao_social', nome_cli)}</div>"
            f"<div class='detail-sub'>{_cad('nome_fantasia', nome_cli)} - CNPJ: {cnpj}</div>"
            f"<div class='detail-grid'>"
            f"<div><span>Cidade</span><b>{_cad('cidade', '')} - {_cad('uf', '')}</b></div>"
            f"<div><span>Endereco</span><b>{_cad('endereco', '')}</b></div>"
            f"<div><span>Bairro</span><b>{_cad('bairro', '')}</b></div>"
            f"<div><span>Contato</span><b>{_cad('nome_contato', '')}</b></div>"
            f"<div><span>Telefone</span><b>{_cad('contato', '')}</b></div>"
            f"<div><span>WhatsApp</span><b>{'Disponivel' if wa else 'Sem contato'}</b></div>"
            f"</div></div>"
        ),
        unsafe_allow_html=True,
    )
    if wa:
        st.link_button("Abrir WhatsApp do cliente", wa, use_container_width=True)

    linha_score = df[df["cnpj"].astype(str) == str(cnpj)].head(1)
    if not linha_score.empty:
        row = linha_score.iloc[0]
        cols_m = st.columns(4)
        with cols_m[0]:
            st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>OL sem combate</div><div class='metric-value'>{_money(row.get('ol_sem_combate', 0))}</div></div>", unsafe_allow_html=True)
        with cols_m[1]:
            st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>Prioritarios</div><div class='metric-value'>{_money(row.get('ol_prioritarios', 0))}</div></div>", unsafe_allow_html=True)
        with cols_m[2]:
            st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>Lancamentos</div><div class='metric-value'>{_money(row.get('ol_lancamentos', 0))}</div></div>", unsafe_allow_html=True)
        with cols_m[3]:
            st.markdown(f"<div class='metric-card metric-center'><div class='metric-label'>Combate</div><div class='metric-value'>{_money(row.get('ol_combate', 0))}</div></div>", unsafe_allow_html=True)

    if not base_cli.empty:
        vendas_res = base_cli[base_cli["status_pedido"].isin(["FATURADO", "FATURADO PARCIAL"])].copy() if "status_pedido" in base_cli.columns else base_cli.copy()
        total_fat = float(pd.to_numeric(vendas_res.get("total_faturado", 0), errors="coerce").fillna(0).sum()) if "total_faturado" in vendas_res.columns else 0
        ultima_compra = " - "
        if "data_do_pedido" in base_cli.columns:
            datas = pd.to_datetime(base_cli["data_do_pedido"], errors="coerce").dropna()
            if not datas.empty:
                ultima_compra = datas.max().strftime("%d/%m/%Y")
        st.markdown(f"<div class='reason-box'>Pedidos: {len(base_cli)} - Faturado: {_money(total_fat)} - Ultima compra: {ultima_compra}</div>", unsafe_allow_html=True)

    comprados = base_cli[base_cli["status_pedido"].isin(["FATURADO", "FATURADO PARCIAL"])][["ean", "principio_ativo", "mix_lancamentos"]].drop_duplicates() if not base_cli.empty else pd.DataFrame(columns=["ean", "principio_ativo", "mix_lancamentos"])
    comprados = comprados[comprados["mix_lancamentos"].isin(["PRIORITARIOS", "LANCAMENTOS"])]
    universo = produtos[produtos["mix_lancamentos"].isin(["PRIORITARIOS", "LANCAMENTOS"])][["ean", "principio_ativo", "mix_lancamentos"]].drop_duplicates()
    best = _best_inv_all(inventario)

    nao_comprados = universo.merge(comprados[["ean"]], on="ean", how="left", indicator=True)
    nao_comprados = nao_comprados[nao_comprados["_merge"] == "left_only"].drop(columns="_merge")
    nao_comprados = nao_comprados.merge(best, on="ean", how="left").sort_values(["mix_lancamentos", "principio_ativo"]).drop_duplicates("ean")
    comprados = comprados.sort_values(["mix_lancamentos", "principio_ativo"]).drop_duplicates("ean")

    _choose_df("Prioritarios e lancamentos comprados", comprados[["ean", "principio_ativo", "mix_lancamentos"]], cabecalho, f"comp_{cnpj}")
    _choose_df("Prioritarios e lancamentos nao comprados", nao_comprados[["ean", "principio_ativo", "mix_lancamentos", "distribuidora", "preco_sem_imposto", "estoque"]], cabecalho, f"nc_{cnpj}")

    sug = oportunidades.copy() if oportunidades is not None else pd.DataFrame()
    if not sug.empty:
        comprados_eans = set(comprados["ean"].astype(str).tolist())
        sug = sug[~sug["ean"].astype(str).isin(comprados_eans)].copy()
        sug = sug[sug["mix_lancamentos"].isin(["PRIORITARIOS", "LANCAMENTOS", "LINHA"])].head(25)
        sug = sug.merge(best, on="ean", how="left", suffixes=("", "_best"))
        if "distribuidora_best" in sug.columns:
            sug["distribuidora"] = sug["distribuidora_best"]
            sug["preco_sem_imposto"] = sug["preco_sem_imposto_best"].fillna(sug["preco_sem_imposto"])
            sug["estoque"] = sug["estoque_best"].fillna(sug["estoque"])
        sug = sug[["ean", "principio_ativo", "mix_lancamentos", "distribuidora", "preco_sem_imposto", "estoque"]].drop_duplicates("ean")
    _choose_df(
        "Sugestao de produtos",
        sug if not sug.empty else pd.DataFrame(columns=["ean", "principio_ativo", "mix_lancamentos", "distribuidora", "preco_sem_imposto", "estoque"]),
        cabecalho,
        f"sug_{cnpj}",
    )

    if foco is not None and not foco.empty:
        foco_df = foco[["ean", "principio_ativo"]].drop_duplicates().merge(produtos[["ean", "mix_lancamentos"]].drop_duplicates(), on="ean", how="left").merge(best, on="ean", how="left")
        foco_df["foco"] = True
        _choose_df("Foco em evidencia", foco_df[["ean", "principio_ativo", "mix_lancamentos", "distribuidora", "preco_sem_imposto", "estoque", "foco"]].drop_duplicates("ean"), cabecalho, f"foco_{cnpj}")

    st.markdown('<div class="section-title">Produtos cancelados</div>', unsafe_allow_html=True)
    canc = cancelados[cancelados["cnpj_pdv"].astype(str) == str(cnpj)].copy() if cancelados is not None and not cancelados.empty else pd.DataFrame()
    if canc.empty:
        st.info("Nenhum produto cancelado para este cliente.")
    else:
        canc["data_do_pedido"] = pd.to_datetime(canc["data_do_pedido"], errors="coerce").dt.strftime("%d/%m/%Y")
        canc.columns = ["CNPJ", "Data", "Produto", "Qtde Cancelada"]
        st.dataframe(canc[["CNPJ", "Data", "Produto", "Qtde Cancelada"]], use_container_width=True, hide_index=True)
