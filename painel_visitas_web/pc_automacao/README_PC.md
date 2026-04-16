Automação local do Painel de Visitas

1. Copie estes arquivos para a pasta do projeto local `painel_visitas_web`.
2. Ajuste `config_local.bat` se precisar.
3. Rode `rodar_agente_local.bat` para processar comandos do site.
4. No Agendador de Tarefas, agende `rodar_agente_local_oculto.vbs` para rodar a cada 5 minutos.
5. Agende também:
   - `atualizar_bussola_oculto.vbs` a cada 1 hora
   - `atualizar_mercadofarma_oculto.vbs` 2x ao dia

O agente local vai:
- puxar o GitHub
- ler `data/comandos_remotos.json`
- executar comandos pendentes
- atualizar `data/status_atualizacao.json`
- fazer push para o GitHub
