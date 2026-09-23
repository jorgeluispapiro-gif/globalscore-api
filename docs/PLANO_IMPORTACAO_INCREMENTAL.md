# Plano técnico da importação incremental

**Etapa:** 4B2-A0

**Status:** proposta técnica para aprovação; nenhuma funcionalidade implementada

**Referências:** backend `cf1afef0502457d872678e4c822457cdcaa2314d` e frontend `cd9d96afefebb235bde473cfabffec886dce058b`

## 1. Estado atual

O fluxo 4B1 permite receber CSV ou XLSX, inspecionar o arquivo, configurar a
leitura, mapear colunas, resolver entidades desconhecidas, validar sem gravar e
confirmar o lote de forma transacional.

Cada `Importacao` já preserva:

- projeto, tipo e hash do arquivo;
- configuração de leitura em JSON;
- mapeamento confirmado em JSON;
- decisões de entidades;
- erros, alertas e totais da validação;
- identidade do usuário que criou o lote;
- observações criadas, por meio de `Observacao.importacao_id`.

A validação é um *dry-run*. Ela persiste apenas a configuração e o resultado da
análise no lote. Entidades, indicadores e observações são criados somente na
confirmação. A confirmação relê o arquivo, repete a validação e grava tudo em uma
transação.

A retomada 4B1-C recupera a configuração e o mapeamento do próprio lote pendente.
Ela não reutiliza a configuração de um lote concluído em outro arquivo.

## 2. Problema

Depois da primeira importação, arquivos mensais com a mesma estrutura exigem
novo mapeamento. O lote concluído possui informação suficiente como origem, mas
não é um perfil reutilizável seguro:

- o JSON histórico pode continuar dizendo `CRIAR` para um indicador ou entidade
  que já foi criado;
- o lote não possui nome funcional, versão, ativação ou cadeia de substituição;
- vários lotes concluídos podem ter estruturas semelhantes e gerar ambiguidade;
- não há registro explícito de qual configuração foi aplicada a um novo lote;
- alterações posteriores em indicadores, entidades ou grupos precisam ser
  detectadas antes do reaproveitamento;
- comparar o hash do arquivo não funciona, pois os valores mudam a cada período.

O objetivo da 4B2 é reconhecer uma estrutura conhecida e reduzir o trabalho do
gestor sem atribuir significado silenciosamente quando existir ambiguidade.

## 3. Alternativas consideradas

### 3.1 Derivar sempre de uma `Importacao` concluída

O sistema procuraria lotes `CONCLUIDA` do projeto e compararia o novo arquivo com
seus JSONs.

**Vantagens**

- nenhuma tabela nova;
- aproveita os dados já existentes;
- implementação inicial aparentemente curta.

**Limitações**

- mistura memória histórica do lote com configuração reutilizável;
- preserva ações `CRIAR` que perderam o sentido após a confirmação;
- não oferece nome, ativação, versão ou escolha de um perfil preferido;
- torna difícil explicar por que um lote antigo foi selecionado;
- repete normalização e validação toda vez que houver candidatos;
- dificulta evoluir a estrutura sem alterar a fotografia auditável do lote.

Essa alternativa reduz a quantidade de tabelas, mas aumenta ambiguidade e
acoplamento. Não é recomendada.

### 3.2 Criar `PerfilImportacao` persistente

Uma importação concluída serve como origem para gerar uma configuração
normalizada e reutilizável.

**Vantagens**

- separa lote histórico de configuração operacional;
- converte indicadores criados em referências estáveis por ID;
- permite nome, ativação, versão e auditoria de uso;
- permite desativar ou substituir um perfil sem alterar importações antigas;
- simplifica autorização, listagem e escolha consciente;
- preserva no novo lote uma fotografia da versão efetivamente aplicada.

**Custos**

- uma tabela nova e campos opcionais em `Importacao`;
- serviço de normalização e compatibilidade;
- migração aditiva do SQLite;
- testes de versão e referências inativas.

Essa é a alternativa recomendada.

## 4. Solução recomendada

Criar um `PerfilImportacao` versionado e pertencente a um projeto e a um usuário.
O perfil nasce de uma importação concluída e guarda:

1. a configuração de leitura reutilizável;
2. a estrutura completa e normalizada dos cabeçalhos;
3. o mapeamento sem ações `CRIAR` pendentes;
4. referências aos indicadores persistidos;
5. associações estáveis de códigos de entidade, quando existirem;
6. um grupo padrão opcional apenas como sugestão;
7. a assinatura estrutural e sua versão de algoritmo;
8. autoria, estado, versão e histórico de utilização.

O novo upload continua criando uma `Importacao`. O reconhecimento compara a
estrutura do arquivo com os perfis ativos autorizados. Aplicar um perfil apenas
preenche e fotografa configuração e mapeamento no lote. A gravação de entidades,
indicadores e observações continua proibida antes da confirmação.

Mesmo com correspondência exata, o gestor recebe uma mensagem clara, revisa o
resumo do *dry-run* e confirma o lote. O reconhecimento reduz cliques; ele não
elimina validação nem confirma dados automaticamente.

## 5. Modelo de dados proposto

### 5.1 `PerfilImportacao`

```text
PerfilImportacao
- id
- projeto_id                           FK Projeto, obrigatório
- nome                                 nome exibido ao gestor
- nome_normalizado                     comparação de unicidade
- versao                               inteiro iniciado em 1
- perfil_anterior_id                   FK PerfilImportacao, opcional
- importacao_origem_id                 FK Importacao CONCLUIDA, obrigatório
- tipo_arquivo                         CSV | XLSX
- configuracao_leitura_json            configuração reutilizável
- estrutura_json                       cabeçalhos completos e normalizados
- mapeamento_json                      papéis e IDs persistidos
- assinatura_estrutura                 SHA-256 do JSON estrutural canônico
- versao_assinatura                    inicialmente 1
- grupo_padrao_id                      FK GrupoComparavel, opcional
- ativo                                booleano
- quantidade_utilizacoes               inteiro
- ultima_utilizacao_em                 opcional
- criado_por                           ID do usuário Supabase
- criado_em
- atualizado_em

UNIQUE (projeto_id, criado_por, nome_normalizado, versao)
UNIQUE (importacao_origem_id)
```

Os JSONs evitam introduzir tabelas filhas antes de existir necessidade real. IDs
guardados no mapeamento devem ser revalidados contra as tabelas relacionais ao
aplicar o perfil. Uma evolução comercial pode normalizar as colunas do perfil em
uma tabela filha sem mudar o contrato externo.

### 5.2 Campos adicionais em `Importacao`

```text
Importacao
- perfil_importacao_id                 FK da versão aplicada, opcional
- classificacao_compatibilidade        COMPATIVEL |
                                       COMPATIVEL_COM_DIFERENCAS |
                                       INCOMPATIVEL, opcional
- diferencas_perfil_json               fotografia das diferenças, opcional
```

O perfil é versionado por linha. Assim, `perfil_importacao_id` identifica
exatamente a configuração usada e dispensa copiar um número de versão separado.
`configuracao_leitura_json` e `mapeamento_json` continuam no lote como fotografia
efetivamente aplicada.

### 5.3 Conteúdo normalizado do mapeamento

- indicador anteriormente `CRIAR`: passa a `EXISTENTE` com `indicador_id`;
- indicador anteriormente associado: preserva `indicador_id`;
- coluna ignorada: permanece explicitamente ignorada;
- entidade anteriormente criada: não preserva `CRIAR`; nos próximos lotes ela é
  reconhecida naturalmente por `Projeto + codigo`;
- código anteriormente `ASSOCIAR`: pode ser preservado como associação do
  perfil para `entidade_id`;
- código anteriormente `IGNORAR`: pode ser preservado, mas deve aparecer no
  resumo como decisão reaproveitada;
- nenhum perfil pode conter uma decisão de entidade ou indicador com ação
  `CRIAR`.

O perfil pode guardar, junto ao `indicador_id`, uma fotografia descritiva de
código, nome, unidade e direção para auditoria e apresentação. O ID permanece a
referência efetiva. Peso não é reaplicado pelo perfil; vale a configuração atual
do indicador e as regras do Global Score.

### 5.4 Configuração dinâmica que não deve ser reaproveitada silenciosamente

- `periodo_padrao`: deve ser limpo e informado para o novo lote;
- `linha_final`: por padrão deve voltar a ser dinâmica, evitando cortar novas
  linhas mensais;
- decisões sobre entidades verdadeiramente novas: sempre passam pelo fluxo 4B1;
- aceite de alertas: nunca é herdado.

## 6. Ciclo de criação, uso e versão

### 6.1 Criação

1. A importação termina como `CONCLUIDA`.
2. O gestor informa um nome para a configuração reutilizável.
3. O backend verifica propriedade, projeto e estado do lote.
4. O backend normaliza cabeçalhos, leitura, indicadores e entidades.
5. Toda ação `CRIAR` é resolvida para o registro persistido correspondente.
6. O perfil v1 é criado sem modificar a importação de origem.

A criação do perfil deve ser uma operação separada da confirmação do lote.
Assim, uma falha ao nomear ou salvar o perfil não desfaz observações corretamente
confirmadas, e a criação pode ser repetida de forma segura.

### 6.2 Utilização

1. O usuário envia novo arquivo.
2. O backend procura perfis ativos do mesmo projeto, proprietário e tipo.
3. A API devolve candidatos e diferenças.
4. O gestor aceita um perfil sugerido ou segue o fluxo manual.
5. O backend fotografa perfil, configuração e diferenças no lote.
6. O *dry-run* existente valida entidades, indicadores, períodos, valores,
   duplicidades e alertas.
7. O gestor confirma conscientemente.
8. Somente após `CONCLUIDA`, o perfil recebe nova utilização e data de uso.

### 6.3 Atualização e versão

Configuração estrutural do perfil não deve ser alterada no mesmo registro. Se
um arquivo com diferenças for revisado e confirmado, o gestor escolhe entre:

- usar a diferença somente naquele lote; ou
- salvar uma nova versão do perfil.

A nova versão aponta para `perfil_anterior_id`. A versão anterior fica inativa,
mas permanece preservada para explicar lotes antigos. Nome e estado ativo podem
ser editados sem reescrever a estrutura.

## 7. Contratos de API propostos

Todos os endpoints abaixo exigem Bearer token.

### 7.1 Perfis

```text
GET /perfis-importacao?projeto_id={id}&ativos=true
```

Lista perfis do projeto pertencentes ao usuário autenticado. Não retorna JSONs
internos completos na listagem.

```text
POST /perfis-importacao
{
  "importacao_id": 42,
  "nome": "Fechamento mensal das filiais"
}
```

Cria v1 exclusivamente a partir de uma importação concluída e pertencente ao
usuário.

```text
GET /perfis-importacao/{id}
```

Retorna estrutura, mapeamento normalizado, origem, versão e uso.

```text
PATCH /perfis-importacao/{id}
{
  "nome": "Nome atualizado",
  "ativo": false
}
```

Altera somente metadados. Não altera estrutura nem mapeamento.

```text
POST /perfis-importacao/{id}/versoes
{
  "importacao_id": 57
}
```

Cria a próxima versão a partir de outro lote concluído e revisado, preservando a
versão anterior.

### 7.2 Reconhecimento e aplicação no lote

```text
POST /importacoes/{id}/reconhecer-perfil
```

Compara o arquivo temporário com os perfis ativos autorizados. Resposta proposta:

```json
{
  "resultado": "COMPATIVEL",
  "perfil_sugerido": {
    "id": 8,
    "nome": "Fechamento mensal das filiais",
    "versao": 2
  },
  "candidatos": [],
  "diferencas": [],
  "requer_revisao": false
}
```

Se dois perfis tiverem a mesma assinatura, nenhum deles deve ser aplicado
automaticamente. A resposta lista candidatos para escolha consciente.

```text
POST /importacoes/{id}/aplicar-perfil
{
  "perfil_id": 8
}
```

Valida novamente a compatibilidade e persiste no lote somente:

- perfil aplicado;
- configuração de leitura reutilizável;
- mapeamento convertido para os índices do novo arquivo;
- classificação e diferenças.

Não cria observações, entidades ou indicadores. A rota existente
`POST /importacoes/{id}/validar` continua sendo o *dry-run* oficial. A rota
`POST /importacoes/{id}/confirmar` continua sendo o único ponto que grava os
dados planejados.

### 7.3 Códigos HTTP principais

- `200`: reconhecimento, consulta ou aplicação concluída;
- `201`: perfil ou nova versão criada;
- `400`: lote não concluído, estrutura inválida ou payload incompleto;
- `404`: lote ou perfil inexistente ou pertencente a outro usuário;
- `409`: nome/versão duplicado, perfil inativo ou diferença que impede aplicação;
- `401`: sessão ausente ou inválida.

## 8. Algoritmo de compatibilidade

### 8.1 Normalização dos cabeçalhos

Para cada célula de cabeçalho:

1. converter para texto;
2. aplicar Unicode NFKC;
3. remover espaços externos;
4. reduzir sequências de espaços internos a um espaço;
5. aplicar comparação sem diferença entre maiúsculas e minúsculas;
6. preservar acentos e pontuação.

Não haverá *fuzzy matching*, remoção de acentos ou dicionário de sinônimos no
MVP. `prazo_medio` e `prazo médio` são diferentes e exigem revisão.

A inspeção deve considerar a linha completa de cabeçalhos, inclusive colunas
ignoradas. Ler somente `colunas_utilizadas` do perfil poderia esconder uma coluna
nova e violaria o princípio de revisão explícita.

O reconhecimento tenta primeiro a configuração de leitura do perfil. Se ela não
produzir cabeçalhos válidos, um fallback limitado pode testar somente os
delimitadores já suportados ou as abas existentes do XLSX. Encontrar a estrutura
por esse fallback sempre gera `COMPATIVEL_COM_DIFERENCAS`; ele não autoriza ajuste
silencioso da configuração.

### 8.2 Estrutura canônica e assinatura

A assinatura é o SHA-256 de um JSON canônico, nunca do arquivo completo:

```json
{
  "versao_assinatura": 1,
  "tipo_arquivo": "CSV",
  "configuracao_estrutural": {
    "quantidade_linhas_cabecalho": 1,
    "delimitador": ";"
  },
  "cabecalhos_ordenados": [
    "codigo",
    "periodo",
    "produtividade",
    "qualidade"
  ]
}
```

Para XLSX, a configuração inclui a aba normalizada. Valores, quantidade de
linhas, nome do arquivo, hash do arquivo e períodos não entram na assinatura.

### 8.3 Classificações

#### `COMPATIVEL`

- mesmo projeto, proprietário e tipo de arquivo;
- assinatura estrutural idêntica;
- indicadores, associações de entidades e grupo padrão ainda pertencem ao
  projeto e estão ativos;
- não existe ambiguidade entre candidatos.

O sistema pode preencher o perfil e mostrar **Configuração conhecida
encontrada**, mas ainda executa validação e exige confirmação.

#### `COMPATIVEL_COM_DIFERENCAS`

O tipo é o mesmo, os nomes correspondentes são exatos e únicos, e existe âncora
suficiente para sugerir o perfil: pelo menos um cabeçalho estrutural
(`CODIGO_ENTIDADE` ou `PERIODO`) mais um indicador, ou pelo menos dois indicadores
conhecidos. Existe também alguma diferença:

- coluna nova ou removida;
- ordem diferente;
- nome alterado, representado como coluna ausente mais coluna nova;
- aba ou delimitador diferente, mas estrutura identificável;
- indicador, entidade associada ou grupo inativo;
- unidade ou direção atual diferente da fotografia informativa do perfil.

O backend reaproveita apenas correspondências inequívocas. Colunas novas ficam
sem papel, colunas relevantes ausentes ficam destacadas e a interface exige
revisão antes do *dry-run*.

#### `INCOMPATIVEL`

- tipo de arquivo diferente no MVP;
- nenhuma combinação mínima de cabeçalhos conhecidos foi encontrada;
- cabeçalhos normalizados duplicados tornam o remapeamento ambíguo;
- configuração de leitura não consegue produzir cabeçalhos válidos;
- perfil pertence a outro projeto ou usuário.

O arquivo segue o fluxo normal da primeira importação.

### 8.4 Ordem de escolha entre candidatos

1. correspondência exata de assinatura;
2. perfil ativo usado mais recentemente;
3. maior versão ativa da mesma cadeia.

Se ainda houver empate entre perfis distintos, a API não escolhe silenciosamente.
Ela devolve os candidatos ao frontend.

## 9. Comportamento para diferenças

| Situação | Classificação inicial | Comportamento |
|---|---|---|
| Estrutura idêntica, período novo | `COMPATIVEL` | Aplicar perfil e validar; período novo é esperado |
| Coluna nova | `COMPATIVEL_COM_DIFERENCAS` | Exigir associar, criar indicador ou ignorar |
| Coluna relevante removida | `COMPATIVEL_COM_DIFERENCAS` | Bloquear confirmação até revisão |
| Coluna ignorada removida | `COMPATIVEL_COM_DIFERENCAS` | Mostrar diferença e permitir aceite consciente |
| Ordem diferente | `COMPATIVEL_COM_DIFERENCAS` | Remapear somente nomes exatos e únicos; revisar |
| Nome de coluna alterado | `COMPATIVEL_COM_DIFERENCAS` | Mostrar uma ausente e uma nova; não inferir sinônimo |
| Nova entidade | Estrutura pode ser `COMPATIVEL` | Usar fluxo 4B1: criar, associar ou ignorar |
| Indicador inativo | `COMPATIVEL_COM_DIFERENCAS` | Não usar; exigir indicador ativo ou nova decisão |
| Grupo padrão inativo | `COMPATIVEL_COM_DIFERENCAS` | Não preselecionar para criação; exigir grupo ativo |
| Observação duplicada | Estrutura pode ser `COMPATIVEL` | `DUPLICIDADE` no *dry-run*; nunca sobrescrever |
| Aba XLSX mudou | `COMPATIVEL_COM_DIFERENCAS` se houver correspondência única | Pedir confirmação da aba |
| Tipo CSV/XLSX mudou | `INCOMPATIVEL` no MVP | Fluxo manual |

## 10. Relação com entidades, indicadores e grupos

### Entidades

O perfil não congela a lista de unidades. Em cada lote, o backend continua
reconhecendo a entidade por `projeto_id + codigo`.

- entidade criada na primeira importação será reconhecida naturalmente;
- associação manual recorrente pode ser guardada como alias no perfil;
- alias somente é aplicado se a entidade estiver ativa e pertencer ao projeto;
- entidade desconhecida continua exigindo decisão;
- perfil nunca cria uma entidade silenciosamente.

### Indicadores

O perfil referencia `indicador_id`. Ao aplicar:

- indicador deve existir, estar ativo e pertencer ao projeto;
- indicador anteriormente criado deixa de ser `CRIAR` no perfil;
- indicador inativo exige revisão;
- coluna nova exige associação, criação ou exclusão explícita;
- unidade e direção podem ser comparadas com a fotografia descritiva;
- pesos não são copiados nem recalculados pelo perfil.

### Grupos

`grupo_padrao_id` é apenas uma sugestão para entidades novas. A criação em
lote continua dependendo de decisão do gestor. Grupo inativo não pode ser usado e
duplicidade de nomes continua seguindo a regra 4B1-B2.

## 11. Relação com Bases de Referência

A importação incremental cria observações normais e não altera:

- `ItemPopulacaoReferencia`;
- `ReguaPercentil`;
- `IndicadorBaseReferencia`;
- Global Scores já calculados;
- versões ativas ou substituídas da Base de Referência.

Novos períodos podem ser avaliados contra a Base ativa existente. Mesmo uma
correção em período que pertenceu à referência não recalibra a fotografia. Para
incorporar novos dados ou correções à régua, o gestor cria explicitamente uma nova
versão da Base de Referência.

## 12. Segurança e autorização

- todas as rotas de perfil exigem Supabase Auth;
- `PerfilImportacao.criado_por` deve ser igual ao usuário autenticado;
- no contrato atual, perfil de outro usuário responde HTTP 404;
- `projeto_id` sozinho não concede acesso;
- importação e perfil aplicado devem possuir o mesmo projeto e proprietário;
- IDs de indicador, entidade e grupo são revalidados no backend;
- perfil inativo não é sugerido nem aplicado;
- caminhos temporários, access tokens e chaves nunca aparecem nas respostas;
- limites de tipo e tamanho de arquivo permanecem;
- o perfil não armazena código executável, fórmulas ou conteúdo integral do
  arquivo;
- reconhecimento e aplicação não criam dados de negócio.

Uma futura organização por equipes poderá substituir a propriedade individual
por ACL. Isso está fora do MVP.

## 13. Estratégia de testes

### Backend

1. normalização determinística de cabeçalhos e assinatura;
2. mesma estrutura e valores diferentes resultam em `COMPATIVEL`;
3. coluna nova, removida, reordenada e renomeada produzem diferenças corretas;
4. cabeçalhos duplicados e papéis essenciais ausentes resultam em
   `INCOMPATIVEL`;
5. CSV e XLSX, inclusive mudança de aba;
6. criação de perfil somente a partir de lote `CONCLUIDA` autorizado;
7. indicador anteriormente `CRIAR` vira `EXISTENTE` com ID persistido;
8. entidade criada deixa de exigir `CRIAR`; associação e exclusão são normalizadas;
9. perfil inativo ou referência inativa exige revisão;
10. aplicar perfil não cria entidade, indicador ou observação;
11. entidade nova continua produzindo a pendência 4B1;
12. período novo é aceito e duplicidade continua bloqueante;
13. nova versão preserva a anterior e lotes mantêm a versão aplicada;
14. usuário B recebe 404 para perfis e lotes do usuário A;
15. importação incremental não altera Base de Referência ou régua.

### Frontend

1. apresentar **Configuração conhecida encontrada**;
2. mostrar nome e versão antes de aplicar;
3. preencher mapeamento exato sem confirmar automaticamente;
4. destacar somente diferenças reais;
5. impedir continuação com coluna nova ou relevante ausente sem decisão;
6. usar o fluxo 4B1 para entidade desconhecida;
7. explicar indicador ou grupo inativo;
8. restaurar perfil e diferenças ao retomar o lote;
9. confirmar rapidamente um arquivo exato após o *dry-run*;
10. preservar todos os testes, lint e build.

### QA integrado

Usar um primeiro lote para criar o perfil e um segundo lote com novos períodos.
Repetir com coluna nova, coluna removida, ordem alterada, entidade nova e
duplicidade. Conferir banco, API, interface, rastreabilidade, autorização e
imutabilidade da Base de Referência.

## 14. Divisão proposta da 4B2

### 4B2-A — Persistência e normalização do perfil

- modelo e migração aditiva;
- criação do perfil a partir de lote concluído;
- conversão de `CRIAR` para IDs persistidos;
- consulta, metadados, ativação e autorização;
- testes de propriedade e ausência de persistência antecipada.

### 4B2-B — Reconhecimento e aplicação no backend

- estrutura canônica e assinatura;
- classificação exata, parcial e incompatível;
- rotas de reconhecimento e aplicação;
- fotografia do perfil no lote e versionamento;
- testes de CSV, XLSX, diferenças e referências inativas.

### 4B2-C — Experiência incremental no frontend

- aviso de configuração conhecida;
- escolha de candidato quando houver ambiguidade;
- aplicação, resumo e revisão das diferenças;
- fluxo rápido para correspondência exata;
- retomada do lote com perfil;
- criação e nova versão do perfil no momento adequado.

### 4B2-D — QA integrado e fechamento

- primeira importação e segundo período;
- derivações estruturais controladas;
- rastreabilidade, autorização e regressão;
- testes, lint e build;
- atualização da documentação do estado real.

## 15. Riscos e mitigações

| Risco | Consequência | Mitigação |
|---|---|---|
| Reutilizar JSON histórico com `CRIAR` | Duplicidade de indicadores ou entidades | Normalizar para IDs somente após `CONCLUIDA` |
| Não perceber coluna nova | Dado ignorado silenciosamente | Comparar a linha completa de cabeçalhos |
| Correspondência aproximada incorreta | Métrica associada ao indicador errado | Sem *fuzzy matching* no MVP |
| Cabeçalho duplicado | Remapeamento ambíguo | Classificar como incompatível ou exigir mapeamento manual |
| Indicador ou grupo inativo | Perfil tecnicamente antigo | Revalidar referências em todo uso |
| Reutilizar `periodo_padrao` antigo | Gravar novo arquivo no mês errado | Nunca herdar o valor do período padrão |
| Reutilizar `linha_final` | Cortar linhas novas | Fim dinâmico por padrão |
| Dois perfis igualmente compatíveis | Escolha silenciosa incorreta | Exigir seleção do gestor |
| Perfil de outro usuário | Vazamento ou IDOR | Propriedade por `criado_por` e 404 genérico |
| Estrutura versionada alterada no lugar | Perda de reprodução | Nova linha para cada versão |
| Aplicação criar dados cedo | Lote parcial | Somente confirmar grava entidades, indicadores e observações |
| Observação já existente | Sobrescrita de histórico | Manter `DUPLICIDADE` bloqueante |
| Importação recalibrar Base | Resultado histórico irreproduzível | Nenhuma chamada automática ao motor de bases |
| Migração SQLite improvisada | Banco local inconsistente | Migração aditiva e testada antes de dados reais |

## 16. Matriz de capacidades

| CAPACIDADE | JÁ EXISTE | NOVO BACKEND | NOVO FRONTEND | RISCO |
|---|---|---|---|---|
| Upload CSV/XLSX | Sim | Reutilizar | Reutilizar | Baixo |
| Preview e configuração de leitura | Sim | Reutilizar leitores | Reutilizar | Baixo |
| Mapeamento explícito | Sim | Reutilizar contrato | Reutilizar | Baixo |
| Persistência de configuração no lote | Sim | Acrescentar referência ao perfil | Restaurar perfil aplicado | Médio |
| Perfil reutilizável | Não | Modelo, normalização, versão e rotas | Nomear e administrar | Alto |
| Assinatura estrutural | Não | Canonicalização e comparação | Apenas apresentar | Médio |
| Reconhecimento automático | Não | Classificar candidatos | Mostrar sugestão | Alto |
| Reaproveitar indicador criado | Parcial | Converter `CRIAR` em ID | Mostrar como existente | Alto |
| Reaproveitar associação de entidade | Parcial | Alias por perfil e validação | Mostrar decisão reaproveitada | Médio |
| Entidade nova | Sim | Manter fluxo 4B1 | Manter decisão agrupada | Baixo |
| Coluna nova ou ausente | Não | Produzir diferenças estruturadas | Exigir revisão | Alto |
| Ordem de colunas diferente | Não | Remapear nomes exatos e únicos | Exibir mudança | Médio |
| Indicador/grupo inativo | Parcial | Detectar e bloquear reaproveitamento | Pedir nova decisão | Médio |
| Período novo | Sim | Manter normalização mensal | Mostrar resumo | Baixo |
| Duplicidade de observação | Sim | Manter bloqueio | Exibir conflito | Baixo |
| *Dry-run* sem gravação | Sim | Reutilizar | Reutilizar | Baixo |
| Confirmação transacional | Sim | Reutilizar | Reutilizar | Baixo |
| Retomada de lote | Sim | Incluir perfil e diferenças | Restaurar estado incremental | Médio |
| Autorização por proprietário | Sim nos lotes | Aplicar também aos perfis | Manter Bearer | Alto |
| Base de Referência congelada | Sim | Não acionar recalibração | Explicar consequência | Baixo |

## 17. Decisões que precisam ser aprovadas antes da 4B2-A

1. adoção de `PerfilImportacao` como tabela própria;
2. criação explícita do perfil depois da confirmação do lote;
3. estrutura versionada e imutável, com atualização por nova versão;
4. propriedade individual pelo usuário Supabase no MVP;
5. algoritmo sem *fuzzy matching*;
6. tipo de arquivo diferente classificado como incompatível no MVP;
7. associações manuais e exclusões de entidades reutilizáveis pelo perfil;
8. `periodo_padrao`, aceite de alertas e `linha_final` não herdados
   silenciosamente;
9. aplicação do perfil separada do *dry-run* e da confirmação.

Nenhum desses itens foi implementado nesta subetapa.
