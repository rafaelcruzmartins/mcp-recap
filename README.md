# MCP ReCap

Servidor MCP próprio para o **Autodesk ReCap Pro no Windows**. Permite que agentes de IA (Claude, Antigravity, Codex/ChatGPT) **vejam e naveguem na nuvem de pontos**, consultem projetos do ReCap e criem projetos novos com o **DeCap.exe**, a ferramenta de linha de comando que acompanha o ReCap.

Testado com **ReCap Pro 2027 (27.0.1.242)** e DeCap 27.0.1.242 no Windows 11, Python 3.11 a 3.13 e SDK oficial `mcp` 1.30.

## Instalar em qualquer PC (Claude Desktop)

No PC com o ReCap Pro instalado e ativado, abra a aba **Code** do Claude Desktop e envie:

```
Instale o servidor MCP deste repositório no meu Claude Desktop seguindo o arquivo claude/setup-recap-mcp.md: https://github.com/rafaelcruzmartins/mcp-recap
```

O Claude Code segue o passo a passo do [`setup-recap-mcp.md`](claude/setup-recap-mcp.md): instala o `uv` se faltar, baixa o código em `%LOCALAPPDATA%\mcp-recap\src`, prepara o ambiente, roda o diagnóstico e registra o servidor `recap` no Claude Desktop e no Claude Code, com backup e sem tocar nos outros servidores. No fim, feche o Claude Desktop pela bandeja do sistema (botão direito > Sair), abra de novo e peça num chat novo: *"verifique a instalação do ReCap, incluindo a licença"*.

Para atualizar, envie a mesma mensagem: o passo a passo faz `git pull` quando o código já existe.

## Escolha o agente

| Agente | Tutorial |
|---|---|
| **Claude** (Claude Code + Claude Desktop) | [claude/](claude/README.md): passo a passo e o arquivo [`setup-recap-mcp.md`](claude/setup-recap-mcp.md) para o Claude Code executar |
| **Google Antigravity** | [antigravity/](antigravity/README.md): cole o arquivo numa conversa nova do Antigravity |
| **ChatGPT / Codex** | [chatgpt/](chatgpt/README.md): cadastro do MCP local no Codex |

## O que ele faz e o que não faz

| Faz | Não faz |
|---|---|
| Detecta a instalação, a versão e as opções do DeCap disponíveis | Não controla a interface do ReCap (o DeCap não é uma API de automação da interface) |
| Lê os pontos de `.rcp`/`.rcs` e gera imagens: perspectiva, planta e corte | Não lê normais, classificação, regiões nem as imagens panorâmicas (RealView) |
| Lê metadados de projetos `.rcp` (manifesto XML) e o cabeçalho de `.rcs` | Não mede sozinho: `pick_point` dá as coordenadas e o agente calcula |
| Cria projetos **novos** a partir de scans, com opções do DeCap | Não edita nem sobrescreve projetos existentes |
| Acompanha, lista e cancela as importações que ele mesmo iniciou | Não executa comandos arbitrários de shell ou código |
| Abre um projeto existente no ReCap, quando chamado explicitamente | Não usa a Reality Capture API na nuvem; nenhum dado sai da máquina |

## Ferramentas

### Ver e navegar na nuvem

Os pontos são lidos direto do `.rcp`/`.rcs`. O ReCap não precisa estar aberto, nada é exportado e nenhum outro programa é instalado. Cada ferramenta devolve uma imagem e um JSON com a câmera e as coordenadas.

| Ferramenta | Para quê |
|---|---|
| `open_cloud` | Abre um `.rcp` ou `.rcs`; devolve limites, número de pontos e a vista geral |
| `view_cloud` | Perspectiva a partir de uma posição, com rumo e inclinação ou um ponto para olhar |
| `move_camera` | Anda (frente, lado, cima) e gira em relação à posição atual |
| `orbit_camera` | Gira em torno de um ponto e aproxima ou afasta |
| `go_to_pixel` | Leva a câmera até o que aparece num pixel da última imagem |
| `pick_point` | Coordenadas X, Y, Z do ponto desenhado num pixel |
| `plan_view` | Planta de uma faixa de cotas, com X e Y nas bordas |
| `section_view` | Corte vertical entre dois pontos, com distância e cota nas bordas |

Testado numa nuvem unificada de 1,26 bilhão de pontos (29 GB): 1 a 2 s por imagem. Num projeto de 98 scans e 4,1 bilhões de pontos: 3 a 7 s. Como funciona e o que foi conferido: [docs/formato-rcs.md](docs/formato-rcs.md).

### Projetos e importação

| Ferramenta | Para quê |
|---|---|
| `check_recap_installation` | Versão, caminhos, opções do DeCap e, se pedido, estado da licença |
| `find_projects` | Procura `.rcp`/`.rcs` numa pasta, com limites de profundidade e quantidade |
| `read_project` | Metadados de um `.rcp`, ou o cabeçalho de um `.rcs` |
| `list_scans` | Scans do projeto, arquivos `.rcs` referenciados e os ausentes |
| `import_scans` | Cria um projeto novo com o DeCap (assíncrono, devolve `job_id`) |
| `get_job_status` | Estado, horários, código de saída, final do log e verificação da saída |
| `list_jobs` | Jobs gerenciados pelo servidor |
| `cancel_job` | Cancela um job ativo iniciado por esta instância do servidor |
| `open_project_in_recap` | Abre um `.rcp` existente na interface do ReCap |

Parâmetros, exemplos e códigos de erro: [docs/ferramentas.md](docs/ferramentas.md).

## Instalação rápida

Pré-requisitos: Windows 10/11, ReCap Pro instalado e **ativado**, [uv](https://docs.astral.sh/uv/) e Git.

```powershell
git clone https://github.com/rafaelcruzmartins/mcp-recap.git
cd mcp-recap
uv sync
uv run mcp-recap --diagnose --check-license
```

O diagnóstico imprime um JSON. `"ready_for_import": true` e `"license": {"state": "valida"}` indicam que tudo está pronto. Depois registre o servidor no seu cliente seguindo o tutorial do agente (tabela acima), ou use o gerador de configuração:

```powershell
uv run mcp-recap-configurar --client claude-desktop --dry-run
uv run mcp-recap-configurar --client claude-desktop
```

Para o Claude Desktop, o gerador procura `%APPDATA%\Claude\claude_desktop_config.json`, `%LOCALAPPDATA%\Claude-Data\<conta>\claude_desktop_config.json` (versões recentes do app) e a variante da Microsoft Store, e atualiza os que existirem. Ele preserva os outros servidores MCP, faz backup `.bak-AAAAMMDD-HHMMSS` antes de alterar um arquivo existente e não toca em arquivos com JSON/TOML inválido. Clientes aceitos: `claude-desktop`, `antigravity`, `codex` e `claude-code` (este só mostra o comando `claude mcp add`; com `--run` ele executa).

Comando para iniciar o servidor (STDIO), usado pelos clientes:

```powershell
uv --directory <pasta do mcp-recap> run mcp-recap
```

## Como funciona

```
cliente MCP ──STDIO──> mcp-recap (Python, FastMCP)
                          ├─ installation.py  descoberta do ReCap, decap --help/--version/--checkLicense
                          ├─ projects.py      leitura do .rcp (ZIP+XML em memória) e do cabeçalho .rcs
                          ├─ rcs.py           leitura dos pontos do .rcs (octree ADOCT 3.x)
                          ├─ scene.py         projeto com vários scans, amostragem por orçamento e cache
                          ├─ render.py        imagem em numpy + Pillow (z-buffer, sombreamento por profundidade)
                          ├─ viewer.py        câmera, perspectiva, planta, corte e apontar pixel
                          ├─ view_tools.py    ferramentas MCP de visualização
                          ├─ decap.py         validação e montagem dos argumentos do DeCap
                          ├─ jobs.py          processos assíncronos, log, verificação e persistência
                          └─ recap_app.py     abertura de projeto na interface do ReCap
```

* STDOUT é exclusivo do protocolo MCP. Logs vão para STDERR e para `%LOCALAPPDATA%\mcp-recap\logs\server.log`.
* Cada importação vira um job com pasta própria em `%LOCALAPPDATA%\mcp-recap\jobs\<id>` (`job.json` e `decap.log`).
* O DeCap retorna código 0 mesmo quando falha. O servidor só marca `succeeded` depois de conferir mensagens de erro, a criação e a leitura do `.rcp`, a existência dos `.rcs`, o número de pontos no cabeçalho e, quando pedido, o sistema de coordenadas gravado.

Decisões de projeto e comportamentos observados do DeCap: [docs/limitacoes.md](docs/limitacoes.md).

## Diagnóstico

```powershell
uv run mcp-recap --diagnose                 # instalação, opções do DeCap e últimos jobs
uv run mcp-recap --diagnose --check-license # também executa decap --checkLicense
```

Problemas comuns e soluções: [docs/diagnostico.md](docs/diagnostico.md).

## Licença do ReCap e privacidade

* O DeCap usa a **mesma licença do ReCap Pro** (`--importWithLicense`) e não ativa licenças. Sem licença ativa, a importação falha; a leitura de metadados continua funcionando.
* A existência do `decap.exe` não prova licença válida. `check_recap_installation` informa `"desconhecido"` até ser chamado com `check_license=true`.
* O modo `--import <token> <chave pública>` do DeCap não é exposto, para não exigir credenciais.
* Tudo roda localmente. O servidor não envia nuvens, caminhos nem metadados para serviços externos. O cliente MCP (o agente) recebe os metadados que você pedir; confira a política de dados do seu cliente antes de usar com projetos sigilosos.
* O histórico de gravação do `.rcp` é devolvido sem o nome de usuário do Windows (`UserId`).
* Não versione nuvens de pontos, projetos `.rcp`/`.rcs` nem logs: o `.gitignore` já bloqueia esses formatos.

## Desenvolvimento e testes

```powershell
uv sync
uv run ruff check src tests
uv run pytest -m "not integration"          # não exige ReCap (DeCap simulado)

$env:RECAP_MCP_INTEGRATION = "1"            # exige ReCap Pro instalado e licenciado
uv run pytest -m integration -s
$env:RECAP_MCP_INTEGRATION_GUI = "1"        # opcional: abre a interface do ReCap
```

Os testes de integração geram nuvens sintéticas pequenas em pastas temporárias novas (com espaços e acentos no caminho) e nunca usam dados reais. Um workflow de CI para o GitHub Actions (Windows, Python 3.11 a 3.13, só os testes que não dependem do ReCap) está descrito em [docs/ci.md](docs/ci.md), pronto para ser adicionado em `.github/workflows/`.

Variáveis de ambiente:

| Variável | Uso |
|---|---|
| `RECAP_HOME` | Pasta que contém `decap.exe`, se a detecção automática falhar |
| `RECAP_MCP_STATE_DIR` | Pasta de estado (jobs e logs). Padrão: `%LOCALAPPDATA%\mcp-recap` |
| `RECAP_MCP_MAX_JOBS` | Importações simultâneas (1 a 8, padrão 2) |
| `RECAP_MCP_DECAP_LAUNCHER` | **Somente para testes automatizados**: comando JSON que substitui o `decap.exe` |
