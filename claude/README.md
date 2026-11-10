# Como conectar o Claude ao ReCap

Vale para o **Claude Code** (CLI ou aba Code do Claude Desktop) e para o **chat do Claude Desktop**. Testado com ReCap Pro 2027 (27.0.1.242).

**Visão geral.** Você só precisa ter o ReCap Pro instalado e ativado. O resto (uv, download do servidor, ambiente Python, registro no Claude Code e no Claude Desktop e teste) fica a cargo do Claude Code, que segue o arquivo [`setup-recap-mcp.md`](setup-recap-mcp.md).

**Pré-requisitos:** Windows 10/11, ReCap Pro com licença ativa, Claude Desktop com conta Pro ou superior.

## 1. Confirmar o ReCap

Abra o ReCap Pro uma vez e confirme que ele entra com a sua conta licenciada. Depois pode fechá-lo: o servidor usa o `DeCap.exe`, que não precisa da interface aberta.

## 2. Deixar o Claude Code fazer o resto

**a) Abra o Code no Claude Desktop** (ícone **</>** no canto superior esquerdo) ou um terminal com `claude`.

**b) Anexe o [`setup-recap-mcp.md`](setup-recap-mcp.md) e envie:**

```
Execute todas as etapas deste arquivo até deixar tudo funcionando.
```

Ele instala o uv (se faltar), clona o repositório, roda `uv sync`, faz o diagnóstico, registra o servidor `recap` no Claude Code e no Claude Desktop (com backup das configurações) e testa. Autorize os comandos quando ele pedir.

## 3. Cadastro manual (alternativa)

Na pasta do repositório:

```powershell
$uv   = (Get-Command uv).Source
$repo = (Get-Location).Path
claude mcp add --scope user recap -- $uv --directory $repo run mcp-recap
claude mcp list
```

Para o chat do Claude Desktop, edite `%APPDATA%\Claude\claude_desktop_config.json` (versões recentes do app: `%LOCALAPPDATA%\Claude-Data\<conta>\claude_desktop_config.json`; instalação pela Microsoft Store: `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json`) acrescentando dentro de `mcpServers`:

```json
"recap": {
  "command": "C:/Users/<você>/.local/bin/uv.exe",
  "args": ["--directory", "C:/caminho/para/mcp-recap", "run", "mcp-recap"]
}
```

Ou use o gerador, que preserva os outros servidores e faz backup:

```powershell
uv run mcp-recap-configurar --client claude-desktop --client claude-code --run
```

Reinicie o Claude Desktop pela bandeja do sistema (botão direito > Sair).

## 4. Primeiro teste

No chat, peça: **"verifique a instalação do ReCap, inclusive a licença"** (`check_recap_installation`). Depois: **"procure projetos ReCap em D:\Scans"** e **"leia os metadados do projeto X"**.

Para importar: **"importe D:\Scans\estacao01.e57 para um projeto novo chamado Teste em D:\ReCap\teste"**. O Claude recebe um `job_id` e acompanha com `get_job_status`.

## Problemas comuns

Veja [docs/diagnostico.md](../docs/diagnostico.md).

## Cuidados

* As importações criam sempre projetos **novos**; o servidor recusa destinos existentes.
* Trabalhe com cópias quando testar dados de projeto reais.
* O agente recebe os metadados que consultar (nomes de arquivos, caminhos, coordenadas). Não use com projetos sigilosos se a política do seu cliente não permitir.
