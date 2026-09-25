# GlobalScore API

> **MEASURE | COMPARE | ADVANCE**  
> Backend RESTful do MVP GlobalScore para persistência de dados, construção de Bases de Referência congeladas, motor de cálculo de avaliações ponderadas/percentílicas, importação assistida de dados, analytics comparativos e eventos gerenciais.

---

## 1. Visão Geral e Papel do Backend

O **GlobalScore API** é a camada de negócio e persistência do ecossistema GlobalScore. Suas principais responsabilidades são:

1. **Gestão de Estrutura de Domínio**: Cadastro e organização de Projetos, Grupos de Comparação, Entidades e Indicadores.
2. **Importação Assistida Incremental**: Upload de arquivos CSV/XLSX com preview, validação rigorosa (dry-run com detecção estatística de outliers por 3 IQR), alertas de qualidade e confirmação em lote transacional.
3. **Motor de Cálculo e Avaliação**:
   - Construção e congelamento de **Bases de Referência** (cálculo automático de estatísticas e limites percentílicos p10–p90);
   - Cálculo de **Global Score** (escore global de 0,0 a 10,0) para avaliações individuais e em lote;
   - Atribuição de percentis, pesos e faixas conceituais por indicador.
4. **Analytics e Storytelling**:
   - Séries temporais e evolução histórica por entidade;
   - Ranking relativo entre entidades de uma mesma base/período;
   - Resumos consolidados e diagnósticos executivos.
5. **Eventos Gerenciais**: Registro de marcos de intervenção e eventos na linha do tempo.
6. **Autenticação e Segurança**: Integração com API externa de autenticação (Supabase Auth) e controle de acesso via Bearer Token.

---

## 2. Tecnologias Utilizadas

- **Linguagem**: Python 3.11
- **Framework Web & API**: Flask 3.x, Flask-RESTX (Swagger OpenAPI 2.0/3.0)
- **ORMs e Persistência**: Flask-SQLAlchemy, SQLite (banco de dados de domínio)
- **Manipulação de Dados**: `openpyxl` (leitura de XLSX), `csv` (nativo)
- **Autenticação Externa**: Supabase Auth HTTP API (`urllib.request` nativo, sem SDK pesado)
- **Containerização**: Docker (imagem Linux leve baseada em `python:3.11-slim`)
- **Suíte de Testes**: Pytest, Pytest-Flask

---

## 3. Estrutura do Repositório

```text
.
├── Dockerfile                  # Containerização do backend (raiz)
├── .dockerignore              # Exclusões para build Docker (raiz)
├── README.md                  # Documentação principal (raiz)
├── CONTEXTO_DO_PROJETO.md     # Fonte canônica das regras de negócio
├── backend/
│   ├── app/                   # Código-fonte da aplicação Flask
│   │   ├── api/               # Namespaces RESTX, DTOs (models) e rotas Swagger
│   │   ├── modelos/           # Entidades SQLAlchemy e schemas de dados
│   │   ├── servicos/          # Regras de negócio, motor de cálculo, importações, analytics
│   │   └── configuracao.py    # Variáveis de ambiente e inicialização do app
│   ├── dados/                 # Diretório de persistência SQLite (globalscore.db)
│   ├── tests/                 # Suíte de 57 testes unitários e de integração
│   ├── executar.py            # Ponto de entrada do servidor Flask (0.0.0.0:5000)
│   ├── requirements.txt       # Dependências de produção
│   └── requirements-dev.txt   # Dependências de desenvolvimento e testes
└── docs/                      # Documentação complementar do projeto
```

---

## 4. Requisitos e Configuração de Ambiente

### Pre-requisitos
- **Python**: versão 3.10 ou 3.11
- **Docker**: (opcional, para execução em container)

### Criação do Ambiente Virtual (Linux / macOS)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
```

### Configuração de Variáveis de Ambiente (`.env`)

Crie um arquivo `.env` no diretório `backend/` (ou defina as variáveis no ambiente do sistema):

```dotenv
SUPABASE_URL=https://sua-instancia.supabase.co
SUPABASE_PUBLIC_KEY=sua-chave-publica-anon
```

> **Atenção**: Use somente a chave pública (`publishable` / `anon`). **Nunca** utilize a `service_role` key. O backend não armazena senhas nem credenciais privadas do Supabase.

---

## 5. Execução Local e Testes

### Executar a API Localmente

```bash
cd backend
python executar.py
```

O servidor estará escutando em `http://localhost:5000` (ou `http://127.0.0.1:5000`).

### Executar a Suíte de Testes (QA)

A partir da raiz do repositório ou de `backend/`:

```bash
pytest backend/tests -q
```

**Resultado Atual de Qualidade**:  
✅ **57 testes automatizados passando 100%** (`57 passed`), cobrindo:
- CRUD e filtros de Projetos, Grupos, Entidades e Indicadores;
- Desativação lógica de entidades e indicadores (sem exclusão de histórico);
- Importação incremental CSV/XLSX, dry-run, detecção de outliers e tratamento de duplicidades;
- Construção, congelamento e ativação de Bases de Referência;
- Motor de cálculo de avaliações individuais e em lote;
- Analytics, evolução temporal, ranking relativo e notas executivas;
- Validação de tokens e respostas HTTP 401/503 da integração Supabase Auth.

---

## 6. Execução com Docker

O `Dockerfile` na raiz do repositório encapsula o ambiente do backend Python em um container isolado.

### Build da Imagem

```bash
docker build -t globalscore-api .
```

### Executar o Container

```bash
docker run --rm -p 5000:5000 --env-file backend/.env globalscore-api
```

Para persistir os dados entre reinicializações do container, monte um volume no diretório `/app/dados`:

```bash
docker run --rm -p 5000:5000 -v globalscore_dados:/app/dados --env-file backend/.env globalscore-api
```

---

## 7. Documentação OpenAPI / Swagger

A documentação interativa OpenAPI (Swagger UI) é gerada automaticamente pelo Flask-RESTX e fica disponível em:

👉 **`http://localhost:5000/docs`**

### Recursos da Interface Swagger `/docs`:
- Visualização detalhada dos **Namespaces** (`/sistema`, `/autenticacao`, `/projetos`, `/grupos`, `/entidades`, `/indicadores`, `/observacoes`, `/bases`, `/avaliacoes`, `/importacoes`, `/analytics`, `/eventos`);
- Inspeção dos modelos de entrada (Request Body DTOs) e resposta (Response Schemas);
- Botão **Authorize** no canto superior direito para inserção do token `Bearer <seu_token_jwt>`, liberando o teste interativo das rotas protegidas diretamente no navegador.

---

## 8. Autenticação e API Externa (Supabase Auth)

### Serviço Externo Utilizado
O **Supabase Auth** é a API pública externa utilizada para autenticação de usuários via e-mail e senha.

### Fluxo de Autenticação

```text
Navegador / Frontend
  │ 1. POST e-mail/senha no Supabase Auth (/auth/v1/token?grant_type=password)
  ▼
Supabase Auth (Serviço Externo)
  │ 2. Retorna access_token (JWT)
  ▼
Navegador / Frontend
  │ 3. Envia requisição ao Flask com "Authorization: Bearer <access_token>"
  ▼
Backend Flask RESTX
  │ 4. Valida o token via HTTP GET no Supabase (/auth/v1/user)
  │ 5. Se válido: extrai ID e e-mail do usuário e processa a requisição.
  │    Se inválido/inexistente: retorna HTTP 401 Unauthorized.
  │    Se Supabase indisponível: retorna HTTP 503 Service Unavailable.
  ▼
SQLite (Banco de Domínio Local)
```

> **Nota**: O banco de dados principal de domínio do GlobalScore é o **SQLite** local (`globalscore.db`). O Supabase Auth atua exclusivamente como a API externa de validação de identidade.

---

## 9. Principais Grupos de Endpoints

| Namespace | Caminho Base | Descrição Resumida |
|---|---|---|
| `sistema` | `/sistema/saude` | Verificação de integridade da API |
| `autenticacao` | `/autenticacao/me` | Dados do usuário logado via Supabase |
| `projetos` | `/projetos` | CRUD e seleção de projetos |
| `grupos` | `/grupos` | Grupos de comparação dentro de um projeto |
| `entidades` | `/entidades` | Unidades/Entidades sob avaliação (aceita `PATCH` com desativação lógica) |
| `indicadores` | `/indicadores` | Indicadores de desempenho (`DELETE` executa desativação lógica) |
| `observacoes` | `/observacoes` | Lançamentos brutos por entidade, indicador e período |
| `bases` | `/bases` | Criação, congelamento, ajuste de pesos e ativação de Bases de Referência |
| `avaliacoes` | `/avaliacoes` | Cálculo de avaliações individuais (`POST`) e em lote (`POST /lote`) |
| `importacoes` | `/importacoes` | Workflow assistido em 3 etapas (upload, dry-run/validar, confirmar) |
| `analytics` | `/analytics` | Evolução histórica (`/evolucao`), ranking (`/ranking`) e resumo por base |
| `eventos` | `/eventos` | Registro e consulta de marcos de intervenção na linha do tempo |

---

## 10. Funcionalidades Principais do Backend

### A) Importação Assistida Incremental
- Suporte a CSV e XLSX (até 10 MB);
- Workflow em 3 etapas: `POST /importacoes` (preview/delimitador) → `POST /importacoes/{id}/validar` (dry-run com 3 IQR) → `POST /importacoes/{id}/confirmar` (gravação em lote transacional);
- Detecção estatística de valores discrepantes (outliers por 3 IQR);
- Confirmação de alertas de qualidade exigida quando status for `VALIDADA_COM_ALERTAS`.

### B) Bases de Referência e Pesagem
- Congelamento estático de estatísticas (min, máx, p10..p90) de um período/grupo de referência;
- Atribuição parametrizável de pesos aos indicadores;
- Preservação da irrepetibilidade: avaliações utilizam as regras da base ativa no momento da geração.

### C) Avaliações e Global Score
- Normalização de observações brutas em percentis (0 a 100);
- Aplicação de pesos configurados para composição do **Global Score** (escore de 0,0 a 10,0);
- Atribuição de faixas conceituais e ratings;
- Suporte a cálculo em lote (`POST /avaliacoes/lote`) para todas as entidades de um grupo.

### D) Analytics & Storytelling
- Consulta de evolução temporal de uma entidade ao longo dos períodos;
- Geração de ranking relativo entre entidades pertencentes à mesma base e período;
- Síntese diagnóstica e resumos para alimentação de interfaces executivas.

---

## 11. Segurança e Integridade

- **Validação de Token**: Requisições protegidas exigem cabeçalho `Authorization: Bearer <token>`;
- **Desativação Lógica**: Indicadores e entidades não são deletados fisicamente do banco de dados quando desativados, preservando o histórico de observações e avaliações já calculadas;
- **Isolamento de Credenciais**: Nenhuma chave secreta ou senha de usuário trafega ou é gravada no banco de dados local.

---

## 12. Limitações Relevantes do MVP

1. **Banco SQLite**: Por padrão, o MVP utiliza SQLite como banco de dados em arquivo local (`dados/globalscore.db`). Em ambientes Docker sem volume persistente montado, a reativação do container recria o banco inicial.
2. **Dependência do Supabase Auth**: As rotas protegidas dependem da conectividade HTTP com o serviço externo do Supabase para validação de JWTs.
3. **Escopo Acadêmico**: O MVP é focado na consolidação dos conceitos de avaliação multicritério, percentis e storytelling de desempenho.
