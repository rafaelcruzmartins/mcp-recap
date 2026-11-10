# Conectar o ReCap ao Google Antigravity (instruções para o agente)

> **Para quem vai usar:** abra o Antigravity, clique em **New Conversation**, cole este texto inteiro (ou anexe este arquivo) e envie. O agente faz a configuração e para nos pontos em que precisa de você. Testado com ReCap Pro 2027 (27.0.1.242).

---

## Para o agente: objetivo e regras

Você vai configurar este Windows para que o Antigravity use as ferramentas do Autodesk ReCap através do servidor MCP `mcp-recap` (STDIO). O servidor roda com `uv`, lê metadados de projetos `.rcp`/`.rcs` e cria projetos novos com o `DeCap.exe` do ReCap.

**Regras obrigatórias:**

1. **Trabalhe por etapas.** Em cada **⏸ PARE**, mostre o que encontrou ou fez e espere a resposta do usuário.
2. **Nada de caminhos fixos.** Descubra os caminhos reais (`$env:USERPROFILE`, `$env:LOCALAPPDATA`, `where.exe uv`).
3. **Não use PowerShell como administrador** e não altere configurações de segurança do Windows.
4. **Faça backup antes de editar** qualquer configuração e mostre o caminho do backup.
5. **Não remova nem altere outros servidores MCP** do `mcp_config.json`.
6. **Grave JSON só com Python** (o gerador do projeto faz isso em UTF-8 sem BOM). Não use `Set-Content`, `Out-File` nem `>` do PowerShell 5.1 para JSON.
7. **Não envie nuvens de pontos nem projetos para a internet.** Testes só com dados sintéticos.
8. Se um comando falhar, mostre o erro completo e explique a causa provável antes de tentar outra coisa.

---

## Etapa 0: Diagnóstico (sem alterar nada)

```powershell
"Usuário: $env:USERNAME"
"ReCap: " + ((Get-ChildItem "$env:ProgramFiles\Autodesk" -Directory | Where-Object Name -match 'ReCap').FullName -join ", ")
"decap.exe: " + ((Get-ChildItem "$env:ProgramFiles\Autodesk\*ReCap*\decap.exe" -ErrorAction SilentlyContinue).FullName -join ", ")
"uv: " + ((where.exe uv 2>$null) -join ", ")
"git: " + (Get-Command git -ErrorAction SilentlyContinue).Source
"Servidor baixado: " + (Test-Path "$env:LOCALAPPDATA\mcp-recap\src\pyproject.toml")
"mcp_config.json: " + (Test-Path "$env:USERPROFILE\.gemini\config\mcp_config.json")
```

Mostre a tabela ao usuário e diga quais etapas faltam. Sem `decap.exe`, o ReCap Pro precisa ser instalado antes.

**⏸ PARE.**

## Etapa 1: Instalar o uv (se faltar)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
& "$env:USERPROFILE\.local\bin\uv.exe" --version
```

Use sempre o caminho completo do `uv.exe` daqui em diante.

## Etapa 2: Baixar e preparar o servidor

Se `$env:LOCALAPPDATA\mcp-recap\src` já existir (por exemplo, instalado para o Claude), **reaproveite como está**: não apague nem atualize.

```powershell
$src = "$env:LOCALAPPDATA\mcp-recap\src"
git clone https://github.com/rafaelcruzmartins/mcp-recap.git $src
$uv = "$env:USERPROFILE\.local\bin\uv.exe"
& $uv --directory $src sync
& $uv --directory $src run mcp-recap --diagnose --check-license
```

Mostre `ready_for_import` e `license.state`. Se a licença não for `valida`, peça ao usuário para abrir o ReCap Pro e entrar com a conta licenciada.

**⏸ PARE.**

## Etapa 3: Registrar no Antigravity

O Antigravity lê `~/.gemini/config/mcp_config.json` (no App, confira o caminho em **Customizations → MCP Servers → Copy Config File Path**). Use o gerador, que preserva os outros servidores e faz backup:

```powershell
& $uv --directory $src run mcp-recap-configurar --client antigravity --uv $uv --dry-run
& $uv --directory $src run mcp-recap-configurar --client antigravity --uv $uv
```

Se o caminho do App for diferente, acrescente `--config "<caminho copiado>"`. A entrada criada é:

```json
"recap": {"command": "<uv.exe>", "args": ["--directory", "<src>", "run", "mcp-recap"]}
```

Mostre o caminho do backup. Peça ao usuário para atualizar a lista de servidores MCP (ou reiniciar o Antigravity).

**⏸ PARE.**

## Etapa 4: Teste

1. Chame `check_recap_installation` com `check_license=true`.
2. Gere um arquivo sintético numa pasta temporária nova e importe:

```powershell
$t = New-Item -ItemType Directory -Path "$env:TEMP\recap-teste-$(Get-Date -Format yyyyMMddHHmmss)"
$linhas = foreach($i in 0..19){ foreach($j in 0..19){ "{0} {1} 0 50 120 120 120" -f ($i*0.1), ($j*0.1) } }
Set-Content -Path "$t\grade.pts" -Value (@("400") + $linhas) -Encoding ascii
"$t"
```

3. Chame `import_scans` com `inputs=["<pasta>\grade.pts"]`, `output_folder="<pasta>\saida"`, `project_name="teste"`, e acompanhe com `get_job_status` até `succeeded`. Confira `verification.total_points = 400`.

Informe o resultado ao usuário.

> Validação: o gerador de `mcp_config.json` foi testado com arquivos temporários (`tests/test_client_config.py`) e o servidor foi testado por STDIO com o cliente do SDK MCP. A execução dentro do Antigravity não foi validada neste projeto porque o Antigravity não estava instalado na máquina de testes.
