# SegundoCerebro 🧠

Motor inteligente para organização de conhecimento pessoal (PKM) voltado para o Obsidian. 
O projeto utiliza a API do **Google Gemini** para processar anotações brutas (textos, áudios/voz, fotos/OCR e links) e estruturá-las automaticamente em formato Markdown com **YAML frontmatter** (título, data, tags padronizadas), **wikilinks** (`[[conceitos]]`), extração de tarefas acionáveis e sincronização com o Google Drive.

O sistema também inclui um **Índice Mestre (MOC - Map of Content)** com taxonomia de tags e um motor de **busca e consulta inteligente (RAG)** para conversar com seu cofre pelo Telegram.

---

## 🌟 Principais Funcionalidades

### 📥 1. Captura Multimodal Total (Telegram)
* **Texto Livre:** Estruturação em Markdown com cabeçalhos, resumo executivo e wikilinks contextuais.
* **🎙️ Áudios e Mensagens de Voz:** Transcreve e organiza reflexões faladas sem você precisar digitar nada.
* **📸 Fotos e Documentos (OCR):** Lê páginas de livros, quadros brancos, slides e notas manuscritas via visão computacional do Gemini.
* **🔗 Links da Web (URLs):** Extrai o conteúdo do artigo/página e sintetiza em uma Nota de Literatura estruturada.
* **✅ Extração de Tarefas:** Identifica pendências e insere automaticamente uma seção com checkboxes `- [ ]`.

### 🗂️ 2. Índice Mestre de Tags (MOC) & Padronização
* **`00_Indice_MOC.md`:** Nota-mãe gerada e mantida automaticamente na raiz do seu cofre e no Google Drive.
* **Taxonomia Inteligente:** Antes de gerar uma nova nota, o Gemini consulta as tags já existentes para evitar duplicações (ex: unifica `ia` e `inteligencia-artificial`).
* **Seção de Próximos Passos:** Consolida todas as tarefas pendentes espalhadas pelas notas.
* **Histórico Cronológico & Dataview:** Tabela completa com links e bloco nativo pronto para o plugin Dataview do Obsidian.

### 🔍 3. Consulta Inteligente ao Cofre (RAG)
* **Converse com seu Segundo Cérebro:** Faça perguntas pelo Telegram (`? O que anotei sobre X?`) e receba respostas sintetizadas com referências às notas de origem (`[[Nota]]`).

---

## 🤖 Comandos do Bot no Telegram

| Comando | Descrição |
| :--- | :--- |
| `/start` ou `/help` | Exibe o menu completo de instruções e atalhos |
| `? <sua pergunta>` ou `/perguntar <pergunta>` | **Consulta RAG:** Pergunta ao seu cofre e responde com base nas suas notas |
| `/indice` ou `/moc` | Exibe o status do Mapa de Conteúdo e sincroniza o `00_Indice_MOC.md` no Drive |
| `/tags` | Lista todas as `#tags` do cofre e o total de notas por tema |
| `/buscar <termo>` | Pesquisa notas por tag, título, resumo ou palavra-chave |
| `/tarefas` | Lista consolidada de todas as tarefas pendentes (`- [ ]`) encontradas |
| `/resumo` ou `/resumo_semana` | Gera uma nota de **Revisão Semanal** conectando aprendizados dos últimos 7 dias |

---

## 🚀 Instalação e Configuração

### 1. Pré-requisitos
* Python 3.10 ou superior.
* Chave de API do Google Gemini ([Google AI Studio](https://aistudio.google.com/app/apikey)).
* Token de Bot do Telegram (criado via `@BotFather`).
* *(Opcional)* URL do Webhook do Google Apps Script para sincronização com Google Drive.

### 2. Instalação das Dependências
```bash
# Clone ou acesse a pasta do projeto
cd SegundoCerebro

# Crie e ative o ambiente virtual
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # Linux / macOS

# Instale os pacotes
pip install -r requirements.txt
```

### 3. Configuração do `.env`
Crie seu arquivo `.env` com base no `.env.example`:
```ini
# Chave da API do Google Gemini
GEMINI_API_KEY=sua_chave_aqui
GEMINI_MODEL=gemini-3.6-flash

# Token do Bot do Telegram
TELEGRAM_TOKEN=seu_token_do_botfather

# Webhook do Google Apps Script (Drive)
APPS_SCRIPT_URL=https://script.google.com/macros/s/SEU_SCRIPT_ID/exec
```

---

## 💻 Modos de Execução

### ▶️ 1. Execução Local Rápida (Recomendado)
No Windows, basta dar um **duplo clique** no arquivo executável:
```text
iniciar_segundocerebro.bat
```
Ele inicializa a API FastAPI em segundo plano na porta 8000 e conecta o Bot do Telegram com suporte completo a texto, áudio, foto, links e RAG. Ao fechar a janela, todos os processos são finalizados.

---

### 🛠️ 2. Módulos Individuais (CLI & Daemons)

* **Processamento de Arquivo Único (Fase 1):**
  ```bash
  python main.py --input minhas_notas.txt --output resultado.md
  ```
* **Processamento em Lote (Fase 2):**
  ```bash
  python cli_batch_processor.py --inbox inbox_raw/ --vault obsidian_vault/
  ```
* **Daemon de Monitoramento Contínuo (Fase 3):**
  ```bash
  python watcher_daemon.py
  ```
* **Servidor FastAPI (Fase 4):**
  ```bash
  python -m uvicorn api_webhook:app --host 0.0.0.0 --port 8000
  ```
  * Swagger Docs interativo: `http://localhost:8000/docs`
  * Endpoints principais:
    * `POST /process_note`: Texto bruto -> Markdown
    * `POST /process_media`: Áudio (base64) ou Imagem (base64) -> Markdown
    * `POST /process_url`: Leitura de artigos e geração de Nota de Literatura
    * `POST /ask`: Consulta contextual (RAG)
    * `GET /index`: Tags, tarefas pendentes e Markdown do MOC
    * `POST /weekly_digest`: Geração de resumo semanal


---

## ☁️ Deploy Gratuito 24/7 na Vercel (Serverless Webhook)

O projeto está preparado para rodar **24h por dia com custo zero** na Vercel usando Webhooks do Telegram (sem manter servidores ligados ociosos):

1. Suba o código para o seu GitHub (`git push origin main`).
2. Acesse [vercel.com](https://vercel.com), clique em **Add New... > Project** e importe o repositório `secondbrain`.
3. Em **Environment Variables** na Vercel, adicione:
   * `GEMINI_API_KEY`
   * `GEMINI_MODEL` (`gemini-3.6-flash`)
   * `TELEGRAM_TOKEN`
   * `APPS_SCRIPT_URL`
4. Clique em **Deploy**.
5. Assim que a Vercel gerar o seu link (ex: `https://secondbrain-xyz.vercel.app`), abra no navegador:
   ```text
   https://SEU-DOMINIO.vercel.app/telegram/setup_webhook
   ```
   Pronto! Essa rota avisa o Telegram para enviar todas as suas mensagens, áudios, fotos e comandos diretamente para a Vercel.


---

## 👥 Configuração Multi-Usuário (Múltiplos Cofres no Mesmo Bot)

O sistema permite que várias pessoas utilizem o **mesmo bot do Telegram** (`@Obsidian-bot`), mantendo cada cofre do Google Drive, tags e notas **100% isolados e privados** ([`user_manager.py`](user_manager.py)):

1. **Descobrir o ID do Telegram:**
   * A pessoa envia o comando `/meuid` para o bot no Telegram.
   * O bot responde na hora com o número de identificação dela (ex: `987654321`).
2. **Criar o Google Apps Script do novo usuário:**
   * Na conta Google da outra pessoa, ela cria uma pasta no Google Drive para o Obsidian dela e publica o código do [`google_apps_script.js`](google_apps_script.js) em [script.google.com](https://script.google.com) (colocando o `folderId` da pasta dela), gerando a URL do App da Web dela.
3. **Cadastrar nas Variáveis de Ambiente da Vercel (ou `.env`):**
   * Basta adicionar as variáveis numeradas em **Settings > Environment Variables** na Vercel:
     * `USER_1_ID` = `SEU_ID_DO_TELEGRAM`
     * `USER_1_NAME` = `Usuario_1`
     * `USER_2_ID` = `ID_DO_OUTRO_USUARIO`
     * `USER_2_NAME` = `Usuario_2`
     * `USER_2_APPS_SCRIPT_URL` = `https://script.google.com/macros/s/LINK_DA_PESSOA/exec`
   * *(Suporta até `USER_20_ID`! Quando `USER_1_ID` é configurado, o bot também ativa o bloqueio de segurança automático contra estranhos).*

---

## Autor e Licença

- **Autor:** [Ariston Gomes](https://www.linkedin.com/in/ariston-gomes/)
- **Licença:** Distribuído sob a licença MIT.
