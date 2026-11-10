# Limitações, decisões e comportamentos observados

## Versões testadas

| Item | Versão |
|---|---|
| Autodesk ReCap Pro | 2027 (27.0.1.242) |
| DeCap.exe | 27.0.1.242 |
| Windows | 11 Pro for Workstations |
| Python | 3.12.14 (testes unitários também em 3.11) |
| SDK `mcp` (Python) | 1.30.0 |

Outras versões do ReCap devem funcionar enquanto o DeCap mantiver `--importWithLicense`. As opções são liberadas conforme o `--help` instalado.

## O que este servidor não promete

* **Leitura de pontos de `.rcs`**: o `.rcs` é um octree binário proprietário. Só o cabeçalho é lido (assinatura `ADOCT`, número de pontos, identificador, translação e limites locais). Esses campos foram conferidos contra o manifesto `.rcp` em projetos gerados pelo DeCap 27.0.1.242, mas o formato não é documentado pela Autodesk.
* **Escrita de `.rcp`/`.rcs`**: nenhuma; projetos só são criados pelo DeCap.
* **Reality Solutions SDK**: a [documentação oficial](https://help.autodesk.com/cloudhelp/ENU/Reality-Capture/files/Product_Overview/recap_sdk.html) informa que ele lê e grava RCP/RCS, mas é obtido pela Autodesk Developer Network (ADN Standard/Professional) e não acompanha o ReCap. Não estava disponível nesta máquina; por isso não há integração.
* **Controle da interface**: o DeCap cria projetos; não registra scans, não mede, não exporta e não manipula a interface. `open_project_in_recap` apenas inicia o ReCap com o arquivo.
* **Reality Capture API na nuvem**: não é usada.
* **Licença**: o servidor não ativa nem contorna licenças. `--checkLicense` é a única verificação disponível.

## Comportamentos do DeCap 27.0.1.242 observados nos testes

| Observação | Consequência no servidor |
|---|---|
| Código de saída 0 mesmo com `[E][1025][File ... can not be found]`, `[P][1 Failed]`, CS inválido ou sem projeto gerado | Sucesso decidido por verificação do resultado, nunca só pelo código de saída |
| `--minRangeClipping 0.3` vira `0` (mensagem `Setting the min clipping range to 0`) | Recorte aceita só inteiros; decimais são recusados com erro claro |
| `--decimation` e o recorte são aceitos (`Setting decimation to 200 mm`), mas **não alteraram a contagem de pontos** em nuvens não estruturadas (PTS e E57 sem grade) | Opções expostas; efeito não pôde ser medido com dados sintéticos. Validar com scans estruturados reais |
| `--unify` com duas entradas terminou sem gerar `.rcp` (código 0 com PTS; `0xC0000005` com E57 + PTS) | Opção exposta, com aviso; o job é marcado `failed` quando não há saída |
| `--inputUnitType 0` (mm) reduz a nuvem 1000× em relação a metro | Efeito verificado no teste de integração |
| `UnitType` gravado no `.rcp` é igual para importações em mm e em m | `unit_type_raw` é devolvido como código bruto da unidade de exibição, sem interpretação |
| CS válido (ex.: `SIRGAS2000.UTM-23S`) é gravado em `<CoordinateSystem>`; inválido gera só o aviso `Current coordinate system not valid` | O servidor compara o CS pedido com o gravado e marca `failed` se diferirem |
| `--currentCoordinateSystem UTM84-23S --targetCoordinateSystem SIRGAS2000.UTM-23S` com coordenadas locais pequenas falhou a conversão (`[P][1 Failed]`, projeto sem scans) | Detectado como falha; conversões exigem coordenadas coerentes com o sistema de origem |
| O arquivo de `--controlFile` é lido na página de código ANSI (UTF-8 com acentos falha) | Arquivo gravado em ANSI; caminhos fora do ANSI fazem a importação longa ser recusada com orientação |
| Caminhos com caracteres fora do ANSI (ex.: chinês) funcionam na linha de comando | Argumentos passados diretamente sempre que possível |
| O `.rcp` grava o caminho absoluto de cada `.rcs` (`Path`), em UTF-8 correto, e deixa `RelativePath` vazio | Se o projeto for movido, `list_scans` acha o `.rcs` pela pasta `<projeto> Support` (`resolved_by: "pasta Support"`) |
| O DeCap escreve no console em ANSI | Logs decodificados como UTF-8 ou, se falhar, ANSI |

## Comportamento do ReCap (interface)

* `ReCap.exe "<projeto.rcp>"` abre o projeto; a inicialização levou cerca de 100 s.
* Iniciado com a flag `DETACHED_PROCESS`, o ReCap encerra antes de criar a janela; a flag não é usada.
* O carregamento é confirmado pelo `AutodeskReCap.log` (`[POINTCLOUD] Project loading success`).
* Com outra instância do ReCap aberta, uma nova instância iniciada com o arquivo não carregou o projeto em 240 s. Por isso `open_project_in_recap` recusa por padrão quando o ReCap já está aberto.

## Decisões de projeto

* **Python + SDK oficial `mcp` (FastMCP), transporte STDIO, ambiente `uv` com `uv.lock`**: reprodutível e igual ao fluxo do `mcp-revit-2026` (que usa `uv`), sem depender de pyRevit, que não se aplica ao ReCap.
* **Somente `--importWithLicense`**: aproveita a licença ativa; o modo com token exigiria credenciais.
* **Opções liberadas pelo `--help`**: nenhuma opção é montada se a versão instalada não a anunciar.
* **Destino sempre novo**: o servidor nunca sobrescreve nem apaga projetos. Arquivos parciais de jobs cancelados ou com falha ficam no destino para inspeção.
* **Subprocessos com lista de argumentos, sem `shell=True`**: nenhuma ferramenta executa shell ou código arbitrário.
* **Leitura do `.rcp` em memória**: o ZIP nunca é extraído para disco (sem risco de *path traversal*); limites de 512 MB por arquivo, 2000 entradas, 32 MB de manifesto e razão de compressão; DTD e entidades XML são recusadas.
* **Nenhuma enumeração de pontos**: respostas trazem apenas metadados e contagens.
* **Jobs**: até 2 importações simultâneas (`RECAP_MCP_MAX_JOBS`). Ao encerrar normalmente, o servidor termina os processos DeCap que iniciou (status `cancelled`). Se o servidor cair, os jobs ativos são marcados `interrupted` no próximo início; o processo pode ter seguido sem supervisão e não é encerrado nem cancelável.
* **Ferramentas síncronas em thread**: chamadas lentas (licença, abertura do ReCap) não bloqueiam outras requisições.

## Referências usadas

* [DeCap (documentação oficial)](https://help.autodesk.com/cloudhelp/ENU/Reality-Capture/files/Creating_Scan_Projects/decap.html)
* [Reality Solutions SDK](https://help.autodesk.com/cloudhelp/ENU/Reality-Capture/files/Product_Overview/recap_sdk.html)
* [rafaelcruzmartins/mcp-revit-2026](https://github.com/rafaelcruzmartins/mcp-revit-2026): organização dos tutoriais por cliente.
* [xuantinhnbs-rgb/recap-mcp](https://github.com/xuantinhnbs-rgb/recap-mcp) (MIT): consultado para entender o formato do `.rcp` e o cabeçalho do `.rcs`. O código deste repositório é implementação própria; nenhum trecho foi copiado. Os campos usados foram reconferidos em arquivos gerados nesta máquina.
