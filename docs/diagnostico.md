# Diagnóstico e solução de problemas

## Procedimento

1. Na pasta do repositório:

   ```powershell
   uv run mcp-recap --diagnose --check-license
   ```

2. Confira no JSON:

   | Campo | Esperado |
   |---|---|
   | `installation.found` | `true` |
   | `installation.decap_exe` | caminho do `decap.exe` |
   | `decap.help_available` | `true` |
   | `decap.modes` | contém `--importWithLicense` |
   | `license.state` | `valida` |
   | `ready_for_import` | `true` |

3. Logs do servidor: `%LOCALAPPDATA%\mcp-recap\logs\server.log`. Logs de cada importação: `%LOCALAPPDATA%\mcp-recap\jobs\<job_id>\decap.log`.
4. Para conferir o protocolo sem cliente, rode os testes STDIO: `uv run pytest tests/test_mcp_stdio.py -q`.

## Problemas comuns

| Sintoma | Causa e solução |
|---|---|
| `installation.found: false` | ReCap não instalado ou em pasta fora do padrão. Defina `RECAP_HOME` com a pasta que contém `decap.exe`. |
| `license.state` diferente de `valida` | Abra o ReCap Pro e entre com a conta licenciada. O DeCap não ativa licenças. |
| Job `failed` com `DeCap informou erro 1025` | Arquivo de entrada não encontrado pelo DeCap. Confira o caminho e permissões. |
| Job `failed` com `marcou como falha os scans` | O DeCap não conseguiu ler o scan (formato ou conteúdo). Teste o arquivo na interface do ReCap. |
| Job `failed`: `Sistema de coordenadas pedido ... não foi gravado` | Código de CS inexistente na biblioteca da Autodesk. Use o código exato mostrado no ReCap/Civil 3D (ex.: `SIRGAS2000.UTM-23S`). |
| Job `failed`: `.rcp esperado não foi criado` com `unify` | Comportamento observado no DeCap 27.0.1.242; importe sem `unify`. |
| `destination_exists` | O servidor nunca sobrescreve. Escolha outro `project_name`/pasta ou remova manualmente o `.rcp` e a pasta `<nome> Support`. |
| `too_many_jobs` | Aguarde ou cancele um job; ajuste `RECAP_MCP_MAX_JOBS`. |
| Job `interrupted` | O servidor foi reiniciado durante a importação. Confira os arquivos no destino; repita em pasta nova. |
| `limit_exceeded` ao importar muitos arquivos com acentos fora do ANSI | Divida a importação em lotes menores ou use caminhos mais curtos. |
| `open_project_in_recap` com `load_confirmed: null` | O ReCap ainda está iniciando (pode levar 1 a 2 min). Repita com `wait_for_load_seconds` maior ou confira a janela. |
| `recap_already_running` ao abrir projeto | Abra o projeto pelo ReCap que já está aberto, ou feche-o e repita. |
| `recap` não aparece no chat do Claude Desktop | Rode `uv run mcp-recap-configurar --client claude-desktop --dry-run` e confira o `path`: deve ser o arquivo que o app usa (`%APPDATA%\Claude\` ou `%LOCALAPPDATA%\Claude-Data\<conta>\`). Depois saia do app pela bandeja e abra de novo. |
| Ferramentas não aparecem no cliente | Reinicie o cliente (no Claude Desktop, saia pela bandeja do sistema). Confira se o caminho do `uv.exe` e do repositório na configuração estão corretos. |
| Erro de timeout ao iniciar no Codex | Na primeira execução o `uv` instala dependências. Rode `uv sync` antes; o gerador já define `startup_timeout_sec = 60`. |
| Qualquer outro | Rode o diagnóstico e abra uma issue com o JSON (remova caminhos sigilosos). |
