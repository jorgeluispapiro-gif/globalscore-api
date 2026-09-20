# GlobalScore API — Etapa 2A

Backend do MVP GlobalScore, responsável por persistir dados, construir Bases de
Referência congeladas e calcular avaliações ponderadas.

## Tecnologias

- Python 3.11
- Flask e Flask-RESTX
- Flask-SQLAlchemy
- SQLite
- Swagger

## Execução local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python executar.py
```

A API ficará disponível em `http://localhost:5000` e o Swagger em
`http://localhost:5000/docs`.

## Testes essenciais

```bash
pytest -q
```

## Execução com Docker

```bash
docker build -t globalscore-api .
docker run --rm -p 5000:5000 -v globalscore_dados:/app/dados globalscore-api
```

## Rotas iniciais

- `GET /sistema/saude`
- `GET|POST /projetos`
- `GET|PATCH /projetos/{id}`
- `GET|POST /grupos`
- `GET|PATCH /grupos/{id}`
- `GET|POST /entidades`
- `GET|PATCH /entidades/{id}`
- `GET|POST /indicadores`
- `GET|PATCH|DELETE /indicadores/{id}`
- `GET|POST /observacoes`
- `GET|POST /bases`
- `GET /bases/{id}`
- `POST /bases/{id}/processar`
- `PATCH /bases/{id}/pesos`
- `POST /bases/{id}/ativar`
- `POST /avaliacoes`
- `GET /avaliacoes/{id}`

O `DELETE` de Indicadores é lógico: o registro é desativado para preservar o
histórico já calculado.

As listagens aceitam filtros por parâmetros de consulta:

- `/grupos?projeto_id=1`;
- `/entidades?projeto_id=1&grupo_id=1`;
- `/observacoes?projeto_id=1&entidade_id=1&indicador_id=1&periodo=2026-01`.

Entidades também são desativadas de forma lógica enviando `{"ativa": false}` em
`PATCH /entidades/{id}`.
