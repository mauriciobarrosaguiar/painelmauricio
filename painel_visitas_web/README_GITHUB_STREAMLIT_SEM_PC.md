# Painel EMS - GitHub + Streamlit sem PC

Este pacote foi ajustado para rodar a automação direto pelo GitHub Actions, sem agente local no computador.

## Como ficou
- Streamlit = painel e botões
- GitHub Actions = robô de automação
- Arquivos atualizados = commitados de volta em `painel_visitas_web/data`

## O que já foi criado
- `.github/workflows/automacao_web.yml`
- `painel_visitas_web/github_action_runner.py`
- Tela de Importação ajustada para disparar GitHub Actions
- Carrinho ajustado para enviar pedido ao Mercado Farma via GitHub Actions

## Passo a passo

### 1) Suba tudo no GitHub
Envie todo o conteúdo deste pacote para o seu repositório `painelems`.

### 2) No GitHub, habilite permissões do Actions
No repositório:
`Settings > Actions > General > Workflow permissions`
Marque:
- **Read and write permissions**
- **Allow GitHub Actions to create and approve pull requests** não é obrigatório

### 3) Crie os Secrets do repositório
No GitHub:
`Settings > Secrets and variables > Actions > New repository secret`

Crie estes secrets:
- `BUSSOLA_LOGIN`
- `BUSSOLA_SENHA`
- `MERCADOFARMA_LOGIN`
- `MERCADOFARMA_SENHA`
- `MERCADOFARMA_CNPJ`
- `GH_AUTOMATION_TOKEN`

## Token do GitHub
O `GH_AUTOMATION_TOKEN` deve ser um token seu com acesso ao repositório.
Permissões mínimas recomendadas no token:
- Actions: Read and write
- Contents: Read and write
- Metadata: Read-only

### 4) Configure os Secrets do Streamlit Cloud
No app do Streamlit, abra **Settings > Secrets** e cole:

```toml
GITHUB_REPO_OWNER = "mauriciobarrosaguiar"
GITHUB_REPO_NAME = "painelems"
GITHUB_REPO_BRANCH = "main"
GITHUB_AUTOMATION_WORKFLOW = "automacao_web.yml"
GITHUB_TOKEN = "SEU_TOKEN_GITHUB"
```

### 5) Faça redeploy do Streamlit
Após salvar os secrets, faça reboot/redeploy do app.

### 6) Como usar
No painel:
- Importação > Atualizar Bússola agora
- Importação > Atualizar Mercado Farma agora
- Carrinho > Enviar pedido para Mercado Farma

Cada clique dispara o workflow do GitHub Actions.

### 7) Onde ver se rodou
No GitHub:
`Actions > Automação Web Painel EMS`

Ali você verá:
- em execução
- concluído
- erro

### 8) Arquivos atualizados
Quando a automação terminar, o GitHub faz commit nos arquivos dentro de:
- `painel_visitas_web/data/`

## Observações importantes
- As credenciais usadas pelo robô são as dos **GitHub Secrets**.
- Os campos de login/senha dentro do painel ficaram como referência e backup local.
- Se o workflow falhar, veja o log dentro do GitHub Actions.
- Se o Streamlit não disparar o workflow, quase sempre o problema é o token sem permissão suficiente.
