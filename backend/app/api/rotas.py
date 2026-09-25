import json
from decimal import Decimal
from pathlib import Path

from flask import g, request
from flask_restx import Namespace, Resource, fields, reqparse
from sqlalchemy.exc import IntegrityError

from app.extensoes import banco
from app.modelos import (
    Avaliacao,
    BaseReferencia,
    Entidade,
    GrupoComparavel,
    Indicador,
    IndicadorBaseReferencia,
    Importacao,
    Observacao,
    PerfilImportacao,
    Projeto,
)
from app.servicos.avaliacoes import calcular_avaliacao, processar_avaliacoes_em_lote
from app.servicos.autenticacao import autenticacao_obrigatoria
from app.servicos.bases_referencia import (
    ajustar_pesos_base,
    ativar_base_referencia,
    processar_base_referencia,
    validar_configuracao_base,
)
from app.servicos.importacoes import (
    AlertasPendentesError,
    DependenciasImportacaoError,
    PerfilIncompativelError,
    anular_importacao,
    aplicar_perfil_importacao,
    confirmar_importacao,
    importacao_para_dict,
    inspecionar_arquivo,
    receber_arquivo,
    reconhecer_perfil_importacao,
    validar_importacao,
)
from app.servicos.perfis_importacao import (
    PerfilPrecisaRevisaoError,
    criar_perfil_importacao,
    perfil_para_dict,
)
from werkzeug.datastructures import FileStorage


namespace_sistema = Namespace("sistema", description="Estado da aplicação")
namespace_autenticacao = Namespace(
    "autenticacao", description="Validação da sessão externa do Supabase Auth"
)
namespace_projetos = Namespace("projetos", description="Configuração dos projetos")
namespace_grupos = Namespace("grupos", description="Configuração dos grupos comparáveis")
namespace_entidades = Namespace("entidades", description="Cadastro das entidades avaliadas")
namespace_indicadores = Namespace("indicadores", description="CRUD de indicadores")
namespace_observacoes = Namespace("observacoes", description="Consulta e entrada manual de valores")
namespace_bases = Namespace("bases", description="Construção e ativação das bases")
namespace_avaliacoes = Namespace("avaliacoes", description="Cálculo do Global Score")
namespace_analytics = Namespace(
    "analytics", description="Consultas analíticas para visão gerencial"
)
namespace_importacoes = Namespace("importacoes", description="Importação assistida de CSV e XLSX")
namespace_perfis_importacao = Namespace(
    "perfis-importacao", description="Perfis reutilizáveis de importação"
)

STATUS_IMPORTACAO_RETOMAVEL = {"ANALISADA", "VALIDADA", "VALIDADA_COM_ALERTAS"}


def obter_importacao_do_usuario(importacao_id):
    """Obtém um lote somente quando ele pertence ao usuário autenticado.

    A mesma resposta para lote inexistente ou pertencente a outra pessoa evita
    revelar a existência de dados privados por meio da API.
    """

    importacao = banco.session.get(Importacao, importacao_id)
    if importacao is None or importacao.criado_por != g.usuario_autenticado["id"]:
        namespace_importacoes.abort(404, "Importação não encontrada.")
    return importacao


def nome_grupo_normalizado(nome):
    """Normaliza somente espaços externos e caixa para comparar nomes de grupos."""

    return str(nome or "").strip().casefold()


def existe_grupo_com_nome(projeto_id, nome, grupo_ignorado_id=None):
    """Verifica duplicidade dentro do projeto sem alterar registros antigos."""

    grupos = GrupoComparavel.query.filter_by(projeto_id=projeto_id).all()
    nome_procurado = nome_grupo_normalizado(nome)
    return any(
        grupo.id != grupo_ignorado_id
        and nome_grupo_normalizado(grupo.nome) == nome_procurado
        for grupo in grupos
    )


parser_upload = reqparse.RequestParser()
parser_upload.add_argument("projeto_id", type=int, required=True, location="form")
parser_upload.add_argument("arquivo", type=FileStorage, required=True, location="files")

modelo_validacao_importacao = namespace_importacoes.model(
    "ValidacaoImportacaoEntrada",
    {
        "configuracao_leitura": fields.Raw(required=True),
        "mapeamento": fields.Raw(required=True),
    },
)

modelo_confirmacao_importacao = namespace_importacoes.model(
    "ConfirmacaoImportacaoEntrada",
    {
        "confirmar_alertas": fields.Boolean(
            default=False,
            description="Aceite explícito dos alertas de qualidade apresentados no dry-run.",
        )
    },
)

modelo_perfil_importacao = namespace_perfis_importacao.model(
    "PerfilImportacaoEntrada",
    {
        "importacao_id": fields.Integer(required=True),
        "nome": fields.String(required=True),
    },
)

modelo_aplicacao_perfil = namespace_importacoes.model(
    "AplicacaoPerfilImportacaoEntrada",
    {"perfil_id": fields.Integer(required=True)},
)


modelo_projeto = namespace_projetos.model(
    "ProjetoEntrada",
    {
        "nome": fields.String(required=True),
        "descricao": fields.String,
        "periodicidade": fields.String(enum=["MENSAL"], default="MENSAL"),
        "status": fields.String(enum=["RASCUNHO", "ATIVO", "ARQUIVADO"], default="RASCUNHO"),
        "criado_por": fields.String(default="sistema"),
    },
)

modelo_grupo = namespace_grupos.model(
    "GrupoComparavelEntrada",
    {
        "projeto_id": fields.Integer(required=True),
        "nome": fields.String(required=True),
        "descricao": fields.String,
        "ativo": fields.Boolean(default=True),
    },
)

modelo_entidade = namespace_entidades.model(
    "EntidadeEntrada",
    {
        "projeto_id": fields.Integer(required=True),
        "grupo_id": fields.Integer(required=True),
        "codigo": fields.String(required=True),
        "nome": fields.String(required=True),
        "descricao": fields.String,
        "ativa": fields.Boolean(default=True),
    },
)

modelo_indicador = namespace_indicadores.model(
    "IndicadorEntrada",
    {
        "projeto_id": fields.Integer(required=True),
        "codigo": fields.String(required=True),
        "nome": fields.String(required=True),
        "descricao": fields.String,
        "unidade_medida": fields.String(required=True),
        "direcao": fields.String(required=True, enum=["MAIOR_MELHOR", "MENOR_MELHOR"]),
        "peso_percentual": fields.Float(required=True),
        "obrigatorio": fields.Boolean(default=True),
        "participa_global_score": fields.Boolean(default=True),
    },
)

modelo_observacao = namespace_observacoes.model(
    "ObservacaoEntrada",
    {
        "entidade_id": fields.Integer(required=True),
        "indicador_id": fields.Integer(required=True),
        "periodo": fields.String(required=True, example="2026-01"),
        "valor": fields.Float(required=True),
    },
)

modelo_base = namespace_bases.model(
    "BaseReferenciaEntrada",
    {
        "projeto_id": fields.Integer(required=True),
        "grupo_id": fields.Integer(required=True),
        "modo": fields.String(required=True, enum=["ENTRE_ENTIDADES", "HISTORICO_ENTIDADE"]),
        "entidade_referencia_id": fields.Integer,
        "nome": fields.String(required=True),
        "versao": fields.Integer(required=True),
        "periodo_inicial": fields.String(required=True, example="2024-01"),
        "periodo_final": fields.String(required=True, example="2025-12"),
        "cobertura_minima_percentual": fields.Float(default=50),
    },
)

modelo_pesos = namespace_bases.model(
    "PesosBase",
    {
        "pesos": fields.Raw(
            required=True,
            description='Objeto no formato {"id_do_indicador": peso_percentual}',
        )
    },
)

modelo_avaliacao = namespace_avaliacoes.model(
    "AvaliacaoEntrada",
    {
        "entidade_id": fields.Integer(required=True),
        "base_referencia_id": fields.Integer(required=True),
        "periodo": fields.String(required=True, example="2026-01"),
    },
)

modelo_avaliacao_lote = namespace_avaliacoes.model(
    "AvaliacaoLoteEntrada",
    {
        "base_referencia_id": fields.Integer(required=True),
        "periodo_inicial": fields.String(required=True, example="2026-04"),
        "periodo_final": fields.String(required=True, example="2026-06"),
    },
)


def indicador_para_dict(indicador):
    return {
        "id": indicador.id,
        "projeto_id": indicador.projeto_id,
        "codigo": indicador.codigo,
        "nome": indicador.nome,
        "descricao": indicador.descricao,
        "unidade_medida": indicador.unidade_medida,
        "direcao": indicador.direcao,
        "peso_percentual": float(indicador.peso_percentual),
        "obrigatorio": indicador.obrigatorio,
        "participa_global_score": indicador.participa_global_score,
        "ativo": indicador.ativo,
    }


def projeto_para_dict(projeto):
    """Transforma o modelo em um objeto simples para a resposta JSON."""

    return {
        "id": projeto.id,
        "nome": projeto.nome,
        "descricao": projeto.descricao,
        "periodicidade": projeto.periodicidade,
        "status": projeto.status,
        "criado_por": projeto.criado_por,
        "criado_em": projeto.criado_em.isoformat(),
        "atualizado_em": projeto.atualizado_em.isoformat(),
    }


def grupo_para_dict(grupo):
    return {
        "id": grupo.id,
        "projeto_id": grupo.projeto_id,
        "nome": grupo.nome,
        "descricao": grupo.descricao,
        "ativo": grupo.ativo,
    }


def entidade_para_dict(entidade):
    return {
        "id": entidade.id,
        "projeto_id": entidade.projeto_id,
        "grupo_id": entidade.grupo_id,
        "codigo": entidade.codigo,
        "nome": entidade.nome,
        "descricao": entidade.descricao,
        "ativa": entidade.ativa,
    }


def observacao_para_dict(observacao):
    return {
        "id": observacao.id,
        "projeto_id": observacao.projeto_id,
        "entidade_id": observacao.entidade_id,
        "indicador_id": observacao.indicador_id,
        "periodo": observacao.periodo,
        "valor": float(observacao.valor),
        "origem": observacao.origem,
        "importacao_id": observacao.importacao_id,
    }


def confirmar_transacao(namespace, mensagem_conflito):
    """Converte violações de unicidade em mensagens compreensíveis no Swagger."""

    try:
        banco.session.commit()
    except IntegrityError:
        banco.session.rollback()
        namespace.abort(409, mensagem_conflito)


def base_para_dict(base):
    indicadores = IndicadorBaseReferencia.query.filter_by(base_referencia_id=base.id).all()
    return {
        "id": base.id,
        "grupo_id": base.grupo_id,
        "nome": base.nome,
        "versao": base.versao,
        "modo": base.modo,
        "entidade_referencia_id": base.entidade_referencia_id,
        "periodo_inicial": base.periodo_inicial,
        "periodo_final": base.periodo_final,
        "status": base.status,
        "quantidade_entidades": base.quantidade_entidades,
        "indicadores": [
            {
                "indicador_id": item.indicador_id,
                "status": item.status,
                "tamanho_populacao": item.tamanho_populacao,
                "participa_global_score": item.participa_global_score,
                "peso_aplicado": float(item.peso_aplicado),
            }
            for item in indicadores
        ],
    }


def avaliacao_para_dict(avaliacao):
    return {
        "id": avaliacao.id,
        "entidade_id": avaliacao.entidade_id,
        "base_referencia_id": avaliacao.base_referencia_id,
        "periodo": avaliacao.periodo,
        "status": avaliacao.status,
        "global_score": float(avaliacao.global_score) if avaliacao.global_score is not None else None,
        "pesos_total": float(avaliacao.pesos_total),
        "itens": [
            {
                "indicador_id": item.indicador_id,
                "valor_observado": float(item.valor_observado)
                if item.valor_observado is not None
                else None,
                "pontuacao_percentil": item.pontuacao_percentil,
                "peso_aplicado": float(item.peso_aplicado),
                "contribuicao_score": float(item.contribuicao_score)
                if item.contribuicao_score is not None
                else None,
            }
            for item in avaliacao.itens
        ],
    }


@namespace_sistema.route("/saude")
class SaudeResource(Resource):
    def get(self):
        """Confirma que a API está pronta para receber chamadas."""

        return {"status": "ok", "servico": "GlobalScore API"}


@namespace_projetos.route("")
class ProjetosResource(Resource):
    def get(self):
        """Lista os projetos cadastrados."""

        projetos = Projeto.query.order_by(Projeto.id).all()
        return [projeto_para_dict(projeto) for projeto in projetos]

    @namespace_projetos.expect(modelo_projeto, validate=True)
    def post(self):
        """Cria o contêiner principal da avaliação."""

        dados = request.json
        projeto = Projeto(
            nome=dados["nome"],
            descricao=dados.get("descricao"),
            periodicidade=dados.get("periodicidade", "MENSAL"),
            status=dados.get("status", "RASCUNHO"),
            criado_por=dados.get("criado_por", "sistema"),
        )
        banco.session.add(projeto)
        banco.session.commit()
        return projeto_para_dict(projeto), 201


@namespace_projetos.route("/<int:projeto_id>")
class ProjetoResource(Resource):
    def get(self, projeto_id):
        projeto = banco.session.get(Projeto, projeto_id)
        if projeto is None:
            namespace_projetos.abort(404, "Projeto não encontrado.")
        return projeto_para_dict(projeto)

    @namespace_projetos.expect(modelo_projeto, validate=False)
    def patch(self, projeto_id):
        """Atualiza somente dados administrativos do projeto."""

        projeto = banco.session.get(Projeto, projeto_id)
        if projeto is None:
            namespace_projetos.abort(404, "Projeto não encontrado.")
        dados = request.json or {}
        for campo in {"nome", "descricao", "status"}:
            if campo in dados:
                setattr(projeto, campo, dados[campo])
        # A periodicidade é mensal no MVP e não muda depois da criação.
        banco.session.commit()
        return projeto_para_dict(projeto)


@namespace_grupos.route("")
class GruposResource(Resource):
    @namespace_grupos.doc(params={"projeto_id": "Filtra os grupos pelo identificador do projeto."})
    def get(self):
        """Lista grupos, opcionalmente limitados a um projeto."""

        consulta = GrupoComparavel.query
        projeto_id = request.args.get("projeto_id", type=int)
        if projeto_id is not None:
            consulta = consulta.filter_by(projeto_id=projeto_id)
        return [grupo_para_dict(grupo) for grupo in consulta.order_by(GrupoComparavel.id).all()]

    @namespace_grupos.expect(modelo_grupo, validate=True)
    @namespace_grupos.response(409, "Já existe um grupo com esse nome neste projeto.")
    def post(self):
        dados = request.json
        if banco.session.get(Projeto, dados["projeto_id"]) is None:
            namespace_grupos.abort(404, "Projeto não encontrado.")
        if existe_grupo_com_nome(dados["projeto_id"], dados["nome"]):
            namespace_grupos.abort(409, "Já existe um grupo com esse nome neste projeto.")
        grupo = GrupoComparavel(
            projeto_id=dados["projeto_id"],
            nome=dados["nome"],
            descricao=dados.get("descricao"),
            ativo=dados.get("ativo", True),
        )
        banco.session.add(grupo)
        banco.session.commit()
        return grupo_para_dict(grupo), 201


@namespace_grupos.route("/<int:grupo_id>")
class GrupoResource(Resource):
    def get(self, grupo_id):
        grupo = banco.session.get(GrupoComparavel, grupo_id)
        if grupo is None:
            namespace_grupos.abort(404, "Grupo comparável não encontrado.")
        return grupo_para_dict(grupo)

    @namespace_grupos.expect(modelo_grupo, validate=False)
    @namespace_grupos.response(409, "Já existe um grupo com esse nome neste projeto.")
    def patch(self, grupo_id):
        """Preserva o vínculo do grupo com o projeto e altera seus dados próprios."""

        grupo = banco.session.get(GrupoComparavel, grupo_id)
        if grupo is None:
            namespace_grupos.abort(404, "Grupo comparável não encontrado.")
        dados = request.json or {}
        if "projeto_id" in dados and dados["projeto_id"] != grupo.projeto_id:
            namespace_grupos.abort(400, "O projeto de um grupo existente não pode ser alterado.")
        if "nome" in dados and existe_grupo_com_nome(grupo.projeto_id, dados["nome"], grupo.id):
            namespace_grupos.abort(409, "Já existe um grupo com esse nome neste projeto.")
        for campo in {"nome", "descricao", "ativo"}:
            if campo in dados:
                setattr(grupo, campo, dados[campo])
        banco.session.commit()
        return grupo_para_dict(grupo)


@namespace_entidades.route("")
class EntidadesResource(Resource):
    @namespace_entidades.doc(
        params={
            "projeto_id": "Filtra as entidades pelo identificador do projeto.",
            "grupo_id": "Filtra as entidades pelo identificador do grupo comparável.",
        }
    )
    def get(self):
        """Lista entidades com filtros independentes por projeto e grupo."""

        consulta = Entidade.query
        projeto_id = request.args.get("projeto_id", type=int)
        grupo_id = request.args.get("grupo_id", type=int)
        if projeto_id is not None:
            consulta = consulta.filter_by(projeto_id=projeto_id)
        if grupo_id is not None:
            consulta = consulta.filter_by(grupo_id=grupo_id)
        return [entidade_para_dict(item) for item in consulta.order_by(Entidade.id).all()]

    @namespace_entidades.expect(modelo_entidade, validate=True)
    def post(self):
        dados = request.json
        projeto = banco.session.get(Projeto, dados["projeto_id"])
        if projeto is None:
            namespace_entidades.abort(404, "Projeto não encontrado.")
        grupo = banco.session.get(GrupoComparavel, dados["grupo_id"])
        if grupo is None:
            namespace_entidades.abort(404, "Grupo comparável não encontrado.")
        if grupo.projeto_id != projeto.id:
            namespace_entidades.abort(400, "O grupo deve pertencer ao projeto da entidade.")
        entidade = Entidade(
            projeto_id=projeto.id,
            grupo_id=grupo.id,
            codigo=dados["codigo"],
            nome=dados["nome"],
            descricao=dados.get("descricao"),
            ativa=dados.get("ativa", True),
        )
        banco.session.add(entidade)
        confirmar_transacao(
            namespace_entidades,
            "Já existe uma entidade com esse código dentro do projeto.",
        )
        return entidade_para_dict(entidade), 201


@namespace_entidades.route("/<int:entidade_id>")
class EntidadeResource(Resource):
    def get(self, entidade_id):
        entidade = banco.session.get(Entidade, entidade_id)
        if entidade is None:
            namespace_entidades.abort(404, "Entidade não encontrada.")
        return entidade_para_dict(entidade)

    @namespace_entidades.expect(modelo_entidade, validate=False)
    def patch(self, entidade_id):
        """Edita ou desativa logicamente a entidade sem apagar seu histórico."""

        entidade = banco.session.get(Entidade, entidade_id)
        if entidade is None:
            namespace_entidades.abort(404, "Entidade não encontrada.")
        dados = request.json or {}
        if "projeto_id" in dados and dados["projeto_id"] != entidade.projeto_id:
            namespace_entidades.abort(400, "O projeto de uma entidade existente não pode ser alterado.")
        if "grupo_id" in dados and dados["grupo_id"] != entidade.grupo_id:
            namespace_entidades.abort(
                400,
                "O grupo de uma entidade existente não pode ser alterado no MVP.",
            )
        for campo in {"codigo", "nome", "descricao", "ativa"}:
            if campo in dados:
                setattr(entidade, campo, dados[campo])
        confirmar_transacao(
            namespace_entidades,
            "Já existe uma entidade com esse código dentro do projeto.",
        )
        return entidade_para_dict(entidade)


@namespace_indicadores.route("")
class IndicadoresResource(Resource):
    def get(self):
        """Lista indicadores, com filtro opcional por projeto."""

        consulta = Indicador.query
        projeto_id = request.args.get("projeto_id", type=int)
        if projeto_id:
            consulta = consulta.filter_by(projeto_id=projeto_id)
        return [indicador_para_dict(item) for item in consulta.order_by(Indicador.id).all()]

    @namespace_indicadores.expect(modelo_indicador, validate=True)
    def post(self):
        """Cria um indicador configurável."""

        dados = request.json
        if banco.session.get(Projeto, dados["projeto_id"]) is None:
            namespace_indicadores.abort(404, "Projeto não encontrado.")
        peso = Decimal(str(dados["peso_percentual"]))
        if peso < 0 or peso > 100:
            namespace_indicadores.abort(400, "O peso deve estar entre 0 e 100.")
        indicador = Indicador(
            projeto_id=dados["projeto_id"],
            codigo=dados["codigo"],
            nome=dados["nome"],
            descricao=dados.get("descricao"),
            unidade_medida=dados["unidade_medida"],
            direcao=dados["direcao"],
            peso_percentual=peso,
            obrigatorio=dados.get("obrigatorio", True),
            participa_global_score=dados.get("participa_global_score", True),
        )
        banco.session.add(indicador)
        confirmar_transacao(
            namespace_indicadores,
            "Já existe um indicador com esse código dentro do projeto.",
        )
        return indicador_para_dict(indicador), 201


@namespace_indicadores.route("/<int:indicador_id>")
class IndicadorResource(Resource):
    def get(self, indicador_id):
        indicador = banco.session.get(Indicador, indicador_id)
        if indicador is None:
            namespace_indicadores.abort(404, "Indicador não encontrado.")
        return indicador_para_dict(indicador)

    @namespace_indicadores.expect(modelo_indicador, validate=False)
    def patch(self, indicador_id):
        """Altera somente os campos enviados pelo usuário."""

        indicador = banco.session.get(Indicador, indicador_id)
        if indicador is None:
            namespace_indicadores.abort(404, "Indicador não encontrado.")
        dados = request.json or {}
        campos = {
            "codigo",
            "nome",
            "descricao",
            "unidade_medida",
            "direcao",
            "obrigatorio",
            "participa_global_score",
        }
        for campo in campos:
            if campo in dados:
                setattr(indicador, campo, dados[campo])
        if "peso_percentual" in dados:
            peso = Decimal(str(dados["peso_percentual"]))
            if peso < 0 or peso > 100:
                namespace_indicadores.abort(400, "O peso deve estar entre 0 e 100.")
            indicador.peso_percentual = peso
        confirmar_transacao(
            namespace_indicadores,
            "Já existe um indicador com esse código dentro do projeto.",
        )
        return indicador_para_dict(indicador)

    def delete(self, indicador_id):
        """Desativa o indicador para preservar seu histórico."""

        indicador = banco.session.get(Indicador, indicador_id)
        if indicador is None:
            namespace_indicadores.abort(404, "Indicador não encontrado.")
        indicador.ativo = False
        banco.session.commit()
        return {"mensagem": "Indicador desativado com sucesso."}


@namespace_observacoes.route("")
class ObservacoesResource(Resource):
    @namespace_observacoes.doc(
        params={
            "projeto_id": "Filtra pelo identificador do projeto.",
            "entidade_id": "Filtra pelo identificador da entidade.",
            "indicador_id": "Filtra pelo identificador do indicador.",
            "periodo": "Filtra pelo período mensal no formato AAAA-MM.",
        }
    )
    def get(self):
        """Consulta observações usando qualquer combinação dos filtros disponíveis."""

        consulta = Observacao.query
        filtros = {
            "projeto_id": request.args.get("projeto_id", type=int),
            "entidade_id": request.args.get("entidade_id", type=int),
            "indicador_id": request.args.get("indicador_id", type=int),
            "periodo": request.args.get("periodo", type=str),
        }
        for campo, valor in filtros.items():
            if valor is not None:
                consulta = consulta.filter_by(**{campo: valor})
        return [
            observacao_para_dict(item)
            for item in consulta.order_by(Observacao.periodo, Observacao.id).all()
        ]

    @namespace_observacoes.expect(modelo_observacao, validate=True)
    def post(self):
        """Registra manualmente uma observação mensal."""

        dados = request.json
        entidade = banco.session.get(Entidade, dados["entidade_id"])
        indicador = banco.session.get(Indicador, dados["indicador_id"])
        if entidade is None or indicador is None:
            namespace_observacoes.abort(404, "Entidade ou indicador não encontrado.")
        if entidade.projeto_id != indicador.projeto_id:
            namespace_observacoes.abort(400, "Entidade e indicador devem pertencer ao mesmo projeto.")
        observacao = Observacao(
            projeto_id=entidade.projeto_id,
            entidade_id=entidade.id,
            indicador_id=indicador.id,
            periodo=dados["periodo"],
            valor=Decimal(str(dados["valor"])),
        )
        banco.session.add(observacao)
        confirmar_transacao(
            namespace_observacoes,
            "Já existe uma observação para essa entidade, indicador e período.",
        )
        return observacao_para_dict(observacao), 201


@namespace_autenticacao.route("/me")
class UsuarioAutenticadoResource(Resource):
    @namespace_autenticacao.doc(security="Bearer")
    @autenticacao_obrigatoria
    def get(self):
        """Confirma a sessão e devolve a identidade reconhecida pelo Supabase."""

        return {
            "id": g.usuario_autenticado["id"],
            "email": g.usuario_autenticado.get("email"),
        }


@namespace_importacoes.route("")
@namespace_importacoes.doc(security="Bearer")
class ImportacoesResource(Resource):
    @namespace_importacoes.doc(
        params={
            "projeto_id": "Identificador obrigatório do projeto.",
            "pendentes": "Deve ser true para consultar lotes retomáveis.",
        },
        responses={400: "Filtro inválido ou ausente."},
    )
    @autenticacao_obrigatoria
    def get(self):
        """Lista somente os lotes retomáveis do projeto e do usuário autenticado."""

        projeto_id = request.args.get("projeto_id", type=int)
        pendentes = request.args.get("pendentes", default="true", type=str).lower()
        if projeto_id is None:
            namespace_importacoes.abort(400, "Informe o projeto_id para consultar importações pendentes.")
        if pendentes not in {"true", "1"}:
            namespace_importacoes.abort(400, "Esta consulta aceita somente pendentes=true.")
        importacoes = (
            Importacao.query.filter(
                Importacao.projeto_id == projeto_id,
                Importacao.criado_por == g.usuario_autenticado["id"],
                Importacao.status.in_(STATUS_IMPORTACAO_RETOMAVEL),
            )
            .order_by(Importacao.criado_em.desc(), Importacao.id.desc())
            .all()
        )
        return [importacao_para_dict(item) for item in importacoes]

    @namespace_importacoes.expect(parser_upload)
    @autenticacao_obrigatoria
    def post(self):
        """Recebe arquivo temporário e devolve uma inspeção sem decidir o mapeamento."""

        dados = parser_upload.parse_args()
        try:
            importacao, preview = receber_arquivo(
                dados["projeto_id"], dados["arquivo"], g.usuario_autenticado["id"]
            )
            resposta = importacao_para_dict(importacao)
            resposta["inspecao"] = preview
            return resposta, 201
        except (ValueError, UnicodeDecodeError) as erro:
            banco.session.rollback()
            namespace_importacoes.abort(400, str(erro))


@namespace_importacoes.route("/<int:importacao_id>")
@namespace_importacoes.doc(security="Bearer")
class ImportacaoResource(Resource):
    @namespace_importacoes.response(404, "Importação não encontrada.")
    @autenticacao_obrigatoria
    def get(self, importacao_id):
        """Consulta somente metadados e resultados; o arquivo e seu caminho são privados."""

        importacao = obter_importacao_do_usuario(importacao_id)
        return importacao_para_dict(importacao)


@namespace_importacoes.route("/<int:importacao_id>/reconhecer-perfil")
@namespace_importacoes.doc(security="Bearer")
class ReconhecerPerfilImportacaoResource(Resource):
    @namespace_importacoes.response(404, "Importação não encontrada.")
    @autenticacao_obrigatoria
    def post(self, importacao_id):
        """Compara a estrutura completa sem persistir dados de negócio."""

        importacao = obter_importacao_do_usuario(importacao_id)
        try:
            return reconhecer_perfil_importacao(
                importacao, g.usuario_autenticado["id"]
            )
        except ValueError as erro:
            namespace_importacoes.abort(409, str(erro))


@namespace_importacoes.route("/<int:importacao_id>/aplicar-perfil")
@namespace_importacoes.doc(security="Bearer")
class AplicarPerfilImportacaoResource(Resource):
    @namespace_importacoes.expect(modelo_aplicacao_perfil, validate=True)
    @namespace_importacoes.response(404, "Importação ou perfil não encontrado.")
    @autenticacao_obrigatoria
    def post(self, importacao_id):
        """Copia a configuração após recalcular a compatibilidade exata."""

        importacao = obter_importacao_do_usuario(importacao_id)
        perfil = banco.session.get(PerfilImportacao, request.json["perfil_id"])
        if perfil is None or perfil.criado_por != g.usuario_autenticado["id"]:
            namespace_importacoes.abort(404, "Perfil de importação não encontrado.")
        try:
            aplicar_perfil_importacao(importacao, perfil)
            return {
                "resultado": "APLICADO",
                "perfil_aplicado": {
                    "id": perfil.id,
                    "nome": perfil.nome,
                    "versao": perfil.versao,
                },
                "importacao": importacao_para_dict(importacao),
            }
        except (PerfilIncompativelError, PerfilPrecisaRevisaoError) as erro:
            banco.session.rollback()
            namespace_importacoes.abort(409, str(erro))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as erro:
            banco.session.rollback()
            namespace_importacoes.abort(400, str(erro))


@namespace_importacoes.route("/<int:importacao_id>/inspecao")
@namespace_importacoes.doc(security="Bearer")
class InspecaoImportacaoResource(Resource):
    @namespace_importacoes.response(404, "Importação não encontrada.")
    @namespace_importacoes.response(
        409, "O arquivo temporário desta importação não está mais disponível para retomada."
    )
    @autenticacao_obrigatoria
    def get(self, importacao_id):
        """Reinspeciona o arquivo privado sem devolver seu caminho físico."""

        importacao = obter_importacao_do_usuario(importacao_id)
        caminho = Path(importacao.caminho_arquivo_temporario or "")
        if importacao.status not in STATUS_IMPORTACAO_RETOMAVEL or not caminho.is_file():
            namespace_importacoes.abort(
                409,
                "O arquivo temporário desta importação não está mais disponível para retomada.",
            )
        try:
            return inspecionar_arquivo(importacao)
        except (OSError, UnicodeDecodeError, ValueError):
            namespace_importacoes.abort(
                409,
                "O arquivo temporário desta importação não está mais disponível para retomada.",
            )


@namespace_importacoes.route("/<int:importacao_id>/validar")
@namespace_importacoes.doc(security="Bearer")
class ValidarImportacaoResource(Resource):
    @namespace_importacoes.response(404, "Importação não encontrada.")
    @namespace_importacoes.expect(modelo_validacao_importacao, validate=True)
    @autenticacao_obrigatoria
    def post(self, importacao_id):
        """Executa o dry-run e não grava entidades, indicadores ou observações."""

        importacao = obter_importacao_do_usuario(importacao_id)
        try:
            return validar_importacao(importacao.id, request.json)
        except ValueError as erro:
            banco.session.rollback()
            namespace_importacoes.abort(400, str(erro))


@namespace_importacoes.route("/<int:importacao_id>/confirmar")
@namespace_importacoes.doc(security="Bearer")
class ConfirmarImportacaoResource(Resource):
    @namespace_importacoes.response(404, "Importação não encontrada.")
    @namespace_importacoes.expect(modelo_confirmacao_importacao, validate=False)
    @autenticacao_obrigatoria
    def post(self, importacao_id):
        """Confirma atomicamente; alertas exigem aceite explícito do gestor."""

        importacao = obter_importacao_do_usuario(importacao_id)
        try:
            dados = request.get_json(silent=True) or {}
            importacao, quantidade = confirmar_importacao(
                importacao.id, dados.get("confirmar_alertas") is True
            )
            resposta = importacao_para_dict(importacao)
            resposta["observacoes_criadas"] = quantidade
            return resposta
        except AlertasPendentesError as erro:
            banco.session.rollback()
            namespace_importacoes.abort(409, str(erro))
        except ValueError as erro:
            banco.session.rollback()
            namespace_importacoes.abort(400, str(erro))


@namespace_importacoes.route("/<int:importacao_id>/observacoes")
@namespace_importacoes.doc(security="Bearer")
class ObservacoesImportacaoResource(Resource):
    @namespace_importacoes.response(404, "Importação não encontrada.")
    @autenticacao_obrigatoria
    def get(self, importacao_id):
        """Lista os registros gravados por um lote para garantir rastreabilidade."""

        importacao = obter_importacao_do_usuario(importacao_id)
        observacoes = Observacao.query.filter_by(importacao_id=importacao.id).order_by(
            Observacao.id
        )
        return [observacao_para_dict(item) for item in observacoes]


@namespace_importacoes.route("/<int:importacao_id>/anular")
@namespace_importacoes.doc(security="Bearer")
class AnularImportacaoResource(Resource):
    @namespace_importacoes.response(404, "Importação não encontrada.")
    @autenticacao_obrigatoria
    def post(self, importacao_id):
        """Cancela lote pendente ou anula lote concluído que ainda não foi consumido."""

        importacao = obter_importacao_do_usuario(importacao_id)
        try:
            importacao, quantidade = anular_importacao(importacao.id)
            resposta = importacao_para_dict(importacao)
            resposta["observacoes_removidas"] = quantidade
            return resposta
        except DependenciasImportacaoError as erro:
            banco.session.rollback()
            namespace_importacoes.abort(
                409,
                str(erro),
                quantidade_bases_dependentes=len(erro.bases),
                quantidade_avaliacoes_dependentes=len(erro.avaliacoes),
                bases_dependentes=erro.bases,
                avaliacoes_dependentes=erro.avaliacoes,
            )
        except ValueError as erro:
            banco.session.rollback()
            namespace_importacoes.abort(400, str(erro))


@namespace_perfis_importacao.route("")
@namespace_perfis_importacao.doc(security="Bearer")
class PerfisImportacaoResource(Resource):
    @autenticacao_obrigatoria
    def get(self):
        """Lista somente os perfis do usuário autenticado no projeto informado."""

        projeto_id = request.args.get("projeto_id", type=int)
        if projeto_id is None:
            namespace_perfis_importacao.abort(
                400, "Informe projeto_id para listar os perfis."
            )
        perfis = PerfilImportacao.query.filter_by(
            projeto_id=projeto_id,
            criado_por=g.usuario_autenticado["id"],
        ).order_by(PerfilImportacao.id).all()
        return [perfil_para_dict(perfil) for perfil in perfis]

    @namespace_perfis_importacao.expect(modelo_perfil_importacao, validate=True)
    @namespace_perfis_importacao.response(201, "Perfil criado.")
    @namespace_perfis_importacao.response(404, "Importação não encontrada.")
    @autenticacao_obrigatoria
    def post(self):
        """Cria a versão 1 a partir de uma importação concluída do usuário."""

        dados = request.json
        importacao = banco.session.get(Importacao, dados["importacao_id"])
        if importacao is None or importacao.criado_por != g.usuario_autenticado["id"]:
            namespace_perfis_importacao.abort(404, "Importação não encontrada.")
        try:
            perfil = criar_perfil_importacao(
                importacao,
                dados["nome"],
                g.usuario_autenticado["id"],
            )
            return perfil_para_dict(perfil), 201
        except IntegrityError:
            banco.session.rollback()
            namespace_perfis_importacao.abort(
                409, "Esta importação já originou um perfil."
            )
        except (ValueError, json.JSONDecodeError) as erro:
            banco.session.rollback()
            namespace_perfis_importacao.abort(400, str(erro))


@namespace_bases.route("")
@namespace_bases.doc(security="Bearer")
class BasesResource(Resource):
    @namespace_bases.doc(params={"projeto_id": "Filtra as bases pelo projeto informado."})
    @autenticacao_obrigatoria
    def get(self):
        consulta = BaseReferencia.query
        projeto_id_informado = request.args.get("projeto_id")
        if projeto_id_informado is not None:
            try:
                projeto_id = int(projeto_id_informado)
            except ValueError:
                namespace_bases.abort(400, "O projeto_id deve ser um número inteiro.")
            consulta = consulta.filter_by(projeto_id=projeto_id)
        return [base_para_dict(item) for item in consulta.order_by(BaseReferencia.id).all()]

    @namespace_bases.expect(modelo_base, validate=True)
    @autenticacao_obrigatoria
    def post(self):
        dados = request.json
        grupo = banco.session.get(GrupoComparavel, dados["grupo_id"])
        if grupo is None or grupo.projeto_id != dados["projeto_id"]:
            namespace_bases.abort(400, "O grupo deve pertencer ao projeto da base.")
        base = BaseReferencia(
            projeto_id=dados["projeto_id"],
            grupo_id=dados["grupo_id"],
            modo=dados["modo"],
            entidade_referencia_id=dados.get("entidade_referencia_id"),
            nome=dados["nome"],
            versao=dados["versao"],
            periodo_inicial=dados["periodo_inicial"],
            periodo_final=dados["periodo_final"],
            cobertura_minima_percentual=Decimal(
                str(dados.get("cobertura_minima_percentual", 50))
            ),
            minimo_observacoes=3,
        )
        banco.session.add(base)
        try:
            banco.session.flush()
            validar_configuracao_base(base)
            banco.session.commit()
        except ValueError as erro:
            banco.session.rollback()
            namespace_bases.abort(400, str(erro))
        return base_para_dict(base), 201


@namespace_bases.route("/<int:base_id>")
@namespace_bases.doc(security="Bearer")
class BaseResource(Resource):
    @autenticacao_obrigatoria
    def get(self, base_id):
        base = banco.session.get(BaseReferencia, base_id)
        if base is None:
            namespace_bases.abort(404, "Base de Referência não encontrada.")
        return base_para_dict(base)


@namespace_bases.route("/<int:base_id>/processar")
@namespace_bases.doc(security="Bearer")
class ProcessarBaseResource(Resource):
    @autenticacao_obrigatoria
    def post(self, base_id):
        try:
            return base_para_dict(processar_base_referencia(base_id))
        except ValueError as erro:
            banco.session.rollback()
            namespace_bases.abort(400, str(erro))


@namespace_bases.route("/<int:base_id>/pesos")
@namespace_bases.doc(security="Bearer")
class PesosBaseResource(Resource):
    @namespace_bases.expect(modelo_pesos, validate=True)
    @autenticacao_obrigatoria
    def patch(self, base_id):
        pesos = {int(chave): valor for chave, valor in request.json["pesos"].items()}
        try:
            ajustar_pesos_base(base_id, pesos)
            return base_para_dict(banco.session.get(BaseReferencia, base_id))
        except ValueError as erro:
            banco.session.rollback()
            namespace_bases.abort(400, str(erro))


@namespace_bases.route("/<int:base_id>/ativar")
@namespace_bases.doc(security="Bearer")
class AtivarBaseResource(Resource):
    @autenticacao_obrigatoria
    def post(self, base_id):
        try:
            return base_para_dict(ativar_base_referencia(base_id))
        except ValueError as erro:
            banco.session.rollback()
            namespace_bases.abort(400, str(erro))


@namespace_avaliacoes.route("")
@namespace_avaliacoes.doc(security="Bearer")
class AvaliacoesResource(Resource):
    @namespace_avaliacoes.expect(modelo_avaliacao, validate=True)
    @autenticacao_obrigatoria
    def post(self):
        dados = request.json
        try:
            avaliacao = calcular_avaliacao(
                dados["entidade_id"], dados["base_referencia_id"], dados["periodo"]
            )
            return avaliacao_para_dict(avaliacao), 201
        except ValueError as erro:
            banco.session.rollback()
            namespace_avaliacoes.abort(400, str(erro))


@namespace_avaliacoes.route("/processar-lote")
@namespace_avaliacoes.doc(security="Bearer")
class ProcessarAvaliacoesLoteResource(Resource):
    @namespace_avaliacoes.expect(modelo_avaliacao_lote, validate=True)
    @autenticacao_obrigatoria
    def post(self):
        """Calcula entidades elegíveis e períodos sem sobrescrever avaliações existentes."""

        dados = request.json
        try:
            return processar_avaliacoes_em_lote(
                dados["base_referencia_id"],
                dados["periodo_inicial"],
                dados["periodo_final"],
            )
        except ValueError as erro:
            banco.session.rollback()
            namespace_avaliacoes.abort(400, str(erro))


@namespace_avaliacoes.route("/<int:avaliacao_id>")
@namespace_avaliacoes.doc(security="Bearer")
class AvaliacaoResource(Resource):
    @autenticacao_obrigatoria
    def get(self, avaliacao_id):
        avaliacao = banco.session.get(Avaliacao, avaliacao_id)
        if avaliacao is None:
            namespace_avaliacoes.abort(404, "Avaliação não encontrada.")
        return avaliacao_para_dict(avaliacao)


@namespace_analytics.route("/overview")
@namespace_analytics.doc(security="Bearer")
class VisaoGeralAnaliticaResource(Resource):
    @namespace_analytics.doc(
        params={
            "projeto_id": "Identificador obrigatório do projeto.",
            "grupo_id": "Identificador obrigatório do grupo comparável.",
            "periodo": "Período mensal obrigatório no formato AAAA-MM.",
        }
    )
    @autenticacao_obrigatoria
    def get(self):
        """Retorna totais e ranking já preparados para a visão geral."""

        from app.servicos.analytics import (
            RecursoAnaliticoNaoEncontradoError,
            obter_visao_geral,
        )

        projeto_id = request.args.get("projeto_id", type=int)
        grupo_id = request.args.get("grupo_id", type=int)
        periodo = request.args.get("periodo", type=str)
        if projeto_id is None or grupo_id is None or periodo is None:
            namespace_analytics.abort(
                400, "Informe projeto_id, grupo_id e periodo para consultar a visão geral."
            )
        try:
            return obter_visao_geral(projeto_id, grupo_id, periodo)
        except RecursoAnaliticoNaoEncontradoError as erro:
            namespace_analytics.abort(404, str(erro))
        except ValueError as erro:
            namespace_analytics.abort(400, str(erro))


@namespace_analytics.route("/entidades/<int:entidade_id>")
@namespace_analytics.doc(security="Bearer")
class DetalheAnaliticoEntidadeResource(Resource):
    @namespace_analytics.doc(
        params={
            "base_referencia_id": "Identificador obrigatório da Base de Referência.",
            "periodo": "Período mensal obrigatório no formato AAAA-MM.",
        }
    )
    @autenticacao_obrigatoria
    def get(self, entidade_id):
        """Retorna avaliação, indicadores e evolução de uma entidade."""

        from app.servicos.analytics import (
            RecursoAnaliticoNaoEncontradoError,
            obter_detalhe_entidade,
        )

        base_referencia_id = request.args.get("base_referencia_id", type=int)
        periodo = request.args.get("periodo", type=str)
        if base_referencia_id is None or periodo is None:
            namespace_analytics.abort(
                400, "Informe base_referencia_id e periodo para consultar a entidade."
            )
        try:
            return obter_detalhe_entidade(entidade_id, base_referencia_id, periodo)
        except RecursoAnaliticoNaoEncontradoError as erro:
            namespace_analytics.abort(404, str(erro))
        except ValueError as erro:
            namespace_analytics.abort(400, str(erro))
