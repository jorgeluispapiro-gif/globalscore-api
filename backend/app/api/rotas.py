from decimal import Decimal

from flask import request
from flask_restx import Namespace, Resource, fields

from app.extensoes import banco
from app.modelos import (
    Avaliacao,
    BaseReferencia,
    Entidade,
    GrupoComparavel,
    Indicador,
    IndicadorBaseReferencia,
    Observacao,
    Projeto,
)
from app.servicos.avaliacoes import calcular_avaliacao
from app.servicos.bases_referencia import (
    ajustar_pesos_base,
    ativar_base_referencia,
    processar_base_referencia,
    validar_configuracao_base,
)


namespace_sistema = Namespace("sistema", description="Estado da aplicação")
namespace_indicadores = Namespace("indicadores", description="CRUD de indicadores")
namespace_observacoes = Namespace("observacoes", description="Entrada manual de valores")
namespace_bases = Namespace("bases", description="Construção e ativação das bases")
namespace_avaliacoes = Namespace("avaliacoes", description="Cálculo do Global Score")


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


def base_para_dict(base):
    indicadores = IndicadorBaseReferencia.query.filter_by(base_referencia_id=base.id).all()
    return {
        "id": base.id,
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
        banco.session.commit()
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
        banco.session.commit()
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
        banco.session.commit()
        return {"id": observacao.id}, 201


@namespace_bases.route("")
class BasesResource(Resource):
    def get(self):
        return [base_para_dict(item) for item in BaseReferencia.query.order_by(BaseReferencia.id).all()]

    @namespace_bases.expect(modelo_base, validate=True)
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
class BaseResource(Resource):
    def get(self, base_id):
        base = banco.session.get(BaseReferencia, base_id)
        if base is None:
            namespace_bases.abort(404, "Base de Referência não encontrada.")
        return base_para_dict(base)


@namespace_bases.route("/<int:base_id>/processar")
class ProcessarBaseResource(Resource):
    def post(self, base_id):
        try:
            return base_para_dict(processar_base_referencia(base_id))
        except ValueError as erro:
            banco.session.rollback()
            namespace_bases.abort(400, str(erro))


@namespace_bases.route("/<int:base_id>/pesos")
class PesosBaseResource(Resource):
    @namespace_bases.expect(modelo_pesos, validate=True)
    def patch(self, base_id):
        pesos = {int(chave): valor for chave, valor in request.json["pesos"].items()}
        try:
            ajustar_pesos_base(base_id, pesos)
            return base_para_dict(banco.session.get(BaseReferencia, base_id))
        except ValueError as erro:
            banco.session.rollback()
            namespace_bases.abort(400, str(erro))


@namespace_bases.route("/<int:base_id>/ativar")
class AtivarBaseResource(Resource):
    def post(self, base_id):
        try:
            return base_para_dict(ativar_base_referencia(base_id))
        except ValueError as erro:
            banco.session.rollback()
            namespace_bases.abort(400, str(erro))


@namespace_avaliacoes.route("")
class AvaliacoesResource(Resource):
    @namespace_avaliacoes.expect(modelo_avaliacao, validate=True)
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


@namespace_avaliacoes.route("/<int:avaliacao_id>")
class AvaliacaoResource(Resource):
    def get(self, avaliacao_id):
        avaliacao = banco.session.get(Avaliacao, avaliacao_id)
        if avaliacao is None:
            namespace_avaliacoes.abort(404, "Avaliação não encontrada.")
        return avaliacao_para_dict(avaliacao)
