# Formato `.rcs` (ADOCT 3.2): o que este servidor lê

A Autodesk não documenta o `.rcs`. A estrutura abaixo foi levantada em 2026-11-10 com nuvens sintéticas de conteúdo conhecido, convertidas pelo DeCap 27.0.1.242, e depois conferida em nuvens reais. O arquivo não é comprimido nem cifrado. Tudo é little-endian.

## Como foi conferido

| Verificação | Resultado |
|---|---|
| Nuvem sintética de 200 mil pontos (PTS → DeCap → `.rcs` → leitura) | 196 386 pontos gravados; todos coincidem com um ponto de origem a menos de 1 mm; RGB idêntico em 100% |
| Nuvem real unificada, 29 GB, 1,26 bilhão de pontos | tabela de folhas e blocos conferem; 4,9 milhões de pontos sorteados, nenhum fora dos limites da sua folha |
| Projeto real com 98 scans, 4,1 bilhões de pontos | idem; scans girados alinham com os não girados (distância mediana de 1,5 cm) |

O DeCap grava menos pontos que a origem quando há pontos muito próximos (196 386 de 200 000 no teste). O contador do cabeçalho traz o número da origem, não o gravado.

## Cabeçalho

| Deslocamento | Tipo | Conteúdo |
|---|---|---|
| 0x00 | 5 bytes | `ADOCT` |
| 0x08 | 2 × u32 | versão (3, 2) |
| 0x10 | 3 × f64 | translação do scan |
| 0x28 | 3 × f64 | rotação (graus) |
| 0x40 | 3 × f64 | escala |
| 0x70 | 6 × f64 | limites do octree (mín., máx.), coordenadas locais |
| 0xA0 | 6 × f64 | limites dos dados (mín., máx.), coordenadas locais |
| 0xD8 | u64 | número de pontos da origem |
| 0xE3 | texto | identificador `{...}` |
| 0x129 | u32 | número de seções |
| 0x12D | n × (u64, u64, u64) | tabela de seções: tipo, deslocamento, tamanho |

Seções encontradas: 1 (pontos), 2 (folhas), 3 (nós superiores do octree, com cor média), 11 (metadados). Arquivos unificados trazem também 6 e 7, não interpretadas.

## Seção 2: folhas

Um `u64` com a quantidade, seguido de registros de 368 bytes:

| Campo | Tipo | Conteúdo |
|---|---|---|
| `mn`, `mx` | 2 × 3 f64 | cubo da folha, coordenadas locais (m) |
| `dmn`, `dmx` | 2 × 3 f64 | limites dos pontos da folha |
| `levels` | u32 | níveis do octree interno |
| `np` | u32 | pontos gravados na folha |
| `size` | u32 | tamanho do bloco de dados, em bytes |
| `inner` | 32 × u32 | nós internos por nível |
| `leaf` | 33 × u32 | nós-folha por nível (a soma é `np`) |

Vale sempre `size = 4 × (soma de inner + soma de leaf) + 16 × np`. O leitor recusa o arquivo se isso não fechar.

## Seção 1: pontos

Os blocos das folhas ficam em sequência, na ordem da seção 2. Cada bloco tem os nós do octree interno (4 bytes cada) e depois os pontos (16 bytes cada):

| Bits / bytes | Conteúdo |
|---|---|
| bits 0-17, 18-35, 36-53 do primeiro `u64` | X, Y, Z em milímetros a partir de `mn` da folha |
| byte 8 | intensidade (0 a 255) |
| bytes 9, 10, 11 | B, G, R |
| bytes 12-15 | não interpretado |

Coordenada no projeto: `R · (mn + deslocamento) + translação`, com a translação e a rotação do nó do scan no `.rcp`.

Os pontos de cada folha estão embaralhados: os primeiros *k* pontos cobrem a folha tão bem quanto uma amostra de 1 em *n*. O servidor usa isso como nível de detalhe e lê só o começo de cada bloco.

## Rotação no `.rcp`

`<Rotation x y z>` está em graus e a matriz usada é `Rz · Ry · Rx`. O giro em Z (anti-horário visto de cima) foi conferido: 1,5 cm de distância mediana entre scans girados e não girados, contra 26 cm com o sinal trocado. Os giros em X e Y dos dados disponíveis são menores que 0,1°, e a ordem adotada venceu as alternativas por margem pequena.

## O que não é lido

* Normais, classificação, regiões e recortes (limit box) do projeto.
* Imagens panorâmicas (RealView): os `.rcs` de panorama não têm folhas e são ignorados.
* `<GlobalTransformation>` do `.rcp` diferente da identidade e escala diferente de 1 geram um aviso e não são aplicadas.
* Versões diferentes de 3.x não foram testadas. Se as contas da tabela de folhas não fecharem, o arquivo é recusado.
