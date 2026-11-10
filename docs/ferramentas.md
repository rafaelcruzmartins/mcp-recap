# Ferramentas, parâmetros e exemplos

Todos os caminhos devem ser **absolutos**. Caminhos com espaços e acentos são aceitos. Cada resposta de sucesso traz `"ok": true`.

## Formato de erro

Erros de domínio voltam como resultado MCP com `isError: true` e o mesmo corpo em texto JSON e em `structuredContent`:

```json
{"ok": false, "error": {"code": "destination_exists", "message": "Já existe 'D:\\obra\\saida\\Pavimento.rcp'. ...", "details": {"path": "..."}}}
```

| Código | Quando |
|---|---|
| `invalid_argument` | Parâmetro fora da faixa, caminho relativo, nome de projeto inválido, combinação de opções incoerente |
| `path_not_found`, `not_a_file`, `not_a_directory` | Caminho inexistente ou do tipo errado |
| `unsupported_format` | Extensão não aceita pela ferramenta |
| `invalid_project` | `.rcp` corrompido, sem manifesto ou com XML inválido; `.rcs` truncado |
| `limit_exceeded` | Limites de leitura (tamanho do manifesto, entradas do ZIP) ou de lista de arquivos |
| `destination_exists` | O `.rcp` ou a pasta `<nome> Support` de destino já existem |
| `option_unsupported` | A opção não aparece em `decap.exe --help` da versão instalada |
| `decap_not_found`, `recap_not_found` | ReCap/DeCap não encontrados |
| `job_not_found`, `job_not_cancellable`, `too_many_jobs` | Gerenciamento de jobs |
| `launch_failed` | O Windows não conseguiu iniciar o processo |
| `recap_already_running` | `open_project_in_recap` com o ReCap já aberto |
| `internal_error` | Falha inesperada (registrada em `server.log`) |

Erros de **esquema** (parâmetro obrigatório ausente, tipo errado) são rejeitados pelo próprio SDK MCP antes de chegar ao servidor e voltam com a mensagem padrão do SDK.

---

## Ver e navegar na nuvem

Coordenadas do projeto em metros, Z para cima. `yaw` é o rumo em graus (0 = norte, +Y; 90 = leste, +X). `pitch` é a inclinação (negativa olha para baixo). Pixels contam a partir do canto superior esquerdo da imagem.

O servidor guarda uma nuvem aberta, uma câmera e a última imagem. `pick_point` e `go_to_pixel` valem para a última imagem, de qualquer tipo.

Parâmetros comuns às ferramentas de imagem: `width` (320 a 1600 px), `height` (240 a 1200 px, só nas perspectivas), `color_by` (`auto`, `rgb`, `intensity`, `elevation`), `max_points` (pontos lidos do disco por imagem), `point_scale` e `pixel_grid` (desenha a grade de pixels sobre a imagem).

| Ferramenta | Parâmetros próprios |
|---|---|
| `open_cloud` | `path` (`.rcp` ou `.rcs`) |
| `view_cloud` | `position` [x, y, z], `look_at` [x, y, z], `yaw`, `pitch`, `fov`, `reset` |
| `move_camera` | `forward`, `right`, `up` (m); `turn_right`, `turn_up` (graus) |
| `orbit_camera` | `right`, `up` (graus), `zoom`, `pivot` [x, y, z] (padrão: centro da última imagem) |
| `go_to_pixel` | `px`, `py`, `stop_distance` (m), `eye_height` (m acima do ponto, olhando na horizontal) |
| `pick_point` | `px`, `py` |
| `plan_view` | `center` [x, y], `width_m`, `height_m`, `z_min`, `z_max` |
| `section_view` | `p1` [x, y], `p2` [x, y], `thickness` (m), `z_min`, `z_max` |

Resposta das perspectivas (além da imagem):

```json
{"ok": true, "view": "perspective",
 "camera": {"position": [360322.0, 7395951.0, 763.2], "yaw": 55.0, "heading": "NE", "pitch": 0.0, "fov": 75.0},
 "image_size": [1024, 768], "color": "rgb",
 "center_point": {"xyz": [360343.448, 7395965.994, 763.18], "distance": 26.17},
 "points_drawn": 999333, "points_read": 2000000, "leaves_visible": 3666, "empty": false,
 "ms": {"read": 233, "draw": 1525}}
```

Roteiro típico: `open_cloud`; `section_view` atravessando o prédio para achar as cotas dos pisos; `plan_view` com uma faixa fina acima do piso para ver paredes e pilares; `go_to_pixel` com `eye_height: 1.6` num ponto do piso para ficar de pé ali; `move_camera` para andar.

Limites: a imagem é uma amostra dos pontos (`max_points`), não a nuvem inteira; não há oclusão além do ponto mais próximo por pixel; `center_point` e `pick_point` devolvem o ponto desenhado, com a precisão da amostra.

---


## check_recap_installation

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `check_license` | bool | `false` | Executa `decap.exe --checkLicense` (alguns segundos) |
| `refresh` | bool | `false` | Refaz a busca da instalação e a leitura do `--help` |

Retorna `installation` (pastas, executáveis, versão do produto e do arquivo, origem da detecção), `decap` (modos e opções presentes no `--help`, versão), `license` (`valida`, `invalida_ou_indisponivel` ou `desconhecido`), `reality_solutions_sdk` (sempre `available: false`; veja limitações), formatos aceitos e limitações.

## find_projects

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `folder` | texto | obrigatório | Pasta onde procurar |
| `include_rcs` | bool | `true` | Inclui `.rcs` |
| `max_depth` | 0 a 32 | 6 | Profundidade de subpastas |
| `max_results` | 1 a 5000 | 200 | Limite de resultados (`truncated: true` quando atingido) |

Não segue links simbólicos nem junções. Para em 20 000 pastas visitadas. Pastas sem permissão aparecem em `access_errors` (até 20).

## read_project

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `path` | texto | `.rcp` ou `.rcs` |

**`.rcp`**: `project_id`, `schema_version`, `save_history` (aplicativo, versão, data; sem usuário), `scan_count`, `total_points`, `coordinate_system`, `geo_reference_origin`, `global_transformation`, `project_bounds` (caixa de `Default Map`; atributos omitidos pelo ReCap são tratados como 0 e listados em `omitted_attributes_as_zero`), `display_unit.unit_type_raw` (código bruto; ver limitações), contagens de medições, regiões, caixas-limite e vistas, pasta Support e `warnings`.

**`.rcs`**: somente o cabeçalho: `format_version`, `num_points`, `guid`, `translation` e `local_data_bounds`. Para ver os pontos, use `open_cloud`.

Exemplo:

```json
{"name": "read_project", "arguments": {"path": "D:\\Obras\\Galpão\\Galpão.rcp"}}
```

## list_scans

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `path` | texto | obrigatório | `.rcp` |
| `verify_rcs_headers` | bool | `true` | Confere `num_points` e o identificador no cabeçalho de cada `.rcs` |
| `max_scans` | 1 a 5000 | 1000 | Limite de scans devolvidos |

Para cada scan: `id`, `name`, `type` (`UNSTRUCTURED`/`STRUCTURED`), `num_points`, presença de RGB/intensidade/normais, `translation`, `registration` e:

* `rcs.resolved_path`, `rcs.resolved_by` (`RelativePath`, `Path` ou `pasta Support`), `rcs.exists`, `rcs.manifest_path_lossy` e `rcs.header_check`;
* `source_scan` (arquivo bruto de origem): `exists` é `null` quando o caminho gravado está corrompido (contém U+FFFD).

Um projeto movido para outra pasta continua legível: o `.rcs` é procurado na pasta `<projeto> Support` ao lado do `.rcp`.

`missing_rcs_count` resume os `.rcs` ausentes.

## import_scans

| Parâmetro | Tipo | Opção do DeCap | Observação |
|---|---|---|---|
| `inputs` | lista de textos | (posicionais) | Até 500 arquivos, sem repetição |
| `output_folder` | texto | `<output folder>` | Criada se não existir (a pasta-mãe precisa existir) |
| `project_name` | texto | `<project name>` | Sem `\ / : * ? " < > \|`; `.rcp` final é removido |
| `decimation_mm` | número > 0 | `--decimation` | Distância mínima entre pontos, mm |
| `min_range_m`, `max_range_m` | inteiro ≥ 0 | `--minRangeClipping`, `--maxRangeClipping` | Somente inteiros (o DeCap trunca decimais); unidade não documentada |
| `unify` | bool | `--unify` | Ver limitações: sem saída nos testes |
| `normalize_intensity` | bool | `--normalizeIntensity 0\|1` | |
| `input_unit` | `mm`, `cm`, `m`, `in`, `ft`, `us_ft` | `--inputUnitType 0..5` | Padrão do DeCap: metro |
| `current_coordinate_system` | código | `--currentCoordinateSystem` | Código da biblioteca de sistemas de coordenadas da Autodesk, ex.: `SIRGAS2000.UTM-23S` |
| `target_coordinate_system` | código | `--targetCoordinateSystem` | Exige `current_coordinate_system` |
| `e57_enable_image_import`, `e57_create_scan_per_image`, `e57_match_image_resolution`, `e57_level_image` | bool | `--e57...` | Exigem ao menos uma entrada `.e57` |
| `dry_run` | bool | | Só valida e devolve o comando, sem executar |

Antes de iniciar, o servidor valida caminhos, formatos, opções (contra o `--help` instalado) e o destino. Se `<nome>.rcp` ou `<nome> Support` já existirem, recusa com `destination_exists`. Listas longas vão por `--controlFile` automaticamente.

Formatos de entrada: verificados nos testes `.pts`, `.xyz`, `.e57`; aceitos, mas não verificados, `.asc .txt .las .laz .ptx .ptg .fls .fws .zfs .zfprj .rds .rsp .xyb .cl3 .clr .pcg .isproj .lsproj` (geram aviso em `warnings`).

Exemplo:

```json
{"name": "import_scans", "arguments": {
  "inputs": ["D:\\Scans\\Estação 01.e57", "D:\\Scans\\Estação 02.e57"],
  "output_folder": "D:\\Projetos ReCap\\Galpão",
  "project_name": "Galpao_importado",
  "decimation_mm": 5,
  "current_coordinate_system": "SIRGAS2000.UTM-23S"
}}
```

Resposta: `job_id`, `status: "running"`, `pid`, `command` (lista de argumentos), `log_file` e `output.expected_rcp`.

## get_job_status

| Parâmetro | Tipo | Padrão |
|---|---|---|
| `job_id` | texto | obrigatório |
| `log_lines` | 0 a 200 | 40 |

Estados: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `interrupted`. Campos: `created_at`, `started_at`, `finished_at`, `elapsed_seconds`, `exit_code` e `exit_code_description` (ex.: `0xC0000005 (violação de acesso)`), `log_tail` (no máximo 200 linhas/64 KB), `progress_by_scan` (enquanto ativo), `failure_reasons` e `verification`:

| Campo de `verification` | Significado |
|---|---|
| `exit_code_zero` | Código de saída 0 (não basta para sucesso) |
| `decap_errors`, `failed_scan_indexes`, `free_text_warnings` | Mensagens `[E]`, `[P][n Failed]` e avisos em texto livre do DeCap |
| `rcp_exists`, `manifest_readable`, `scan_count`, `total_points`, `project_bounds` | O projeto existe, é legível e tem scans |
| `rcs_files`, `missing_rcs_count` | `.rcs` existentes e com número de pontos igual ao manifesto |
| `coordinate_system_requested`, `coordinate_system_applied` | O sistema pedido foi gravado no projeto |

## list_jobs

`status` (filtro opcional) e `limit` (1 a 500, padrão 50). Inclui jobs de execuções anteriores do servidor; `controlled_by_this_server` é `true` apenas enquanto o job está ativo e o processo pertence a esta instância (jobs encerrados mostram `false`).

## cancel_job

Encerra (TerminateProcess) **somente** o PID iniciado por esta instância do servidor para aquele job. Recusa jobs encerrados ou `interrupted`. Arquivos parciais não são apagados; `output_now` mostra o que ficou no destino.

## open_project_in_recap

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `path` | texto | obrigatório | `.rcp` existente e válido |
| `wait_for_load_seconds` | 0 a 300 | 0 | Aguarda o registro `Project loading success` no `AutodeskReCap.log` |
| `allow_second_instance` | bool | `false` | Inicia o ReCap mesmo com outra instância aberta |

Se o ReCap já estiver aberto, a ferramenta recusa com `recap_already_running` (e lista os PIDs): nos testes, uma segunda instância não carregou o projeto em 240 s. Abra o projeto pelo ReCap aberto ou feche-o antes.

Executa `ReCap.exe "<projeto>"`, o mesmo formato da associação de arquivos do Windows. Com `wait_for_load_seconds > 0`, `load_confirmed` vale `true`/`false` conforme o log do ReCap; com 0, `null`. A inicialização do ReCap levou cerca de 100 s nos testes, então use 150 ou mais para confirmar.
