from __future__ import annotations

import time

import pandas as pd
import streamlit as st

ACTIVE_STATUSES = {"solicitado", "pendente", "executando"}


def is_active(bloco: dict | None) -> bool:
    status = str((bloco or {}).get("status", "")).strip().lower()
    return status in ACTIVE_STATUSES


def _progress(bloco: dict | None) -> dict:
    progress = dict((bloco or {}).get("progresso") or {})
    atual = int(progress.get("atual") or 0)
    total = int(progress.get("total") or 0)
    percentual = int(progress.get("percentual") or 0)
    if total > 0 and percentual <= 0:
        percentual = int((atual / total) * 100)
    return {"atual": atual, "total": total, "percentual": max(0, min(percentual, 100))}


def render_monitor(title: str, bloco: dict | None, *, key_prefix: str, empty_message: str) -> None:
    data = dict(bloco or {})
    status = str(data.get("status", "nunca") or "nunca")
    mensagem = str(data.get("mensagem", "") or empty_message)
    etapa = str(data.get("etapa_atual", "") or "-")
    atualizado = str(data.get("atualizado_em", "") or data.get("ultimo_sucesso", "") or "-")
    comando = str(data.get("ultimo_comando_id", "") or "-")
    erro = str(data.get("erro", "") or "")
    resumo = dict(data.get("resumo") or {})
    progress = _progress(data)

    st.markdown(f"### {title}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", status)
    c2.metric("Etapa", etapa)
    c3.metric("Atualizado", atualizado)
    c4.metric("Comando", comando[-8:] if comando != "-" else "-")
    st.caption(mensagem)

    if progress["total"] > 0:
        st.progress(progress["percentual"] / 100, text=f"{progress['atual']} / {progress['total']} etapas")

    if erro:
        st.error(erro)

    if resumo:
        cols = st.columns(min(4, len(resumo)))
        for idx, (label, value) in enumerate(resumo.items()):
            cols[idx % len(cols)].metric(str(label).replace("_", " ").title(), str(value))

    eventos = list(data.get("eventos") or [])
    if eventos:
        eventos_df = pd.DataFrame(
            [
                {
                    "Hora": evento.get("quando", "-"),
                    "Nivel": evento.get("nivel", "info"),
                    "Detalhe": evento.get("texto", ""),
                }
                for evento in reversed(eventos[-12:])
            ]
        )
        st.dataframe(eventos_df, use_container_width=True, hide_index=True)
    else:
        st.info(empty_message)

    live = st.toggle("Acompanhar ao vivo", value=is_active(data), key=f"{key_prefix}_live")
    if live and is_active(data):
        st.caption("Atualizacao automatica a cada 5 segundos enquanto houver execucao.")
        time.sleep(5)
        st.rerun()
