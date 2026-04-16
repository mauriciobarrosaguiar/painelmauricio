# Atualização automática das planilhas no GitHub pelo computador

## O que este pacote faz
- Atualiza a base do **Bússola**
- Atualiza a base do **Mercado Farma**
- Faz **commit + push** no GitHub
- Gera logs
- Permite rodar manualmente ou agendar no Windows

## Estrutura sugerida
Coloque estes arquivos dentro da pasta do seu projeto, por exemplo:

```text
C:\Users\Mauricio\Documents\painelems
```

## 1) Ajuste o arquivo `config_local.bat`
Abra o arquivo e ajuste:
- `PROJECT_DIR`
- `PYTHON_EXE`
- `GIT_REMOTE`
- `GITHUB_BRANCH`

## 2) Coloque os extratores na pasta do projeto
Esses scripts precisam existir na pasta do projeto:
- `bussola_extrator_v4.py`
- `mercadofarma_extrator.py`

## 3) Teste manualmente
Dê dois cliques em:
- `atualizar_bussola.bat`
- `atualizar_mercadofarma.bat`
- `rotina_atualizacao_completa.bat`

## 4) Configure o Git local na primeira vez
No CMD, dentro da pasta do projeto:

```bash
git init
git remote add origin https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git
git branch -M main
git pull origin main --allow-unrelated-histories
git config --global user.name "Seu Nome"
git config --global user.email "seu-email@exemplo.com"
```

## 5) Token do GitHub
Quando o Git pedir senha no push:
- usuário = seu usuário do GitHub
- senha = seu **token** do GitHub

## 6) O que cada arquivo faz
- `atualizar_bussola.bat` → roda o extrator do Bússola e sobe a base nova
- `atualizar_mercadofarma.bat` → roda o extrator do Mercado Farma e sobe a base nova
- `rotina_atualizacao_completa.bat` → roda os dois, um após o outro
- `git_push_update.bat` → faz add / commit / push só se houver mudança
- `*.vbs` → rodam ocultos

## 7) Agendador de Tarefas do Windows
### Bússola
- repetir a cada 2 horas

### Mercado Farma
- 2 vezes ao dia, por exemplo:
  - 08:30
  - 14:30

### Programa a executar
Use os arquivos:
- `atualizar_bussola_oculto.vbs`
- `atualizar_mercadofarma_oculto.vbs`

## 8) Onde os arquivos são atualizados
O sistema espera as planilhas na pasta `data` do projeto:
- `data\Pedidos.xlsx`
- `data\Pedidos_bussola.csv`
- `data\Estoque_preco_distribuidora.xlsx`

## 9) Logs
Os logs ficam em:
- `logs\bussola_YYYY-MM-DD.log`
- `logs\mercadofarma_YYYY-MM-DD.log`
- `logs\git_YYYY-MM-DD.log`

## 10) Observação importante
Estes arquivos cuidam só da **atualização da base para o GitHub**.
Eles não limpam pedido nem enviam pedido no Mercado Farma.
