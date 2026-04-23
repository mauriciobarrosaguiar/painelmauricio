from __future__ import annotations

import re
import unicodedata

import pandas as pd
import streamlit as st

from services.discount_actions import TYPE_COMBO, TYPE_MELHOR_PRECO, TYPE_PROGRESSIVO, action_price_from_choice, actions_to_dataframe, find_action_for_item


def _money(value) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
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


def _plain_digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _safe_int(value, default=0) -> int:
    number = pd.to_numeric(value, errors="coerce")
    return int(default if pd.isna(number) else number)


def _safe_float(value, default=0.0) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(number) else number)


def _strip_accents(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(ch))


def _norm(value: str) -> str:
    text = _strip_accents(value).lower()
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _merge_clientes(score_df: pd.DataFrame | None, clientes_df: pd.DataFrame | None) -> pd.DataFrame:
    clientes = score_df.copy() if isinstance(score_df, pd.DataFrame) else pd.DataFrame()
    if clientes.empty:
        return clientes
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


def _build_client_header(score_df: pd.DataFrame | None, clientes_df: pd.DataFrame | None, base_full: pd.DataFrame | None) -> dict | None:
    clientes = _merge_clientes(score_df, clientes_df)
    if clientes.empty:
        return None
    cli_ref = clientes[["nome_fantasia", "cnpj", "cidade", "nome_contato", "contato"]].drop_duplicates().sort_values("nome_fantasia").copy()
    cli_ref["nome_contato"] = cli_ref["nome_contato"].fillna("")
    cli_ref["label"] = cli_ref["nome_fantasia"] + " - " + cli_ref["cnpj"].astype(str) + " - " + cli_ref["nome_contato"].astype(str)
    labels = cli_ref["label"].tolist()
    if not labels:
        return None
    default_idx = 0
    current_cnpj = str(st.session_state.get("pedido_cliente_cnpj") or "")
    if current_cnpj:
        match = cli_ref.index[cli_ref["cnpj"].astype(str) == current_cnpj]
        if len(match):
            default_idx = cli_ref.index.get_loc(match[0])
    selected = st.selectbox("Cliente para levar produtos ao carrinho", labels, index=default_idx, key="acoes_cliente_carrinho")
    cliente_row = cli_ref[cli_ref["label"] == selected].iloc[0]
    st.session_state.pedido_cliente_cnpj = str(cliente_row["cnpj"])

    base_cli = base_full.copy() if isinstance(base_full, pd.DataFrame) else pd.DataFrame()
    if not base_cli.empty and "cnpj_pdv" in base_cli.columns:
        base_cli = base_cli[base_cli["cnpj_pdv"].astype(str) == str(cliente_row["cnpj"])]
    else:
        base_cli = pd.DataFrame()

    def _get_first(col: str, default=""):
        if col in base_cli.columns and not base_cli[col].dropna().empty:
            return str(base_cli[col].dropna().iloc[0])
        if col in clientes.columns:
            candidate = clientes[clientes["cnpj"].astype(str) == str(cliente_row["cnpj"])][col].dropna()
            if not candidate.empty:
                return str(candidate.iloc[0])
        return default

    header = {
        "Cliente": str(cliente_row["nome_fantasia"]),
        "CNPJ": str(cliente_row["cnpj"]),
        "Empresa": str(cliente_row["nome_fantasia"]),
        "Razao social": _get_first("razao_social", str(cliente_row["nome_fantasia"])),
        "Nome do comprador": _get_first("nome_contato", str(cliente_row.get("nome_contato", "") or "")),
        "Tel do comprador": _plain_digits(_get_first("contato", str(cliente_row.get("contato", "") or ""))),
    }
    st.caption(f"Carrinho sera montado para {header['Cliente']} | CNPJ {header['CNPJ']}")
    return header


def _remember_coupon(cupom: str):
    cupom = str(cupom or "").strip()
    if not cupom:
        return
    atuais = [item.strip() for item in str(st.session_state.get("mf_cupom", "") or "").split(";") if item.strip()]
    if cupom not in atuais:
        atuais.append(cupom)
    st.session_state["mf_cupom"] = "; ".join(atuais)


def _add_to_cart(itens: list[dict]) -> int:
    carrinho = st.session_state.setdefault("cart_items", [])
    existentes = {(str(i.get("CNPJ", "")), str(i.get("EAN", "")), str(i.get("Distribuidora", ""))): idx for idx, i in enumerate(carrinho)}
    adicionados = 0
    for item in itens:
        key = (str(item.get("CNPJ", "")), str(item.get("EAN", "")), str(item.get("Distribuidora", "")))
        if key in existentes:
            carrinho[existentes[key]].update(item)
        else:
            carrinho.append(item)
            adicionados += 1
    st.session_state.cart_items = carrinho
    return adicionados


def _find_inventory_choice(inv: pd.DataFrame, action_row: dict) -> dict | None:
    if inv is None or inv.empty:
        return None
    distribuidora = str(action_row.get("distribuidora", "") or "").strip()
    ean = str(action_row.get("ean", "") or "").strip()
    produto = str(action_row.get("produto", "") or "").strip()
    base = inv[inv["distribuidora"].astype(str).str.strip() == distribuidora].copy()
    if ean:
        match = base[base["ean"].astype(str) == ean].copy()
        if not match.empty:
            return match.sort_values(["preco_sem_imposto", "estoque"], ascending=[True, False]).iloc[0].to_dict()
    if produto:
        alvo = _norm(produto)
        match = base[base["principio_ativo"].astype(str).map(lambda value: _norm(value) == alvo)].copy()
        if not match.empty:
            return match.sort_values(["preco_sem_imposto", "estoque"], ascending=[True, False]).iloc[0].to_dict()
    return None


def _build_action_item(header: dict, action_row: dict, choice: dict, quantidade: int, foco_eans: set[str]) -> dict:
    return {
        **header,
        "EAN": str(action_row.get("ean", "") or choice.get("ean", "")),
        "Produto": str(choice.get("principio_ativo", "") or action_row.get("produto", "")),
        "Distribuidora": str(choice.get("distribuidora", "") or action_row.get("distribuidora", "") or "Sem distribuidora"),
        "Preco": action_price_from_choice(choice, _safe_float(action_row.get("desconto", 0), 0.0)),
        "Estoque": _safe_int(choice.get("estoque", 0), 0),
        "Mix": str(choice.get("mix_lancamentos", "LINHA") or "LINHA"),
        "Qtde": max(1, int(quantidade or 1)),
        "Foco": str(action_row.get("ean", "") or choice.get("ean", "")) in foco_eans,
        "Cupom": str(action_row.get("cupom", "") or ""),
        "Acao": str(action_row.get("nome_acao", "") or ""),
        "Tipo acao": str(action_row.get("tipo_acao", "") or ""),
    }


def _campaign_label(row: dict, default_prefix: str) -> str:
    return str(row.get("nome_acao", "") or row.get("cupom", "") or row.get("produto", "") or default_prefix).strip()


def _filter_actions(df: pd.DataFrame, distribuidora: str, busca: str) -> pd.DataFrame:
    out = df.copy()
    if distribuidora != "Todas":
        out = out[out["distribuidora"].astype(str) == distribuidora]
    if busca:
        termo = _norm(busca)
        digits = _plain_digits(busca)
        mask = (
            out["produto"].astype(str).map(lambda value: termo in _norm(value))
            | out["nome_acao"].astype(str).map(lambda value: termo in _norm(value))
            | out["cupom"].astype(str).str.lower().str.contains(str(busca).strip().lower(), na=False)
        )
        if digits:
            mask = mask | out["ean"].astype(str).str.contains(digits, na=False)
        out = out[mask]
    return out


def _prepare_inventory(inventario: pd.DataFrame) -> pd.DataFrame:
    inv = inventario.copy() if isinstance(inventario, pd.DataFrame) else pd.DataFrame()
    if inv.empty:
        return inv
    inv["ean"] = inv["ean"].astype(str)
    inv["estoque"] = pd.to_numeric(inv.get("estoque", 0), errors="coerce").fillna(0).astype(int)
    inv["preco_sem_imposto"] = pd.to_numeric(inv.get("preco_sem_imposto", 0), errors="coerce").fillna(0.0)
    inv["pf_dist"] = pd.to_numeric(inv.get("pf_dist", 0), errors="coerce").fillna(0.0)
    inv["pf_fabrica"] = pd.to_numeric(inv.get("pf_fabrica", 0), errors="coerce").fillna(0.0)
    inv["desconto"] = pd.to_numeric(inv.get("desconto", 0), errors="coerce").fillna(0.0)
    return inv


def _render_combo_section(df: pd.DataFrame, inv: pd.DataFrame, header: dict | None, foco_eans: set[str]):
    st.markdown('<div class="section-title">Combos</div>', unsafe_allow_html=True)
    if df.empty:
        st.caption("Nenhum combo vigente para os filtros atuais.")
        return
    group_cols = ["distribuidora", "nome_acao", "cupom", "validade"]
    for idx, (_, grupo) in enumerate(df.sort_values(["distribuidora", "nome_acao", "produto"]).groupby(group_cols, dropna=False)):
        meta = grupo.iloc[0].to_dict()
        with st.container(border=True):
            st.markdown(f"**{_campaign_label(meta, 'Combo')}**")
            st.caption(f"{meta.get('distribuidora', '-')} | Cupom: {meta.get('cupom', '-') or '-'} | Validade: {_format_date(meta.get('validade', ''))}")
            linhas = []
            for _, row in grupo.iterrows():
                choice = _find_inventory_choice(inv, row.to_dict())
                linhas.append(
                    {
                        "Produto": str(row.get("produto", "") or ""),
                        "EAN": str(row.get("ean", "") or ""),
                        "Qtd minima": int(row.get("qtd_minima", 1) or 1),
                        "Desconto": _pct(row.get("desconto", 0)),
                        "Estoque": _safe_int((choice or {}).get("estoque", 0), 0),
                        "Preco acao": _money(action_price_from_choice(choice or {}, row.get("desconto", 0))) if choice else "-",
                    }
                )
            st.dataframe(pd.DataFrame(linhas), use_container_width=True, hide_index=True)
            disabled = header is None
            if st.button("Levar combo ao carrinho", key=f"combo_add_{idx}", use_container_width=True, disabled=disabled):
                itens = []
                faltantes = []
                for _, row in grupo.iterrows():
                    choice = _find_inventory_choice(inv, row.to_dict())
                    if not choice:
                        faltantes.append(str(row.get("produto", "") or row.get("ean", "")))
                        continue
                    itens.append(_build_action_item(header, row.to_dict(), choice, int(row.get("qtd_minima", 1) or 1), foco_eans))
                if itens:
                    adicionados = _add_to_cart(itens)
                    _remember_coupon(meta.get("cupom", ""))
                    st.success(f"{adicionados if adicionados else len(itens)} item(ns) do combo enviados ao carrinho.")
                if faltantes:
                    st.warning(f"Sem estoque/localizacao para: {', '.join(faltantes[:4])}")


def _render_progressive_section(df: pd.DataFrame, inv: pd.DataFrame, header: dict | None, foco_eans: set[str], action_key=()):
    st.markdown('<div class="section-title">Escalonadas</div>', unsafe_allow_html=True)
    if df.empty:
        st.caption("Nenhuma acao escalonada vigente para os filtros atuais.")
        return
    group_cols = ["distribuidora", "nome_acao", "ean", "produto", "validade"]
    for idx, (_, grupo) in enumerate(df.sort_values(["distribuidora", "nome_acao", "produto", "qtd_de"]).groupby(group_cols, dropna=False)):
        meta = grupo.iloc[0].to_dict()
        choice = _find_inventory_choice(inv, meta)
        with st.container(border=True):
            st.markdown(f"**{_campaign_label(meta, 'Escalonada')}**")
            st.caption(f"{meta.get('produto', '')} | {meta.get('distribuidora', '-')} | Validade: {_format_date(meta.get('validade', ''))}")
            tiers = []
            for _, row in grupo.iterrows():
                qtd_de = int(row.get("qtd_de", 1) or 1)
                qtd_ate = int(row.get("qtd_ate", 0) or 0)
                faixa = f"{qtd_de}+" if qtd_ate <= 0 else f"{qtd_de} a {qtd_ate}"
                tiers.append(
                    {
                        "Faixa": faixa,
                        "Desconto": _pct(row.get("desconto", 0)),
                        "Cupom": str(row.get("cupom", "") or "-"),
                        "Preco acao": _money(action_price_from_choice(choice or {}, row.get("desconto", 0))) if choice else "-",
                    }
                )
            st.dataframe(pd.DataFrame(tiers), use_container_width=True, hide_index=True)
            qty = st.number_input("Quantidade", min_value=1, step=1, value=max(1, int(meta.get("qtd_de", 1) or 1)), key=f"prog_qty_{idx}")
            action = find_action_for_item(
                action_key,
                ean=str(meta.get("ean", "") or ""),
                distribuidora=str(meta.get("distribuidora", "") or ""),
                quantidade=int(qty),
                produto=str(meta.get("produto", "") or ""),
                tipo_preferido=TYPE_PROGRESSIVO,
            )
            if action:
                st.caption(f"Faixa aplicada: {int(action.get('qtd_de', 1) or 1)}{'+' if int(action.get('qtd_ate', 0) or 0) <= 0 else f' a {int(action.get('qtd_ate', 0) or 0)}'} | Cupom: {action.get('cupom', '-') or '-'}")
            else:
                st.caption("A quantidade informada ainda nao atinge nenhuma faixa promocional.")
            disabled = header is None or action is None or choice is None
            if st.button("Levar produto ao carrinho", key=f"prog_add_{idx}", use_container_width=True, disabled=disabled):
                item = _build_action_item(header, action or meta, choice, int(qty), foco_eans)
                adicionados = _add_to_cart([item])
                _remember_coupon((action or meta).get("cupom", ""))
                st.success(f"{adicionados if adicionados else 1} item enviado ao carrinho.")


def _render_open_section(df: pd.DataFrame, inv: pd.DataFrame, header: dict | None, foco_eans: set[str]):
    st.markdown('<div class="section-title">Abertas</div>', unsafe_allow_html=True)
    if df.empty:
        st.caption("Nenhuma acao aberta vigente para os filtros atuais.")
        return
    group_cols = ["distribuidora", "nome_acao", "cupom", "validade"]
    for idx, (_, grupo) in enumerate(df.sort_values(["distribuidora", "nome_acao", "produto"]).groupby(group_cols, dropna=False)):
        meta = grupo.iloc[0].to_dict()
        with st.container(border=True):
            st.markdown(f"**{_campaign_label(meta, 'Campanha aberta')}**")
            st.caption(f"{meta.get('distribuidora', '-')} | Cupom: {meta.get('cupom', '-') or '-'} | Validade: {_format_date(meta.get('validade', ''))}")
            linhas = []
            choices_by_ean: dict[str, dict] = {}
            for _, row in grupo.iterrows():
                action_row = row.to_dict()
                choice = _find_inventory_choice(inv, action_row)
                ean = str(row.get("ean", "") or "")
                choices_by_ean[ean] = choice or {}
                linhas.append(
                    {
                        "Selecionar": False,
                        "EAN": ean,
                        "Produto": str(row.get("produto", "") or ""),
                        "Estoque": _safe_int((choice or {}).get("estoque", 0), 0),
                        "Preco acao": _money(action_price_from_choice(choice or {}, row.get("desconto", 0))) if choice else "-",
                        "Qtde": 1,
                    }
                )
            editor = st.data_editor(
                pd.DataFrame(linhas),
                use_container_width=True,
                hide_index=True,
                key=f"open_editor_{idx}",
                num_rows="fixed",
                column_config={
                    "Selecionar": st.column_config.CheckboxColumn("Sel.", width="small"),
                    "EAN": st.column_config.TextColumn("EAN", width="medium"),
                    "Produto": st.column_config.TextColumn("Produto", width="large"),
                    "Estoque": st.column_config.NumberColumn("Estoque", width="small"),
                    "Preco acao": st.column_config.TextColumn("Preco acao", width="small"),
                    "Qtde": st.column_config.NumberColumn("Qtde", min_value=1, step=1, width="small"),
                },
            )
            disabled = header is None
            if st.button("Adicionar selecionados ao carrinho", key=f"open_add_{idx}", use_container_width=True, disabled=disabled):
                selecionados = editor[editor["Selecionar"]].copy()
                if selecionados.empty:
                    st.warning("Selecione ao menos um produto.")
                else:
                    itens = []
                    faltantes = []
                    for _, row_sel in selecionados.iterrows():
                        ean = str(row_sel.get("EAN", "") or "")
                        original = grupo[grupo["ean"].astype(str) == ean].head(1)
                        if original.empty:
                            continue
                        action_row = original.iloc[0].to_dict()
                        choice = choices_by_ean.get(ean) or _find_inventory_choice(inv, action_row)
                        if not choice:
                            faltantes.append(str(action_row.get("produto", "") or ean))
                            continue
                        itens.append(_build_action_item(header, action_row, choice, int(row_sel.get("Qtde", 1) or 1), foco_eans))
                    if itens:
                        adicionados = _add_to_cart(itens)
                        _remember_coupon(meta.get("cupom", ""))
                        st.success(f"{adicionados if adicionados else len(itens)} produto(s) enviados ao carrinho.")
                    if faltantes:
                        st.warning(f"Sem estoque/localizacao para: {', '.join(faltantes[:4])}")


def render_acoes_desconto(
    inventario: pd.DataFrame,
    action_records: list[dict] | None = None,
    score_df: pd.DataFrame | None = None,
    clientes_df: pd.DataFrame | None = None,
    base_full: pd.DataFrame | None = None,
    foco: pd.DataFrame | None = None,
    action_key=(),
):
    st.markdown('<h2 class="page-title">Acoes de desconto</h2>', unsafe_allow_html=True)
    st.caption("Campanhas agrupadas por tipo para facilitar visualizacao e envio ao carrinho.")

    inv = _prepare_inventory(inventario)
    actions_df = actions_to_dataframe(action_records or [])
    active = actions_df[actions_df["status"].eq("Vigente")].copy() if not actions_df.empty else pd.DataFrame()

    dist_options = ["Todas"]
    if not active.empty and "distribuidora" in active.columns:
        dist_options += sorted([d for d in active["distribuidora"].dropna().astype(str).unique().tolist() if d])
    f1, f2 = st.columns([1.1, 1.4])
    distribuidora = f1.selectbox("Distribuidora", dist_options, key="acoes_filtro_distribuidora")
    busca = f2.text_input("Buscar produto, campanha, cupom ou EAN", key="acoes_busca_produto")

    header = _build_client_header(score_df, clientes_df, base_full)
    foco_eans = set()
    if isinstance(foco, pd.DataFrame) and not foco.empty and "ean" in foco.columns:
        foco_eans = set(foco["ean"].dropna().astype(str).tolist())

    if inv.empty:
        st.info("Atualize o Mercado Farma para visualizar os produtos com estoque e preco.")
        return
    if active.empty:
        st.info("Nenhuma acao vigente cadastrada no momento.")
        return

    filtered = _filter_actions(active, distribuidora, busca)
    combos = filtered[filtered["tipo_acao"].eq(TYPE_COMBO)].copy()
    progressivos = filtered[filtered["tipo_acao"].eq(TYPE_PROGRESSIVO)].copy()
    abertas = filtered[filtered["tipo_acao"].eq(TYPE_MELHOR_PRECO)].copy()

    if filtered.empty:
        st.info("Nenhuma campanha encontrada com os filtros atuais.")
    else:
        _render_combo_section(combos, inv, header, foco_eans)
        _render_progressive_section(progressivos, inv, header, foco_eans, action_key=action_key)
        _render_open_section(abertas, inv, header, foco_eans)

    with st.expander("Todas as acoes cadastradas", expanded=False):
        if actions_df.empty:
            st.caption("Nenhuma acao cadastrada.")
        else:
            all_show = actions_df.copy()
            all_show.rename(
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
            all_show["Desconto"] = all_show["Desconto"].map(_pct)
            all_show["Validade"] = all_show["Validade"].map(_format_date)
            st.dataframe(all_show, use_container_width=True, hide_index=True)
