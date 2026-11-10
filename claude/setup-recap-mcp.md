# Tarefa: conectar o Claude ao Autodesk ReCap (mcp-recap)

Você está rodando no Claude Code, num PC Windows. Sua tarefa é instalar o servidor MCP `mcp-recap` (repositório https://github.com/rafaelcruzmartins/mcp-recap) e deixá-lo funcionando no chat do Claude Desktop e no Claude Code.

## Regras de execução

- Trabalhe de forma autônoma; execute, verifique e corrija sozinho. Pare só para algo que o usuário precise fazer com as próprias mãos (ex.: entrar na conta do ReCap, reiniciar o Claude Desktop).
- Use PowerShell, sem privilégios de administrador. Instale tudo no perfil do usuário.
- Não presuma caminhos nem nome de usuário: use `$env:USERPROFILE`, `$env:APPDATA`, `$env:LOCALAPPDATA`, `Get-Command`.
- Antes de editar qualquer configuração existente, faça backup `.bak-AAAAMMDD-HHMMSS` ao lado do original. Não remova nem altere outros servidores MCP.
- Não feche nem reinicie o Claude Desktop (você pode estar dentro dele). Ao final, peça ao usuário para reiniciar.
- Não envie nuvens de pontos nem projetos para a internet. Nos testes, use apenas dados sintéticos.

## Etapas

### 1. Diagnóstico inicial
Rode e anote o resultado:

```powershell
[Environment]::OSVersion.VersionString
"git: " + (Get-Command git -ErrorAction SilentlyContinue).Source
"uv: "  + (Get-Command uv  -ErrorAction SilentlyContinue).Source
Get-ChildItem "$env:ProgramFiles\Autodesk\*ReCap*\decap.exe" -ErrorAction SilentlyContinue | Select-Object FullName
```

Se `decap.exe` não existir, avise o usuário: a leitura de projetos funcionará, mas importar exige o ReCap Pro instalado (ou a variável `RECAP_HOME` apontando para a pasta do `decap.exe`). Continue a instalação.

### 2. uv
```powershell
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $uv = "$env:USERPROFILE\.local\bin\uv.exe"
    if (-not (Test-Path $uv)) { powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" }
}
& $uv --version
```
Use sempre o caminho absoluto em `$uv` (o PATH do terminal atual pode não ter sido atualizado).

### 3. Código do servidor
```powershell
$src = "$env:LOCALAPPDATA\mcp-recap\src"
if (Test-Path "$src\.git") {
    git -C $src pull --ff-only
} elseif (Test-Path $src) {
    Move-Item $src "$src.old-$(Get-Date -Format yyyyMMdd-HHmmss)"   # pasta antiga sem git: preserve
    git clone https://github.com/rafaelcruzmartins/mcp-recap.git $src
} else {
    git clone https://github.com/rafaelcruzmartins/mcp-recap.git $src
}
Test-Path "$src\pyproject.toml"   # tem que dar True
```
Sem git: baixe `https://github.com/rafaelcruzmartins/mcp-recap/archive/refs/heads/main.zip`, extraia e mova o conteúdo da pasta extraída para `$src` (o `pyproject.toml` precisa ficar na raiz de `$src`).

### 4. Ambiente e diagnóstico
```powershell
& $uv --directory $src sync --locked
& $uv --directory $src run mcp-recap --diagnose --check-license
```
Confira no JSON: `ready_for_import: true` e `license.state: "valida"`. Se a licença não for válida, peça ao usuário para abrir o ReCap Pro, entrar com a conta licenciada e avisar; depois repita o diagnóstico.

### 5. Registrar no chat do Claude Desktop
Use o gerador do projeto. Ele faz backup, preserva os outros servidores, grava JSON UTF-8 sem BOM e não altera arquivos com JSON inválido.

```powershell
& $uv --directory $src run mcp-recap-configurar --client claude-desktop --uv $uv --dry-run
```

Leia o `path` no resultado. O gerador procura, nesta ordem, os arquivos que existirem:
- `%APPDATA%\Claude\claude_desktop_config.json`
- `%LOCALAPPDATA%\Claude-Data\<conta>\claude_desktop_config.json` (versões recentes do app)
- `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json` (Microsoft Store)

Se nenhum existir, ele cria o primeiro. Se houver dúvida sobre qual arquivo o app usa, peça ao usuário para abrir no Claude Desktop **Configurações → Desenvolvedor → Editar configuração** e informar o caminho do arquivo que abrir; então acrescente `--config "<esse caminho>"`. Depois aplique:

```powershell
& $uv --directory $src run mcp-recap-configurar --client claude-desktop --uv $uv
```

Mostre ao usuário o caminho do backup (`backup`) e a lista `other_servers` (servidores preservados). Se o resultado trouxer `error` (JSON inválido), mostre o erro, faça backup e corrija o arquivo unindo tudo num único objeto JSON antes de repetir.

### 6. Registrar no Claude Code
```powershell
claude mcp remove recap --scope user 2>$null
claude mcp add --scope user recap -- $uv --directory $src run mcp-recap
claude mcp list
```
Confirme que `recap` aparece como conectado.

### 7. Teste
1. Protocolo, sem depender do ReCap:
   ```powershell
   & $uv --directory $src run pytest tests/test_mcp_stdio.py -q
   ```
2. Se as ferramentas `recap` já aparecem nesta sessão, chame `check_recap_installation` com `check_license=true`. Se não aparecerem, diga ao usuário para abrir uma sessão nova e pedir o teste lá.
3. Opcional, importação com dado sintético:
   ```powershell
   $t = New-Item -ItemType Directory -Path "$env:TEMP\recap-teste-$(Get-Date -Format yyyyMMddHHmmss)"
   $linhas = foreach($i in 0..19){ foreach($j in 0..19){ "{0} {1} 0 50 120 120 120" -f ($i*0.1), ($j*0.1) } }
   Set-Content -Path "$t\grade.pts" -Value (@("400") + $linhas) -Encoding ascii
   ```
   Chame `import_scans` com `inputs=["<$t>\grade.pts"]`, `output_folder="<$t>\saida"`, `project_name="teste"` e acompanhe com `get_job_status` até `succeeded`. Confira `verification.total_points = 400`.

### 8. Relatório final
Em poucas linhas: pasta do servidor (`$src`), backups criados, servidores preservados, resultado do diagnóstico e dos testes, e o que o usuário ainda precisa fazer: **fechar o Claude Desktop pela bandeja do sistema (botão direito > Sair), abrir de novo** e pedir num chat novo *"verifique a instalação do ReCap, incluindo a licença"*.

Para atualizar no futuro, repita as etapas 3 e 4.
