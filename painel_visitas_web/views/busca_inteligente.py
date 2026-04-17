from __future__ import annotations

import re
import unicodedata

import pandas as pd
import streamlit as st


ABREVIACOES_BUSCA = {
    "hct": "hidroclorotiazida",
    "hctz": "hidroclorotiazida",
    "hidro": "hidroclorotiazida",
    "olme": "olmesartana",
    "olmes": "olmesartana",
    "anlo": "anlodipino",
    "amlod": "anlodipino",
    "los": "losartana",
    "losar": "losartana",
    "vals": "valsartana",
    "rosu": "rosuvastatina",
    "ator": "atorvastatina",
    "aas": "acido acetilsalicilico",
    "vit": "colecalciferol",
    "dnova": "colecalciferol",
    "carboiste": "carbocisteina",
    "bromozepam": "bromazepam",
    "loratadino": "loratadina",
    "desloratadino": "desloratadina",
    "finasterido": "finasterida",
}

FRASES_BUSCA = {
    "ac mefenamico": "mefenamico",
    "ac tranexamico": "tranexamico",
    "acido tranexamico": "tranexamico",
    "neo dia": "neodia",
    "neo folico": "neo folico",
    "vit d": "colecalciferol",
    "vitamina d": "colecalciferol",
    "50 mil": "50000",
    "10 mil": "10000",
    "5 mil": "5000",
}

TERMOS_FRACOS = {"mg", "g", "ml", "cp", "cpr", "caps", "caixa", "frasco", "adulto", "pediatrico"}


def _strip_accents(value: object) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(ch))


def normalizar_busca(valor: object) -> str:
    texto = _strip_accents(valor).lower().replace("µ", "mc")
    texto = re.sub(r"(\d)([a-z])", r"\1 \2", texto)
    texto = re.sub(r"([a-z])(\d)", r"\1 \2", texto)
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def substituir_frases_busca(texto: str) -> str:
    resultado = f" {normalizar_busca(texto)} "
    for origem, destino in sorted(FRASES_BUSCA.items(), key=lambda item: len(item[0]), reverse=True):
        resultado = re.sub(rf"(?<![a-z0-9]){re.escape(origem)}(?![a-z0-9])", f" {normalizar_busca(destino)} ", resultado)
    return re.sub(r"\s+", " ", resultado).strip()


def expandir_consulta_produto(consulta: str) -> tuple[list[str], str]:
    texto = substituir_frases_busca(consulta)
    termos: list[str] = []
    for token in texto.split():
        if token in TERMOS_FRACOS:
            continue
        expansao = ABREVIACOES_BUSCA.get(token, token)
        for termo in normalizar_busca(expansao).split():
            if termo and termo not in TERMOS_FRACOS and termo not in termos:
                termos.append(termo)
    return termos, " ".join(termos)


def _prepare_catalog(inventario: pd.DataFrame) -> pd.DataFrame:
    if inventario is None or inventario.empty:
        return pd.DataFrame(columns=["ean", "principio_ativo", "distribuidora", "mix_lancamentos", "preco_sem_imposto", "estoque", "texto_busca"])
    df = inventario.copy()
    df["ean"] = df["ean"].astype(str)
    df["principio_ativo"] = df["principio_ativo"].astype(str)
    df["distribuidora"] = df["distribuidora"].astype(str)
    df["mix_lancamentos"] = df.get("mix_lancamentos", "").astype(str)
    df["preco_sem_imposto"] = pd.to_numeric(df.get("preco_sem_imposto", 0), errors="coerce").fillna(0)
    df["estoque"] = pd.to_numeric(df.get("estoque", 0), errors="coerce").fillna(0)
    df["desconto"] = pd.to_numeric(df.get("desconto", 0), errors="coerce").fillna(0)
    df["texto_busca"] = (
        df["principio_ativo"].map(normalizar_busca)
        + " "
        + df["distribuidora"].map(normalizar_busca)
        + " "
        + df["mix_lancamentos"].map(normalizar_busca)
        + " "
        + df["ean"].astype(str)
    )
    return df


def buscar_produtos_inteligente(
    consulta: str,
    inventario: pd.DataFrame,
    limite: int = 20,
    distribuidora_filtro: str = "",
    mix_filtro: str = "Todos",
) -> tuple[pd.DataFrame, str]:
    catalogo = _prepare_catalog(inventario)
    termos, consulta_expandida = expandir_consulta_produto(consulta)
    if catalogo.empty or not termos:
        return pd.DataFrame(), consulta_expandida

    if distribuidora_filtro:
        catalogo = catalogo[catalogo["distribuidora"].astype(str) == distribuidora_filtro].copy()
    if mix_filtro != "Todos":
        catalogo = catalogo[catalogo["mix_lancamentos"].astype(str) == mix_filtro].copy()
    if catalogo.empty:
        return pd.DataFrame(), consulta_expandida

    digits_query = "".join(ch for ch in consulta if ch.isdigit())
    catalogo["hits"] = 0
    for termo in termos:
        catalogo["hits"] = catalogo["hits"] + catalogo["texto_busca"].str.contains(re.escape(termo), na=False).astype(int)

    minimo_hits = 1 if len(termos) == 1 else min(2, len(termos))
    candidatos = catalogo[catalogo["hits"] >= minimo_hits].copy()
    if candidatos.empty and digits_query:
        candidatos = catalogo[catalogo["ean"].astype(str).str.contains(digits_query, na=False)].copy()
    if candidatos.empty:
        candidatos = catalogo[catalogo["hits"] > 0].copy()
    if candidatos.empty:
        return pd.DataFrame(), consulta_expandida

    candidatos["score"] = candidatos["hits"] * 15
    candidatos["score"] += candidatos["texto_busca"].str.startswith(consulta_expandida).astype(int) * 10
    if digits_query:
        candidatos["score"] += candidatos["ean"].astype(str).eq(digits_query).astype(int) * 50
        candidatos["score"] += candidatos["ean"].astype(str).str.startswith(digits_query).astype(int) * 15
    candidatos["score"] += (candidatos["estoque"] > 0).astype(int) * 4

    candidatos = candidatos.sort_values(["score", "estoque", "preco_sem_imposto", "principio_ativo"], ascending=[False, False, True, True])
    return candidatos.head(limite).reset_index(drop=True), consulta_expandida


def render_busca_inteligente(score_df: pd.DataFrame, inventario: pd.DataFrame, clientes_df: pd.DataFrame | None = None) -> None:
    st.markdown(
        """
        <div class="hero-search">
            <h1>Busca Inteligente</h1>
            <p>Encontre apresentacoes equivalentes por nome, abreviacao, EAN, substancia e dose usando a planilha extraida do Mercado Farma.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if inventario is None or inventario.empty:
        st.info("A extracao do Mercado Farma ainda nao esta disponivel para pesquisa.")
        return

    col1, col2 = st.columns([1.45, 0.45])
    consulta = col1.text_area(
        "Cole um ou varios produtos, um por linha",
        placeholder="Ex.:\nHCT\nOlmesartana 40/25\nRosuvastatina 20mg",
        height=180,
    )
    distribs = ["Todas"] + sorted(inventario["distribuidora"].dropna().astype(str).unique().tolist())
    dist_label = col2.selectbox("Distribuidora", distribs)
    mix_label = col2.selectbox("Tipo de produto", ["Todos", "PRIORITARIOS", "LANCAMENTOS", "LINHA", "COMBATE"])
    limite = int(col2.number_input("Resultados por item", min_value=1, max_value=50, value=12, step=1))

    st.caption("Atalhos reconhecidos: HCT = hidroclorotiazida; Olmesartana 40/25 busca olmesartana + hidroclorotiazida 40 mg + 25 mg.")
    if not consulta.strip():
        st.info("Cole a lista ou digite um produto para pesquisar.")
        return

    linhas = [linha.strip() for linha in consulta.splitlines() if linha.strip()]
    if not linhas:
        st.info("Cole a lista ou digite um produto para pesquisar.")
        return

    selected_eans = set(st.session_state.get("preselected_products", {}).keys() if isinstance(st.session_state.get("preselected_products", {}), dict) else [])
    resumo = []
    novos_selecionados: dict[str, bool] = {}

    for idx, linha in enumerate(linhas):
        resultado, interpretado = buscar_produtos_inteligente(
            linha,
            inventario,
            limite=limite,
            distribuidora_filtro="" if dist_label == "Todas" else dist_label,
            mix_filtro=mix_label,
        )
        resumo.append({"Item pesquisado": linha, "Busca interpretada": interpretado or "-", "Encontrados": len(resultado)})
        with st.expander(f"{linha} - {len(resultado)} resultado(s)", expanded=len(linhas) == 1):
            st.caption(f"Busca interpretada: {interpretado or linha}")
            if resultado.empty:
                st.warning("Nenhum produto encontrado para essa linha.")
                continue

            cols = st.columns(3)
            for pos, (_, row) in enumerate(resultado.iterrows()):
                with cols[pos % 3]:
                    with st.container(border=True):
                        st.markdown(f"**{row.get('principio_ativo', '')}**")
                        st.caption(f"EAN: {row.get('ean', '')}")
                        st.markdown(
                            f"<span class='inventory-pill'>{row.get('distribuidora', '')}</span>"
                            f"<span class='inventory-pill'>{row.get('mix_lancamentos', '') or 'LINHA'}</span>",
                            unsafe_allow_html=True,
                        )
                        m1, m2 = st.columns(2)
                        m1.metric("Sem imposto", f"R$ {float(pd.to_numeric(row.get('preco_sem_imposto', 0), errors='coerce') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                        m2.metric("Estoque", str(int(pd.to_numeric(row.get("estoque", 0), errors="coerce") or 0)))
                        checked = st.checkbox(
                            "Levar para Montar pedido",
                            value=str(row.get("ean", "")) in selected_eans,
                            key=f"smart_pick_{idx}_{row.get('ean', '')}_{row.get('distribuidora', '')}",
                        )
                        if checked:
                            novos_selecionados[str(row.get("ean", ""))] = True

    st.dataframe(pd.DataFrame(resumo), use_container_width=True, hide_index=True)
    if st.button("Levar selecionados para Montar pedido", use_container_width=True):
        if not novos_selecionados:
            st.warning("Selecione pelo menos um produto para continuar.")
        else:
            st.session_state.preselected_products = novos_selecionados
            st.session_state.page = "Montar pedido"
            st.rerun()
