# GlobalScore API

Backend do MVP GlobalScore, responsável por persistir dados, construir Bases de
Referência congeladas e calcular avaliações ponderadas.

## Tecnologias

- Python 3.11
- Flask e Flask-RESTX
- Flask-SQLAlchemy
- SQLite
- Swagger
- openpyxl, somente para leitura de XLSX
- Supabase Auth como API externa de autenticação

## Execução local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python executar.py
```

A API ficará disponível em `http://localhost:5000` e o Swagger em
`http://localhost:5000/docs`.

Antes de iniciar a API, use `backend/.env.example` como referência e defina no
ambiente `SUPABASE_URL` e `SUPABASE_PUBLIC_KEY`. O arquivo `.env` real não deve ser
versionado. A aplicação lê variáveis do processo; no Docker, elas podem ser
fornecidas com `--env-file`.

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
- `GET /autenticacao/me`
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
- `POST /importacoes` (multipart com `projeto_id` e `arquivo`)
- `GET /importacoes/{id}`
- `POST /importacoes/{id}/validar`
- `POST /importacoes/{id}/confirmar`

O `DELETE` de Indicadores é lógico: o registro é desativado para preservar o
histórico já calculado.

As listagens aceitam filtros por parâmetros de consulta:

- `/grupos?projeto_id=1`;
- `/entidades?projeto_id=1&grupo_id=1`;
- `/observacoes?projeto_id=1&entidade_id=1&indicador_id=1&periodo=2026-01`.

Entidades também são desativadas de forma lógica enviando `{"ativa": false}` em
`PATCH /entidades/{id}`.

## Importação assistida

O MVP aceita apenas CSV e XLSX, com limite de 10 MB. A importação possui três
etapas e nunca interpreta silenciosamente o significado das colunas:

1. `POST /importacoes` guarda o arquivo temporariamente e devolve preview, abas e
   sugestão de delimitador quando aplicável;
2. `POST /importacoes/{id}/validar` recebe a região escolhida, formato numérico,
   formato do período e mapeamento explícito, realizando um dry-run;
3. `POST /importacoes/{id}/confirmar` grava o lote validado em uma única transação.

O dry-run separa erros bloqueantes de alertas de qualidade. Valores extremos são
identificados pelos limites de 3 IQR, tanto dentro do lote quanto no histórico do
mesmo indicador e grupo, quando existem ao menos cinco valores e IQR diferente de
zero. Um lote `VALIDADA_COM_ALERTAS` exige a confirmação explícita
`{"confirmar_alertas": true}`; os valores aceitos são preservados sem alteração.

Para auditoria, `GET /importacoes/{id}/observacoes` lista as observações criadas
pelo lote. `POST /importacoes/{id}/anular` cancela um lote ainda não concluído ou
remove atomicamente as observações de um lote concluído sem uso. Se uma Base de
Referência ou Avaliação materializada depender dele, a API responde HTTP 409 e
preserva os dados.

Exemplo mínimo de configuração e mapeamento para CSV largo:

```json
{
  "configuracao_leitura": {
    "linha_inicial": 2,
    "linhas_cabecalho": [1],
    "colunas_utilizadas": [1, 2, 3],
    "delimitador": ";",
    "formato_periodo": "MM/AAAA",
    "formato_numerico": {
      "separador_decimal": ",",
      "separador_milhar": "."
    }
  },
  "mapeamento": {
    "formato": "LARGO",
    "colunas": [
      {"indice_coluna": 1, "papel": "CODIGO_ENTIDADE"},
      {"indice_coluna": 2, "papel": "PERIODO"},
      {
        "indice_coluna": 3,
        "papel": "VALOR_INDICADOR",
        "indicador": {"acao": "ASSOCIAR", "indicador_id": 1}
      }
    ]
  }
}
```

Células vazias não geram observações. Duplicidades são relatadas no dry-run e
impedem a confirmação. O arquivo temporário é removido depois da conclusão.

## Autenticação externa com Supabase

O Supabase Auth foi escolhido como o terceiro componente exigido pela pós: uma
API externa pública que possui [plano gratuito](https://supabase.com/docs/guides/platform/billing-on-supabase).
No MVP, ele é usado somente para autenticação por e-mail e senha. O banco de dados
do GlobalScore continua sendo SQLite.

```text
Frontend futuro
  → login por e-mail/senha no Supabase Auth
  → recebe access token
  → envia Authorization: Bearer <token> para o Flask
  → Flask consulta GET /auth/v1/user no Supabase
  → Flask usa id e e-mail do usuário autenticado
```

A validação segue o endpoint de usuário documentado pelo Supabase em
[JSON Web Tokens](https://supabase.com/docs/guides/auth/jwts). A configuração de
e-mail/senha está na documentação de
[Password-based Auth](https://supabase.com/docs/guides/auth/passwords).

Variáveis necessárias:

```text
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_PUBLIC_KEY=sua-chave-publica
```

Use somente a chave pública `publishable` ou `anon` adequada ao projeto. Nunca use
`service_role` no frontend ou no repositório. O GlobalScore não recebe nem armazena
senhas. Ausência ou rejeição do token retorna HTTP 401; indisponibilidade do
Supabase retorna HTTP 503.

O Swagger permanece público em `/docs` e permite informar o Bearer token no botão
**Authorize**. A rota `/sistema/saude` também é pública. Nesta etapa, somente as
rotas `/importacoes` e `GET /autenticacao/me` exigem autenticação. A identidade
validada pelo Supabase preenche `criado_por` nos novos lotes.
