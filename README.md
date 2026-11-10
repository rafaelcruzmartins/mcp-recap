# MCP ReCap

Servidor MCP próprio para o **Autodesk ReCap Pro no Windows**. Permite que agentes de IA (Claude, Antigravity, Codex/ChatGPT) consultem projetos do ReCap e criem projetos novos com o **DeCap.exe**, a ferramenta de linha de comando que acompanha o ReCap.

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
| Lê metadados de projetos `.rcp` (manifesto XML) e o cabeçalho de `.rcs` | Não lê os pontos de `.rcs` (exige o Reality Solutions SDK, distribuído pela ADN) |
| Lê pontos dos arquivos de origem (E57, LAS/LAZ, PTS/XYZ) para resumos, cortes e imagens | Não devolve a nuvem inteira: só contagens, estatísticas, amostras limitadas e imagens |
| Cria projetos **novos** a partir de scans, com opções do DeCap | Não edita nem sobrescreve projetos existentes |
| Acompanha, lista e cancela as importações que ele mesmo iniciou | Não executa comandos arbitrários de shell ou código |
| Abre um projeto existente no ReCap, quando chamado explicitamente | Não usa a Reality Capture API na nuvem; nenhum dado sai da máquina |

## Ferramentas

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

### Leitura de pontos (resumos, recortes e imagens)

Sem o Reality Solutions SDK (que não acompanha o ReCap), os pontos são lidos dos **arquivos de origem** dos scans: `.e57`, `.las`, `.laz`, `.pts` e `.xyz`. Informe o próprio arquivo ou o `.rcp`, e o servidor usa os arquivos gravados em `rawScanPath`. Os `.rcs` continuam sem ser decodificados, e scans FARO `.fls` não são lidos.

| Ferramenta | Para quê |
|---|---|
| `point_cloud_info` | Pontos por scan, limites, atributos, unidade, sistema de coordenadas, poses scan→arquivo e estado do índice |
| `index_point_cloud` | Indexa a nuvem em blocos (cache local). Até 3 milhões de pontos indexa na hora; acima disso vira job |
| `query_box` | Contagem, limites reais, estatísticas de Z e amostra decimada (padrão 2.000, máximo 10.000 pontos) |
| `z_histogram` | Histograma de Z com picos (candidatos a pisos, lajes e forros) |
| `section` | Corte horizontal (cota + espessura) ou vertical (p1→p2 + espessura): pontos 2D decimados e PNG |
| `render_view` | PNG ortográfico (topo, frente, fundos, laterais ou direção livre), cor por RGB, intensidade ou elevação, com eixos, escala gráfica e legenda |
| `profile` | Perfil ao longo de uma polilinha com faixa de largura definida: Z mínimo, mediano e máximo por estaca, pontos 2D e PNG |
| `nearest_points` / `measure` | Vizinhos de uma coordenada; distância 3D e horizontal, com encaixe opcional nos pontos da nuvem |
| `export_subset` | Exporta caixa ou seção para `.pts`, `.xyz` ou `.e57` novo (job, sem sobrescrever) |

Detalhes, limites e exemplos: [docs/pontos.md](docs/pontos.md). Toda resposta traz `coordinate_frame`, que indica se as coordenadas são do arquivo de origem e se são georreferenciadas. Quando a consulta parte de um `.rcp`, o servidor também diz se os limites lidos coincidem com os do projeto ReCap. Regiões com mais pontos que o orçamento são lidas com 1 a cada N pontos (nível de detalhe) e a resposta avisa que a contagem é estimada.

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
| `RECAP_MCP_MAX_QUERY_POINTS` | Pontos lidos por consulta antes de passar a amostrar (padrão 10 000 000, cerca de 600 MB) |
| `RECAP_MCP_RENDER_MAX_POINTS` | Pontos usados por imagem (padrão 4 000 000) |
| `RECAP_MCP_QUERY_TIMEOUT_S` | Tempo máximo de uma consulta, em segundos (padrão 120) |
| `RECAP_MCP_SYNC_INDEX_POINTS` | Até quantos pontos a indexação é feita na hora, sem job (padrão 3 000 000) |
| `RECAP_MCP_MAX_E57_EXPORT_POINTS` | Limite de pontos da exportação `.e57`, montada em memória (padrão 50 000 000) |
| `RECAP_MCP_DECAP_LAUNCHER` | **Somente para testes automatizados**: comando JSON que substitui o `decap.exe` |
