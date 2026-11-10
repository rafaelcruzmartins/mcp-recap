# Leitura de pontos: ferramentas, limites e exemplos

## De onde vêm os pontos

O Reality Solutions SDK da Autodesk é o único caminho documentado para ler os pontos de `.rcs`/`.rcp`. Ele é distribuído pela ADN e não acompanha o ReCap; as DLLs que vêm nos produtos Autodesk não têm headers nem licença de uso avulso. Por isso este servidor segue o **Plano B**: lê os **arquivos de origem** dos scans.

| Entrada (`source`) | O que acontece |
|---|---|
| `.e57` | Lido por blocos com libE57 (pye57). A pose de cada scan é aplicada: coordenadas no sistema do arquivo E57, em metros |
| `.las` / `.laz` | Lido por blocos com laspy/lazrs. Se o arquivo declara CRS, as coordenadas são georreferenciadas |
| `.pts` / `.xyz` / `.txt` / `.asc` | Texto com 3, 4, 6 ou 7 colunas (XYZ, I, RGB). A unidade não é declarada: use `input_unit` |
| `.rcp` | Usa os arquivos gravados em `rawScanPath` de cada scan. Se algum estiver ausente, recusa e lista os faltantes |
| `.rcs` | Recusado: exige o SDK |

Formatos não suportados no Plano B: FARO `.fls`/`.fws`, Leica `.ptg`/`.ptx` (este é texto, mas com cabeçalho próprio; não testado), Z+F `.zfs` e qualquer projeto cujo arquivo de origem tenha sido apagado ou movido.

## Coordenadas

Toda resposta traz `coordinate_frame`:

```json
{"coordinates": "arquivo de origem", "frames": ["arquivo E57 (sistema do arquivo, após aplicar a pose de cada scan)"],
 "unit": "m", "georeferenced": null, "scan_local": false,
 "matches_recap_project_bounds": true, "note": "Os limites lidos coincidem com os do projeto ReCap; ..."}
```

* As coordenadas nunca são locais do scan (`scan_local: false`): a pose do E57 é sempre aplicada.
* Com `source` = `.rcp`, o servidor compara os limites lidos com os do projeto ReCap. Se coincidirem (tolerância de 1 cm ou 0,01 % da extensão), as coordenadas equivalem às do projeto. Se não coincidirem, o scan foi registrado ou movido no ReCap, e as coordenadas são as do arquivo de origem.
* `point_cloud_info` mostra a pose scan→arquivo de cada scan E57 (quatérnio e translação).

## Índice (cache)

Os formatos de origem não têm índice espacial. Na primeira consulta, a nuvem é lida uma vez e gravada em blocos XY em `%LOCALAPPDATA%\mcp-recap\cache\<chave>` (19 bytes por ponto). A chave muda se o arquivo de origem mudar. Os originais nunca são alterados.

* Até 3 milhões de pontos, a indexação é feita na própria consulta.
* Acima disso, a consulta responde `cache_required`: rode `index_point_cloud`, que vira job com progresso e cancelamento (`get_job_status`, `cancel_job`).
* Medido nesta máquina: LAZ com 20 milhões de pontos indexado em 9,6 s, pico de 332 MB de memória e 380 MB de cache.

## Limites

| Limite | Padrão | Variável |
|---|---|---|
| Amostra devolvida | 2.000 pontos (máx. 10.000) | parâmetro `sample_size` |
| Pontos lidos por consulta | 10 milhões; acima, lê 1 a cada N | `RECAP_MCP_MAX_QUERY_POINTS` |
| Pontos por imagem | 4 milhões | `RECAP_MCP_RENDER_MAX_POINTS` |
| Tempo por consulta | 120 s | `RECAP_MCP_QUERY_TIMEOUT_S` |
| Indexação imediata | 3 milhões de pontos | `RECAP_MCP_SYNC_INDEX_POINTS` |
| Exportação `.e57` | 50 milhões de pontos | `RECAP_MCP_MAX_E57_EXPORT_POINTS` |
| Imagem | até 4096 px por lado | parâmetro `width_px` |

Quando há amostragem, a resposta traz `sampling` e `count_is_exact: false`. A contagem passa a ser estimada: blocos totalmente dentro da região contam exato, e os da borda são extrapolados. A precisão das coordenadas no cache é melhor que 0,1 mm em blocos de até 1 km.

## Ferramentas

Todas aceitam `source` e, para texto, `input_unit` (`m`, `mm`, `cm`, `in`, `ft`, `us_ft`). Caixas são `min`/`max` = `[x, y, z]`; sem caixa, vale a nuvem inteira.

| Ferramenta | Parâmetros principais | Retorno |
|---|---|---|
| `point_cloud_info` | — | totais, limites, atributos, scans e poses, estado do índice, `project_context` |
| `index_point_cloud` | `tile_size_m` (opcional) | resumo do índice ou `job_id` |
| `query_box` | `min`, `max`, `sample_size` | `points_in_region`, `actual_bounds`, `z_stats` (mín, máx, média, desvio, p05/p50/p95), `sample` (`columns` + `rows`) |
| `z_histogram` | `min`, `max`, `bin_size_m` (0,05) | `bins` e `peaks` (`z_center`, `z_estimate`, `count`) |
| `section` | `mode` = `horizontal` (`z`, `thickness_m`) ou `vertical` (`p1`, `p2`, `thickness_m`); `min`/`max` limitam a extensão | pontos 2D (`x,y` ou `s,z`, com `s` = distância a partir de `p1`) + PNG |
| `render_view` | `view` (`top`, `bottom`, `front`, `back`, `left`, `right`, `custom` + `direction`), `color_by`, `width_px`, `point_size`, `min`/`max` | PNG + `view.axes` e, em `custom`, `screen_basis` |
| `profile` | `polyline` (2 a 500 vértices), `width_m`, `step_m`, `zmin`/`zmax` | `stations` (Z mín/mediano/máx por estaca), pontos 2D (`chainage, z`) + PNG |
| `nearest_points` | `point`, `k` (≤ 100), `max_radius_m` (≤ 100) | vizinhos com distância |
| `measure` | `p1`, `p2`, `snap`, `snap_radius_m` | `distance_3d`, `distance_horizontal`, `delta`, pontos encaixados |
| `export_subset` | `output_folder`, `file_name`, `format`, caixa ou `section_mode` + `z`/`p1`/`p2` + `thickness_m` | `job_id`; o arquivo é gravado como `.parcial` e renomeado no fim |

As imagens voltam como conteúdo de imagem MCP (o agente pode vê-las) e também são salvas em `%LOCALAPPDATA%\mcp-recap\imagens`.

### Exemplos

Achar os pisos e cortar 1 m acima do primeiro:

```json
{"name": "z_histogram", "arguments": {"source": "D:\\Scans\\Galpao.rcp", "bin_size_m": 0.05}}
{"name": "section", "arguments": {"source": "D:\\Scans\\Galpao.rcp", "mode": "horizontal", "z": 1.0, "thickness_m": 0.05}}
```

Vista de topo colorida por elevação de uma região:

```json
{"name": "render_view", "arguments": {"source": "D:\\Scans\\Galpao.e57", "view": "top", "color_by": "elevation",
 "min": [100, 200, -5], "max": [160, 260, 30], "width_px": 1600}}
```

Exportar uma faixa vertical para `.pts`:

```json
{"name": "export_subset", "arguments": {"source": "D:\\Scans\\Galpao.e57", "output_folder": "D:\\Recortes",
 "file_name": "corte_A", "format": "pts", "section_mode": "vertical", "p1": [0, 10], "p2": [50, 10], "thickness_m": 0.2}}
```

## Verificação feita

* Projeto "Teste MCP.rcp", gerado pelo DeCap 27.0.1.242 a partir de uma grade de 900 pontos (2,9 × 2,9 m): 900 pontos lidos pela origem, limites iguais aos do projeto e `matches_recap_project_bounds: true`.
* Sala sintética em E57 com dois scans (o segundo com rotação de 90° e translação): limites 0–6 × 0–4 × 0–3 m reconstruídos; picos do histograma em 0,0002 e 2,9998 m; parede a parede 5,998 m; pé-direito 2,993 m; imagens de topo, frente e cortes conferidas visualmente.
* LAZ sintético de 20 milhões de pontos (400 × 300 m): indexação em 9,6 s; caixa de 20 × 20 m em 0,12 s; histograma completo em 1,3 s; corte de 400 m em 0,4 s; perfil em 1,2 s; vista de topo inteira em 3,0 s (amostrada 1:5).
