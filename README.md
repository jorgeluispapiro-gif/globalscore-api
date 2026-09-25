# GlobalScore API

> **MEASURE | COMPARE | ADVANCE**  
> Backend RESTful do MVP GlobalScore para persistência de dados, importação assistida, construção de Bases de Referência, avaliações percentílicas ponderadas, analytics comparativos e eventos gerenciais.

---

## 1. Visão geral

A **GlobalScore API** concentra as regras de negócio e a persistência do GlobalScore. O backend é a fonte de verdade para percentis, pesos e Global Score; o frontend apenas consome e apresenta os resultados calculados.

Principais responsabilidades:

1. **Domínio**: projetos, grupos comparáveis, entidades, indicadores e observações.
2. **Importação assistida incremental**: CSV/XLSX, preview, mapeamento explícito, dry-run, alertas de qualidade e confirmação transacional.
3. **Bases de Referência**: definição da população comparável, cobertura mínima, congelamento das estatísticas e régua percentílica completa de **P0 a P100**.
4. **Avaliações**: cálculo individual e em lote, preservando a Base de Referência utilizada.
5. **Analytics**: visão geral do grupo, ranking quando aplicável, detalhe da entidade, evolução temporal e fatos objetivos do período.
6. **Eventos gerenciais**: registro de fatos contextuais associados a entidade e período, sem inferência causal sobre o desempenho.
7. **Autenticação**: validação de Bearer token emitido pelo Supabase Auth.

O **Global Score permanece na escala de 0 a 100**. Cada indicador recebe pontuação percentílica entre 0 e 100 e a API calcula a soma ponderada com pesos que totalizam 100%.

---

## 2. Tecnologias

- Python 3.11
- Flask 3.x
- Flask-RESTX / Swagger UI
- Flask-SQLAlchemy
- SQLite
- openpyxl para leitura de XLSX
- biblioteca `csv` da própria linguagem
- Supabase Auth como serviço externo de autenticação
- Docker
- Pytest

---

## 3. Estrutura do repositório

```text
.
├── Dockerfile
├── .dockerignore
├── README.md
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── modelos/
│   │   ├── servicos/
│   │   └── configuracao.py
│   ├── dados/
│   ├── tests/
│   ├── executar.py
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── .env.example
└── docs/
```

---

## 4. Configuração local

### Pré-requisitos

- Python 3.10 ou 3.11
- Docker, opcionalmente, para execução em container

### Ambiente virtual

A partir da raiz do repositório:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
```

### Variáveis de ambiente

Use `backend/.env.example` como referência:

```dotenv
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_PUBLIC_KEY=sua-chave-publica
SUPABASE_AUTH_TIMEOUT_SEGUNDOS=5
```

O código usa `os.getenv()` e **não carrega automaticamente um arquivo `.env`**. Para execução local sem Docker, exporte as variáveis para o processo:

```bash
export SUPABASE_URL="https://seu-projeto.supabase.co"
export SUPABASE_PUBLIC_KEY="sua-chave-publica"
export SUPABASE_AUTH_TIMEOUT_SEGUNDOS="5"
```

Use somente chave pública adequada ao projeto (`publishable` / `anon`). Não use `service_role` no frontend ou no repositório.

---

## 5. Execução local

```bash
cd backend
python executar.py
```

A API fica disponível em:

- `http://localhost:5000`
- Swagger UI: `http://localhost:5000/docs`

---

## 6. Testes

Da raiz do repositório:

```bash
pytest backend/tests -q
```

De dentro de `backend/`:

```bash
pytest tests -q
```

Validação realizada no fechamento acadêmico:

**57 testes automatizados passando.**

A suíte cobre os fluxos críticos de importação, Bases de Referência, avaliações, analytics, eventos e autenticação.

---

## 7. Docker

O `Dockerfile` está na raiz do repositório.

### Build

```bash
docker build -t globalscore-api .
```

### Execução

Crie um arquivo local `backend/.env` com as variáveis de autenticação e execute:

```bash
docker run --rm \
  -p 5000:5000 \
  --env-file backend/.env \
  globalscore-api
```

Para preservar o SQLite entre reinicializações:

```bash
docker run --rm \
  -p 5000:5000 \
  -v globalscore_dados:/app/dados \
  --env-file backend/.env \
  globalscore-api
```

Em uma rede Docker compartilhada com o frontend:

```bash
docker network create globalscore-net

docker run --rm \
  --network globalscore-net \
  --name globalscore-api \
  -p 5000:5000 \
  -v globalscore_dados:/app/dados \
  --env-file backend/.env \
  globalscore-api
```

---

## 8. Swagger / OpenAPI

A documentação interativa gerada pelo Flask-RESTX fica em:

**`http://localhost:5000/docs`**

A interface permite:

- visualizar namespaces e endpoints;
- consultar parâmetros, modelos de entrada e respostas documentadas;
- testar operações expostas pelo Swagger;
- informar `Bearer <access_token>` no botão **Authorize** para rotas protegidas.

---

## 9. Autenticação e serviço externo

O serviço externo utilizado é o **Supabase Auth**.

Fluxo:

```text
Frontend
  → autentica e-mail/senha no Supabase Auth
  → recebe access_token
  → chama a GlobalScore API com Authorization: Bearer <token>
  → backend valida o token no Supabase Auth
  → backend processa a requisição autorizada
```

Na validação, o backend consulta o endpoint de usuário do Supabase (`/auth/v1/user`). Token ausente ou rejeitado produz HTTP 401; indisponibilidade do serviço externo pode produzir HTTP 503.

O **SQLite continua sendo o banco de domínio do GlobalScore**. O Supabase é usado para autenticação, não como banco principal da aplicação.

### Cadastro, plano e condições de uso

- cadastro do serviço: [Supabase Dashboard](https://supabase.com/dashboard/sign-up);
- documentação de autenticação por senha: [Supabase Auth — Password-based authentication](https://supabase.com/docs/guides/auth/passwords);
- plano utilizado no MVP: [Free Plan do Supabase](https://supabase.com/docs/guides/platform/billing-on-supabase), sujeito aos limites publicados pelo fornecedor;
- serviço hospedado sujeito aos [termos do Supabase](https://supabase.com/terms);
- cliente JavaScript usado pelo frontend distribuído sob [licença MIT](https://github.com/supabase/supabase-js/blob/master/LICENSE).

As operações externas efetivamente usadas são:

- `POST /auth/v1/token?grant_type=password`, acionado pelo cliente Supabase no login;
- `GET /auth/v1/user`, consultado pelo backend para validar o Bearer token.

O GlobalScore utiliza da resposta apenas os dados necessários à sessão, como
identificador e e-mail do usuário. Dados de projetos, indicadores, observações e
avaliações não são persistidos no Supabase.

---

## 10. Principais endpoints

| Área | Endpoint | Finalidade |
|---|---|---|
| Sistema | `GET /sistema/saude` | Saúde da API |
| Autenticação | `GET /autenticacao/me` | Usuário autenticado |
| Projetos | `GET/POST /projetos` | Listagem e criação |
| Grupos | `GET/POST /grupos` | Grupos comparáveis |
| Entidades | `GET/POST /entidades` | Entidades avaliadas |
| Indicadores | `GET/POST /indicadores` | Indicadores do projeto |
| Observações | `GET/POST /observacoes` | Valores por entidade, indicador e período |
| Importações | `POST /importacoes` | Upload e preview |
| Importações | `POST /importacoes/{id}/validar` | Dry-run |
| Importações | `POST /importacoes/{id}/confirmar` | Confirmação transacional |
| Bases | `GET/POST /bases` | Consulta e criação de Bases de Referência |
| Bases | `POST /bases/{id}/processar` | Processamento da Base |
| Bases | `PATCH /bases/{id}/pesos` | Ajuste de pesos |
| Bases | `POST /bases/{id}/ativar` | Ativação da Base |
| Avaliações | `POST /avaliacoes` | Avaliação individual |
| Avaliações | `POST /avaliacoes/processar-lote` | Processamento em lote |
| Avaliações | `GET /avaliacoes/{id}` | Consulta de avaliação |
| Analytics | `GET /analytics/overview` | Totais e ranking do período |
| Analytics | `GET /analytics/entidades/{entidade_id}` | Avaliação, indicadores, evolução, eventos e leitura do período |
| Eventos | `GET/POST /eventos` | Consulta e criação de eventos gerenciais |
| Eventos | `PATCH/DELETE /eventos/{id}` | Edição e exclusão de evento |

Para a lista completa e os contratos de cada rota, consulte o Swagger em `/docs`.

---

## 11. Importação assistida incremental

O MVP aceita CSV e XLSX com limite de 10 MB.

Fluxo principal:

1. `POST /importacoes`: guarda temporariamente o arquivo e retorna preview e metadados de leitura.
2. `POST /importacoes/{id}/validar`: executa o dry-run com mapeamento explícito.
3. `POST /importacoes/{id}/confirmar`: grava o lote validado em uma única transação.

O dry-run separa erros bloqueantes de alertas. Valores extremos podem ser sinalizados por regra de **3 IQR**; eles não são alterados silenciosamente. Duplicidades bloqueantes impedem a confirmação.

Também existem recursos de rastreabilidade, anulação de lote e perfis de importação.

---

## 12. Bases de Referência e Global Score

A Base de Referência define o universo comparável e permanece preservada depois de ativada.

Para indicadores válidos, a API constrói uma régua completa com **101 cortes percentílicos: P0, P1, ..., P100**.

Na avaliação:

- a observação da entidade é posicionada nessa régua;
- cada indicador recebe pontuação de 0 a 100;
- a direção do indicador pode ser `MAIOR_MELHOR` ou `MENOR_MELHOR`;
- os pesos dos indicadores participantes devem totalizar exatamente 100%;
- o Global Score é a soma ponderada das pontuações, permanecendo na escala **0 a 100**;
- ausência de dados necessários pode gerar avaliação `INCOMPLETA`, sem transformar ausência em nota zero.

O backend **não atribui rating ou faixa conceitual** nesta versão do MVP.

---

## 13. Analytics e eventos

`GET /analytics/overview` retorna a visão macro de um projeto/grupo/período, incluindo totais e ranking quando a Base é do modo `ENTRE_ENTIDADES`.

`GET /analytics/entidades/{entidade_id}` retorna o detalhe da entidade para a mesma Base de Referência, incluindo:

- avaliação do período;
- indicadores;
- evolução temporal;
- eventos associados aos períodos;
- leitura objetiva do período, como score atual, score anterior, variação absoluta e tendência.

Eventos gerenciais são fatos registrados no mesmo período. A aplicação **não infere que um evento causou melhora ou piora do desempenho**.

---

## 14. Segurança e integridade

- rotas protegidas usam `Authorization: Bearer <token>`;
- credenciais secretas não devem ser versionadas;
- entidades e indicadores preservam histórico por desativação lógica quando aplicável;
- Bases ativadas preservam a referência usada nos cálculos;
- avaliações existentes não são sobrescritas pelo processamento em lote.

---

## 15. Limitações do MVP

- o banco padrão é SQLite em arquivo local;
- a validação de identidade depende de conectividade com o Supabase Auth;
- o servidor Flask e o Vite são usados de forma adequada à demonstração acadêmica/local, não como desenho de produção em larga escala;
- Docker Compose não é necessário para o funcionamento do MVP; os componentes podem ser executados separadamente ou na mesma rede Docker.
