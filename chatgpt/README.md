# ReCap com ChatGPT e Codex

O servidor `mcp-recap` é local (STDIO). Clientes da OpenAI que aceitam MCP local podem usá-lo; o ChatGPT no navegador, não.

| Onde você quer usar | Como conectar |
| --- | --- |
| Codex (CLI, extensão ou aplicativo) no Windows | Cadastre o servidor local como STDIO (abaixo). |
| ChatGPT no aplicativo para Windows | Se o aplicativo oferecer cadastro de MCP local nas configurações, use os mesmos campos. Disponibilidade depende do plano e do modo de conversa; não validado neste projeto. |
| ChatGPT no navegador (`chatgpt.com`) | Exige servidor MCP remoto por HTTP. Este projeto não expõe HTTP; não se aplica. |

## 1. Instalar o servidor

Siga a [instalação rápida do README](../README.md#instalação-rápida) e confirme o diagnóstico:

```powershell
uv run mcp-recap --diagnose --check-license
```

## 2. Cadastrar no Codex

Com a CLI:

```powershell
$uv   = (Get-Command uv).Source
$repo = "<pasta do mcp-recap>"
codex mcp list
codex mcp add recap -- $uv --directory $repo run mcp-recap
```

Ou pelo gerador, que edita `~/.codex/config.toml` com backup, preserva o resto do arquivo e define tempos maiores (a primeira execução do `uv` e a verificação de licença podem passar do padrão de 10 s de inicialização e 60 s por ferramenta):

```powershell
uv run mcp-recap-configurar --client codex --dry-run
uv run mcp-recap-configurar --client codex
```

Trecho gerado:

```toml
[mcp_servers.recap]
command = "C:\\Users\\<você>\\.local\\bin\\uv.exe"
args = ["--directory", "C:\\caminho\\mcp-recap", "run", "mcp-recap"]
startup_timeout_sec = 60
tool_timeout_sec = 300
```

Referência: [documentação da OpenAI sobre MCP no Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## 3. Testar

Numa nova sessão, peça: **"Use check_recap_installation com check_license=true e informe a versão do ReCap."** Depois peça para listar projetos de uma pasta com `find_projects`.

> Validação: o cadastro gerado foi testado com arquivos temporários (`tests/test_client_config.py`). A execução dentro do Codex não foi validada neste projeto porque o Codex não estava instalado na máquina de testes.
