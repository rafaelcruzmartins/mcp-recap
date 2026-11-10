# CI sugerido (GitHub Actions)

O workflow não foi incluído no PR porque a conta que o publicou não tem o escopo `workflow` no GitHub. Para ativá-lo, crie `.github/workflows/ci.yml` com o conteúdo abaixo.

Ele roda no Windows, em Python 3.11, 3.12 e 3.13, apenas os testes que não dependem do ReCap (o DeCap é simulado por `tests/fake_decap.py`). Os testes de integração ficam de fora (`-m "not integration"`) porque exigem ReCap Pro licenciado.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  testes:
    runs-on: windows-latest
    strategy:
      matrix:
        python: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - name: Instalar dependências (uv.lock)
        run: uv sync --locked --python ${{ matrix.python }}
      - name: Lint
        run: uv run --python ${{ matrix.python }} ruff check src tests
      - name: Testes (sem ReCap)
        run: uv run --python ${{ matrix.python }} pytest -m "not integration" -q
```

Situação: este workflow ainda não rodou no GitHub. Os mesmos comandos de lint e de testes sem ReCap foram executados localmente em Python 3.11 e 3.12.
