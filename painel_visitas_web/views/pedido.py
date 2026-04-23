from __future__ import annotations

from io import BytesIO
import math
import re
import unicodedata

import pandas as pd
import streamlit as st

from services.discount_actions import apply_action_to_choice, combo_groups, find_action_for_item


def _money(value):
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _strip_accents(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(ch))


def _norm(value: str) -> str:
    text = _strip_accents(value).lower().replace("µ", "mc")
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _search_match(name: str, ean: str, query: str) -> bool:
    q = _norm(query)
    if not q:
        return True
    n = _norm(name)
    e = str(ean)
    tokens = [token for token in q.split() if token]
    return all(token in n or token in e for token in tokens)


def _calc_preco_sem(escolha):
    return float(pd.to_numeric(escolha.get("preco_sem_imposto", 0), errors="coerce") or 0)


def _excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
        worksheet = writer.sheets[sheet_name[:31]]
        for idx, column in enumerate(df.columns):
            largura = max(len(str(column)), min(42, int(df[column].astype(str).str.len().fillna(0).max()) + 2 if not df.empty else 14))
            worksheet.set_column(idx, idx, largura)
    output.seek(0)
    return output.getvalue()


def _inventory_export(inventario: pd.DataFrame) -> pd.DataFrame:
    if inventario is None or inventario.empty:
        return pd.DataFrame(columns=["EAN", "NOME DO PRODUTO", "DISTRIBUIDORA", "ESTOQUE", "DESCONTO (%)", "PF DIST. (R$)", "PRECO FINAL (R$)", "SEM IMPOSTO (R$)", "DATA"])
    export_df = inventario.copy()
    export_df["data_fmt"] = pd.to_datetime(export_df.get("data"), errors="coerce").dt.strftime("%d/%m/%Y")
    result = pd.DataFrame(
        {
            "EAN": export_df.get("ean", "").astype(str),
            "NOME DO PRODUTO": export_df.get("principio_ativo", "").astype(str),
            "DISTRIBUIDORA": export_df.get("distribuidora", "").astype(str),
            "ESTOQUE": pd.to_numeric(export_df.get("estoque", 0), errors="coerce").fillna(0).astype(int),
            "DESCONTO (%)": pd.to_numeric(export_df.get("desconto", 0), errors="coerce").fillna(0).round(2),
            "PF DIST. (R$)": pd.to_numeric(export_df.get("pf_dist", 0), errors="coerce").fillna(0).round(2),
            "PRECO FINAL (R$)": pd.to_numeric(export_df.get("preco_com_imposto", 0), errors="coerce").fillna(0).round(2),
            "SEM IMPOSTO (R$)": pd.to_numeric(export_df.get("preco_sem_imposto", 0), errors="coerce").fillna(0).round(2),
            "DATA": export_df.get("data_fmt", "").fillna(""),
        }
    )
    return result.sort_values(["NOME DO PRODUTO", "DISTRIBUIDORA", "EAN"]).reset_index(drop=True)


def _catalogo_cliente(cnpj: str, base_full: pd.DataFrame, produtos: pd.DataFrame, inventario: pd.DataFrame, oportunidades: pd.DataFrame) -> pd.DataFrame:
    if base_full is None or not isinstance(base_full, pd.DataFrame):
        base_full = pd.DataFrame()
    if produtos is None or not isinstance(produtos, pd.DataFrame):
        produtos = pd.DataFrame()
    if oportunidades is None or not isinstance(oportunidades, pd.DataFrame):
        oportunidades = pd.DataFrame()

    if not base_full.empty and {"cnpj_pdv", "status_pedido", "ean"}.issubset(base_full.columns):
        comprados = base_full[
            (base_full["cnpj_pdv"].astype(str) == str(cnpj))
            & (base_full["status_pedido"].isin(["FATURADO", "FATURADO PARCIAL"]))
        ][["ean"]].drop_duplicates()
    else:
        comprados = pd.DataFrame(columns=["ean"])

    catalogo = oportunidades.copy() if not oportunidades.empty else produtos.copy()
    if catalogo is None or not isinstance(catalogo, pd.DataFrame):
        catalogo = pd.DataFrame()
    if catalogo.empty and not produtos.empty:
        catalogo = produtos.copy()

    if "principio_ativo" not in catalogo.columns and not produtos.empty:
        catalogo = produtos.copy()

    if "score_sugestao" not in catalogo.columns:
        catalogo["score_sugestao"] = 0

    cols_merge = [c for c in ["ean", "principio_ativo", "mix_lancamentos"] if c in produtos.columns]
    if cols_merge:
        base_prod = produtos[cols_merge].drop_duplicates().copy()
        for col in cols_merge:
            if col not in catalogo.columns:
                catalogo[col] = ""
        catalogo = catalogo.merge(base_prod, on=cols_merge, how="outer")

    for col, default in {"ean": "", "principio_ativo": "", "mix_lancamentos": "LINHA"}.items():
        if col not in catalogo.columns:
            catalogo[col] = default
    catalogo = catalogo.merge(comprados.assign(comprado=1), on="ean", how="left")
    catalogo["comprado"] = catalogo["comprado"].fillna(0)
    catalogo["preco_sem_imposto"] = pd.to_numeric(catalogo.get("preco_sem_imposto", 0), errors="coerce").fillna(0)
    catalogo["estoque"] = pd.to_numeric(catalogo.get("estoque", 0), errors="coerce").fillna(0)

    return catalogo.sort_values(["comprado", "score_sugestao", "preco_sem_imposto", "principio_ativo"], ascending=[True, False, True, True]).drop_duplicates("ean")


def _add_to_cart(itens):
    carrinho = st.session_state.setdefault("cart_items", [])
    existentes = {(str(i["CNPJ"]), str(i["EAN"]), str(i["Distribuidora"])): idx for idx, i in enumerate(carrinho)}
    adicionados = 0
    for item in itens:
        key = (str(item["CNPJ"]), str(item["EAN"]), str(item["Distribuidora"]))
        if key in existentes:
            carrinho[existentes[key]].update(item)
        else:
            carrinho.append(item)
            adicionados += 1
    st.session_state.cart_items = carrinho
    return adicionados


def _wa_link(phone: str, msg: str = ""):
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return ""
    return f"https://wa.me/55{digits}?text={msg}" if len(digits) <= 11 else f"https://wa.me/{digits}?text={msg}"


def _merge_clientes(score_df: pd.DataFrame, clientes_df: pd.DataFrame | None) -> pd.DataFrame:
    clientes = score_df.copy()
    if clientes_df is None or clientes_df.empty or "cnpj" not in clientes_df.columns:
        for column in ["nome_contato", "contato", "telefone_limpo", "razao_social", "endereco", "bairro", "uf"]:
            if column not in clientes.columns:
                clientes[column] = ""
        return clientes
    cols = [col for col in ["cnpj", "nome_contato", "contato", "telefone_limpo", "razao_social", "endereco", "bairro", "uf"] if col in clientes_df.columns]
    ref = clientes_df[cols].drop_duplicates("cnpj").copy()
    ref["cnpj"] = ref["cnpj"].astype(str)
    clientes["cnpj"] = clientes["cnpj"].astype(str)
    return clientes.merge(ref, on="cnpj", how="left", suffixes=("", "_cad"))


def render_pedido(
    score_df: pd.DataFrame,
    oportunidades: pd.DataFrame,
    inventario: pd.DataFrame,
    cidade: str = "Todas",
    base_full: pd.DataFrame | None = None,
    produtos: pd.DataFrame | None = None,
    foco: pd.DataFrame | None = None,
    clientes_df: pd.DataFrame | None = None,
    action_records: list[dict] | None = None,
    action_key=(),
):
    st.markdown('<h2 class="page-title">Montar pedido</h2>', unsafe_allow_html=True)
    clientes = _merge_clientes(score_df.copy(), clientes_df)
    if cidade != "Todas":
        clientes = clientes[clientes["cidade"] == cidade]
    if clientes.empty:
        st.info("Nenhum cliente disponivel para montar pedido.")
        return

    filtro_cliente = st.text_input("Filtrar cliente por nome, CNPJ, comprador, telefone ou cidade")
    if filtro_cliente:
        termo = filtro_cliente.strip().lower()
        digits = re.sub(r"\D", "", filtro_cliente)
        mask = (
            clientes["nome_fantasia"].astype(str).str.lower().str.contains(termo, na=False)
            | clientes["cidade"].astype(str).str.lower().str.contains(termo, na=False)
            | clientes["nome_contato"].astype(str).str.lower().str.contains(termo, na=False)
            | clientes["contato"].astype(str).str.lower().str.contains(termo, na=False)
        )
        if digits:
            mask = mask | clientes["cnpj"].astype(str).str.contains(digits, na=False) | clientes["telefone_limpo"].astype(str).str.contains(digits, na=False)
        clientes = clientes[mask]
    if clientes.empty:
        st.warning("Nenhum cliente encontrado com os filtros informados.")
        return

    cli_ref = clientes[["nome_fantasia", "cnpj", "cidade", "uf", "nome_contato", "contato"]].drop_duplicates().sort_values("nome_fantasia").copy()
    cli_ref["label"] = cli_ref["nome_fantasia"] + " - " + cli_ref["cnpj"].astype(str) + " - " + cli_ref["nome_contato"].fillna("").astype(str)
    labels = cli_ref["label"].tolist()
    default_idx = 0
    if st.session_state.get("pedido_cliente_cnpj") is not None:
        match = cli_ref.index[cli_ref["cnpj"] == st.session_state.get("pedido_cliente_cnpj")]
        if len(match):
            default_idx = cli_ref.index.get_loc(match[0])
    cliente_label = st.selectbox("Cliente (nome + CNPJ + comprador)", labels, index=default_idx)
    cliente_row = cli_ref[cli_ref["label"] == cliente_label].iloc[0]
    cnpj = cliente_row["cnpj"]
    st.session_state.pedido_cliente_cnpj = cnpj

    base_cli = (base_full if base_full is not None else pd.DataFrame()).copy()
    base_cli = base_cli[base_cli["cnpj_pdv"].astype(str) == str(cnpj)] if not base_cli.empty else pd.DataFrame()
    cli_cad = clientes_df[clientes_df["cnpj"].astype(str) == str(cnpj)].head(1) if clientes_df is not None and not clientes_df.empty and "cnpj" in clientes_df.columns else pd.DataFrame()

    def _cad(col, default=""):
        if not cli_cad.empty and col in cli_cad.columns and not cli_cad[col].dropna().empty:
            return str(cli_cad.iloc[0][col])
        if col in base_cli.columns and not base_cli[col].dropna().empty:
            return str(base_cli[col].dropna().iloc[0])
        return default

    cadastro = {
        "empresa": cliente_row["nome_fantasia"],
        "razao_social": _cad("razao_social", cliente_row["nome_fantasia"]),
        "nome_fantasia": _cad("nome_fantasia", cliente_row["nome_fantasia"]),
        "nome_comprador": _cad("nome_contato", cliente_row.get("nome_contato", "")),
        "tel_comprador": _cad("contato", cliente_row.get("contato", "")),
        "endereco": _cad("endereco", ""),
        "bairro": _cad("bairro", ""),
        "cidade": _cad("cidade", cliente_row.get("cidade", "")),
        "uf": _cad("uf", cliente_row.get("uf", "")),
    }
    wa = _wa_link(cadastro["tel_comprador"])

    st.markdown(
        (
            f"<div class='detail-card'><div class='detail-title'>{cadastro['razao_social']}</div>"
            f"<div class='detail-sub'>{cadastro['nome_fantasia']} - CNPJ: {cliente_row['cnpj']}</div>"
            f"<div class='detail-grid'>"
            f"<div><span>Cidade</span><b>{cadastro['cidade']} - {cadastro['uf']}</b></div>"
            f"<div><span>Endereco</span><b>{cadastro['endereco']}</b></div>"
            f"<div><span>Bairro</span><b>{cadastro['bairro']}</b></div>"
            f"<div><span>Responsavel</span><b>{cadastro['nome_comprador']}</b></div>"
            f"<div><span>Contato</span><b>{cadastro['tel_comprador']}</b></div>"
            f"<div><span>WhatsApp</span><b>{'Disponivel' if wa else 'Sem contato'}</b></div>"
            f"</div></div>"
        ),
        unsafe_allow_html=True,
    )

    linha_score = score_df[score_df["cnpj"].astype(str) == str(cnpj)].head(1) if score_df is not None and not score_df.empty else pd.DataFrame()
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

    top_actions = st.columns(4)
    if wa:
        top_actions[0].link_button("Abrir WhatsApp", wa, use_container_width=True)
    else:
        top_actions[0].button("Abrir WhatsApp", disabled=True, use_container_width=True)
    export_inventory = _inventory_export(inventario)
    top_actions[1].download_button(
        "Baixar planilha MF",
        data=_excel_bytes(export_inventory, "MercadoFarma"),
        file_name="mercado_farma_extraido.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    if top_actions[2].button("Ir para Pedido Inteligente", use_container_width=True):
        st.session_state.page = "Pedido Inteligente"
        st.rerun()
    if top_actions[3].button("Limpar selecao inteligente", use_container_width=True):
        st.session_state.preselected_products = {}
        st.rerun()

    catalogo = _catalogo_cliente(cnpj, base_full if base_full is not None else pd.DataFrame(), produtos if produtos is not None else pd.DataFrame(), inventario, oportunidades)
    if catalogo.empty:
        st.info("Sem catalogo disponivel para este cliente.")
        return

    pre_map = st.session_state.get("preselected_products", {})
    preselected = set(pre_map.keys()) if isinstance(pre_map, dict) else set()
    catalogo["preselect"] = catalogo["ean"].astype(str).isin(preselected)
    catalogo = catalogo.sort_values(["preselect", "comprado", "score_sugestao", "preco_sem_imposto"], ascending=[False, True, False, True])

    inv = inventario.copy() if inventario is not None else pd.DataFrame()
    if not inv.empty:
        inv["ean"] = inv["ean"].astype(str)
        inv["preco_sem_imposto"] = pd.to_numeric(inv.get("preco_sem_imposto", 0), errors="coerce").fillna(0)
        inv["estoque"] = pd.to_numeric(inv.get("estoque", 0), errors="coerce").fillna(0)
        inv["desconto"] = pd.to_numeric(inv.get("desconto", 0), errors="coerce").fillna(0)

    def _remember_coupon(cupom: str):
        cupom = str(cupom or "").strip()
        if not cupom:
            return
        atual = [item.strip() for item in str(st.session_state.get("mf_cupom", "") or "").split(";") if item.strip()]
        if cupom not in atual:
            atual.append(cupom)
        st.session_state["mf_cupom"] = "; ".join(atual)

    def _find_catalog_row(ean: str, produto_nome: str):
        ean = str(ean or "")
        if ean and not catalogo.empty:
            match = catalogo[catalogo["ean"].astype(str) == ean]
            if not match.empty:
                return match.iloc[0]
        if produto_nome and not catalogo.empty:
            alvo = _norm(produto_nome)
            match = catalogo[catalogo["principio_ativo"].astype(str).map(lambda value: _norm(value) == alvo)]
            if not match.empty:
                return match.iloc[0]
        return pd.Series({"ean": ean, "principio_ativo": produto_nome, "mix_lancamentos": "LINHA"})

    def _find_inventory_choice(ean: str, produto_nome: str, distribuidora: str):
        if inv.empty:
            return None
        base = inv[inv["distribuidora"].astype(str) == str(distribuidora or "")]
        ean = str(ean or "")
        if ean:
            match = base[base["ean"].astype(str) == ean]
            if not match.empty:
                return match.sort_values(["preco_sem_imposto", "estoque"], ascending=[True, False]).iloc[0]
        if produto_nome:
            alvo = _norm(produto_nome)
            match = base[base["principio_ativo"].astype(str).map(lambda value: _norm(value) == alvo)]
            if not match.empty:
                return match.sort_values(["preco_sem_imposto", "estoque"], ascending=[True, False]).iloc[0]
        return None

    filtros = st.columns([1.7, 1.0, 1.0, 0.7])
    busca = filtros[0].text_input("Buscar produto por nome ou EAN")
    mix_filtro = filtros[1].selectbox("Mix", ["Todos", "LANCAMENTOS", "PRIORITARIOS", "LINHA", "COMBATE"])
    dist_options = ["Todas"] + sorted(inv["distribuidora"].dropna().astype(str).unique().tolist()) if not inv.empty else ["Todas"]
    dist_filtro = filtros[2].selectbox("Distribuidora", dist_options)
    per_page = filtros[3].selectbox("Produtos por pagina", [9, 12, 18], index=1)

    if busca:
        catalogo = catalogo[catalogo.apply(lambda item: _search_match(item.get("principio_ativo", ""), item.get("ean", ""), busca), axis=1)]
    if mix_filtro != "Todos":
        catalogo = catalogo[catalogo["mix_lancamentos"] == mix_filtro]
    if dist_filtro != "Todas" and not inv.empty:
        eans_dist = inv[inv["distribuidora"].astype(str) == dist_filtro]["ean"].astype(str).unique().tolist()
        catalogo = catalogo[catalogo["ean"].astype(str).isin(eans_dist)]

    total_pages = max(1, math.ceil(len(catalogo) / per_page))
    page_key = f"pedido_page_{cnpj}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    st.session_state[page_key] = max(1, min(int(st.session_state[page_key]), total_pages))

    nav1, nav2, nav3 = st.columns([0.9, 1.1, 0.9])
    with nav1:
        if st.button("Anterior", use_container_width=True, disabled=st.session_state[page_key] <= 1, key=f"prev_{cnpj}"):
            st.session_state[page_key] = max(1, st.session_state[page_key] - 1)
            st.rerun()
    with nav2:
        st.number_input("Pagina", min_value=1, max_value=total_pages, key=page_key, step=1)
    with nav3:
        if st.button("Proxima", use_container_width=True, disabled=st.session_state[page_key] >= total_pages, key=f"next_{cnpj}"):
            st.session_state[page_key] = min(total_pages, st.session_state[page_key] + 1)
            st.rerun()

    page = int(st.session_state[page_key])
    catalogo_pagina = catalogo.iloc[(page - 1) * per_page : page * per_page].copy()
    st.markdown('<div class="section-title">Produtos sugeridos e catalogo completo</div>', unsafe_allow_html=True)

    def _item_from_row(row: pd.Series, escolha: pd.Series, quantidade: int, action: dict | None = None) -> dict:
        foco_flag = False if foco is None or foco.empty else str(row.get("ean", "")) in foco["ean"].astype(str).tolist()
        escolha_calc = apply_action_to_choice(escolha, action) if action else escolha
        return {
            "Cliente": cliente_row["nome_fantasia"],
            "CNPJ": str(cnpj),
            "Empresa": cadastro["empresa"],
            "Razao social": cadastro["razao_social"],
            "Nome do comprador": cadastro["nome_comprador"],
            "Tel do comprador": cadastro["tel_comprador"],
            "EAN": str(row.get("ean", "")),
            "Produto": row.get("principio_ativo", ""),
            "Distribuidora": str(escolha.get("distribuidora", "Sem distribuidora") or "Sem distribuidora"),
            "Preco": _calc_preco_sem(escolha_calc),
            "Estoque": int(pd.to_numeric(escolha_calc.get("estoque", 0), errors="coerce") or 0),
            "Mix": row.get("mix_lancamentos", "LINHA"),
            "Qtde": int(quantidade),
            "Foco": foco_flag,
            "Cupom": str((action or {}).get("cupom", "") or ""),
            "Acao": str((action or {}).get("nome_acao", "") or ""),
            "Tipo acao": str((action or {}).get("tipo_acao", "") or ""),
        }

    combos = combo_groups(action_records or [])
    if combos:
        st.markdown('<div class="section-title">Combos e campanhas cadastradas</div>', unsafe_allow_html=True)
        combo_cols = st.columns(2)
        for idx_combo, combo in enumerate(combos):
            with combo_cols[idx_combo % 2]:
                with st.container(border=True):
                    st.markdown(f"**{combo.get('nome_acao', 'Combo')}**")
                    st.caption(
                        f"Distribuidora: {combo.get('distribuidora', '-')} | Cupom: {combo.get('cupom', '-') or '-'} | Validade: {pd.to_datetime(combo.get('validade'), errors='coerce').strftime('%d/%m/%Y') if pd.notna(pd.to_datetime(combo.get('validade'), errors='coerce')) else '-'}"
                    )
                    combo_df = pd.DataFrame(
                        [
                            {
                                "Produto": item.get("produto", ""),
                                "EAN": item.get("ean", ""),
                                "Qtd minima": int(item.get("qtd_minima", 1) or 1),
                                "Desconto": f"{float(pd.to_numeric(item.get('desconto', 0), errors='coerce') or 0):.2f}%".replace(".", ","),
                            }
                            for item in combo.get("itens", [])
                        ]
                    )
                    st.dataframe(combo_df, use_container_width=True, hide_index=True)
                    if st.button(f"Adicionar combo {idx_combo + 1}", key=f"add_combo_{idx_combo}", use_container_width=True):
                        itens_combo = []
                        faltantes = []
                        for action in combo.get("itens", []):
                            escolha = _find_inventory_choice(action.get("ean", ""), action.get("produto", ""), action.get("distribuidora", ""))
                            if escolha is None:
                                faltantes.append(action.get("produto", action.get("ean", "")))
                                continue
                            row_ref = _find_catalog_row(action.get("ean", ""), action.get("produto", ""))
                            quantidade = int(action.get("qtd_minima", 1) or 1)
                            itens_combo.append(_item_from_row(row_ref, escolha, quantidade, action=action))
                        if itens_combo:
                            adicionados = _add_to_cart(itens_combo)
                            _remember_coupon(combo.get("cupom", ""))
                            st.success(f"{adicionados if adicionados else len(itens_combo)} item(ns) do combo adicionados ao carrinho.")
                        if faltantes:
                            st.warning(f"Itens sem estoque/localizacao no Mercado Farma: {', '.join(faltantes[:4])}")

    def _collect_visible_items() -> list[dict]:
        itens = []
        for _, row in catalogo_pagina.iterrows():
            ean = str(row.get("ean", ""))
            quantidade = int(st.session_state.get(f"qty_{cnpj}_{ean}", 0) or 0)
            if quantidade <= 0:
                continue
            variantes = inv[inv["ean"] == ean].copy().sort_values(["preco_sem_imposto", "estoque"], ascending=[True, False]) if not inv.empty else pd.DataFrame()
            if variantes.empty:
                variantes = pd.DataFrame(
                    [
                        {
                            "distribuidora": row.get("distribuidora", "Sem distribuidora") or "Sem distribuidora",
                            "preco_sem_imposto": row.get("preco_sem_imposto", 0),
                            "estoque": row.get("estoque", 0),
                            "desconto": row.get("desconto", 0),
                            "pf_dist": row.get("pf_dist", 0),
                        }
                    ]
                )
            variantes["distribuidora"] = variantes["distribuidora"].fillna("Sem distribuidora").astype(str)
            opcoes = [dist for dist in variantes["distribuidora"].tolist() if str(dist).strip()] or ["Sem distribuidora"]
            dist = st.session_state.get(f"dist_{cnpj}_{ean}", opcoes[0])
            escolha_df = variantes[variantes["distribuidora"] == dist]
            escolha = escolha_df.iloc[0] if not escolha_df.empty else variantes.iloc[0]
            action = find_action_for_item(
                action_key,
                ean=ean,
                distribuidora=dist,
                quantidade=quantidade,
                produto=row.get("principio_ativo", ""),
            )
            itens.append(_item_from_row(row, escolha, quantidade, action=action))
        return itens

    action_cols = st.columns(2)
    if action_cols[0].button("Adicionar produtos com quantidade ao carrinho", use_container_width=True, key="pedido_add_top"):
        itens = _collect_visible_items()
        if itens:
            adicionados = _add_to_cart(itens)
            st.success(f"{adicionados if adicionados else len(itens)} produto(s) adicionado(s) ao carrinho.")
        else:
            st.warning("Informe a quantidade de ao menos um produto.")
    action_cols[1].caption("Use a Busca Inteligente para levar itens ao topo desta tela.")

    cols = st.columns(3)
    for idx, (_, row) in enumerate(catalogo_pagina.iterrows()):
        ean = str(row.get("ean", ""))
        variantes = inv[inv["ean"] == ean].copy().sort_values(["preco_sem_imposto", "estoque"], ascending=[True, False]) if not inv.empty else pd.DataFrame()
        if variantes.empty:
            variantes = pd.DataFrame(
                [
                    {
                        "distribuidora": row.get("distribuidora", "Sem distribuidora") or "Sem distribuidora",
                        "preco_sem_imposto": row.get("preco_sem_imposto", 0),
                        "estoque": row.get("estoque", 0),
                        "desconto": row.get("desconto", 0),
                        "pf_dist": row.get("pf_dist", 0),
                    }
                ]
            )
        variantes["distribuidora"] = variantes["distribuidora"].fillna("Sem distribuidora").astype(str)
        opcoes = [dist for dist in variantes["distribuidora"].tolist() if str(dist).strip()] or ["Sem distribuidora"]
        default_dist = opcoes[0]
        if dist_filtro != "Todas" and dist_filtro in opcoes:
            default_dist = dist_filtro
        with cols[idx % 3]:
            with st.container(border=True):
                badge_class = "info" if row.get("preselect", False) else ("neutro" if str(row.get("mix_lancamentos", "LINHA")) == "LINHA" else "")
                badge_text = "Selecionado na busca" if row.get("preselect", False) else str(row.get("mix_lancamentos", "LINHA") or "LINHA")
                st.markdown(f"<span class='product-badge {badge_class}'>{badge_text}</span>", unsafe_allow_html=True)
                st.markdown(f"<div class='product-name'>{row.get('principio_ativo', '')}</div>", unsafe_allow_html=True)
                st.caption(f"EAN: {ean}")
                escolha_dist = st.selectbox("Distribuidora", opcoes, index=opcoes.index(default_dist), key=f"dist_{cnpj}_{ean}")
                escolha_df = variantes[variantes["distribuidora"] == escolha_dist]
                escolha = escolha_df.iloc[0] if not escolha_df.empty else variantes.iloc[0]
                quantidade = st.number_input("Quantidade", min_value=0, step=1, key=f"qty_{cnpj}_{ean}")
                quantidade_preview = max(1, int(quantidade or 0))
                action = find_action_for_item(
                    action_key,
                    ean=ean,
                    distribuidora=escolha_dist,
                    quantidade=quantidade_preview,
                    produto=row.get("principio_ativo", ""),
                )
                escolha_calc = apply_action_to_choice(escolha, action) if action else escolha
                st.markdown(
                    (
                        f"<span class='inventory-pill'>{escolha_dist}</span>"
                        f"<span class='inventory-pill'>Estoque: {int(pd.to_numeric(escolha_calc.get('estoque', 0), errors='coerce') or 0)}</span>"
                        f"<span class='inventory-pill'>Desc.: {float(pd.to_numeric(escolha_calc.get('desconto', 0), errors='coerce') or 0):.1f}%</span>"
                    ),
                    unsafe_allow_html=True,
                )
                if action:
                    st.markdown(
                        (
                            f"<span class='inventory-pill'>Acao: {action.get('tipo_acao', '')}</span>"
                            f"<span class='inventory-pill'>Cupom: {action.get('cupom', '-') or '-'}</span>"
                        ),
                        unsafe_allow_html=True,
                    )
                m1, m2 = st.columns(2)
                m1.metric("Sem imposto", _money(_calc_preco_sem(escolha_calc)))
                m2.metric("PF Dist", _money(pd.to_numeric(escolha_calc.get("pf_dist", 0), errors="coerce") or 0))
                if st.button("Adicionar ao carrinho", key=f"add_{cnpj}_{ean}", use_container_width=True):
                    if int(quantidade or 0) <= 0:
                        st.warning("Informe uma quantidade maior que zero.")
                    else:
                        _add_to_cart([_item_from_row(row, escolha, int(quantidade), action=action)])
                        _remember_coupon((action or {}).get("cupom", ""))
                        st.success("Produto adicionado ao carrinho.")

    if st.button("Adicionar produtos com quantidade ao carrinho", use_container_width=True, key="pedido_add_bottom"):
        itens = _collect_visible_items()
        if itens:
            adicionados = _add_to_cart(itens)
            st.success(f"{adicionados if adicionados else len(itens)} produto(s) adicionado(s) ao carrinho.")
        else:
            st.warning("Informe a quantidade de ao menos um produto.")
