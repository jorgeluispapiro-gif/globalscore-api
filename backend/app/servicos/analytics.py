import re

from app.extensoes import banco
from app.modelos import (
    Avaliacao,
    BaseReferencia,
    Entidade,
    Evento,
    GrupoComparavel,
    Indicador,
    ItemAvaliacao,
    Projeto,
)


class RecursoAnaliticoNaoEncontradoError(ValueError):
    """Diferencia ausência de cadastro de um parâmetro analítico inválido."""


def _validar_periodo(periodo):
    """Garante a periodicidade mensal aprovada sem iniciar qualquer cálculo."""

    if not isinstance(periodo, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", periodo):
        raise ValueError("O período deve seguir o formato AAAA-MM.")


def _base_para_resumo(base):
    if base is None:
        return None
    return {
        "id": base.id,
        "nome": base.nome,
        "versao": base.versao,
        "modo": base.modo,
        "entidade_referencia_id": base.entidade_referencia_id,
        "periodo_inicial": base.periodo_inicial,
        "periodo_final": base.periodo_final,
    }


def _evento_para_resumo(evento):
    """Prepara o fato registrado sem produzir interpretação causal."""

    return {
        "id": evento.id,
        "projeto_id": evento.projeto_id,
        "entidade_id": evento.entidade_id,
        "periodo": evento.periodo,
        "titulo": evento.titulo,
        "descricao": evento.descricao,
        "criado_em": evento.criado_em.isoformat(),
    }


def _obter_leitura_periodo(entidade_id, base_id, periodo, avaliacao_atual):
    """Compara resultados persistidos da mesma Base, sem recalcular pontuações."""

    score_atual = None
    if (
        avaliacao_atual is not None
        and avaliacao_atual.status == "CALCULADA"
        and avaliacao_atual.global_score is not None
    ):
        score_atual = avaliacao_atual.global_score

    avaliacao_anterior = None
    if score_atual is not None:
        avaliacao_anterior = (
            Avaliacao.query.filter(
                Avaliacao.entidade_id == entidade_id,
                Avaliacao.base_referencia_id == base_id,
                Avaliacao.periodo < periodo,
                Avaliacao.status == "CALCULADA",
                Avaliacao.global_score.is_not(None),
            )
            .order_by(Avaliacao.periodo.desc(), Avaliacao.id.desc())
            .first()
        )

    score_anterior = (
        avaliacao_anterior.global_score if avaliacao_anterior is not None else None
    )
    variacao = (
        score_atual - score_anterior
        if score_atual is not None and score_anterior is not None
        else None
    )
    tendencia = "SEM_COMPARACAO"
    if variacao is not None:
        if variacao > 0:
            tendencia = "SUBIU"
        elif variacao < 0:
            tendencia = "CAIU"
        else:
            tendencia = "ESTAVEL"

    return {
        "global_score_atual": float(score_atual) if score_atual is not None else None,
        "global_score_anterior": (
            float(score_anterior) if score_anterior is not None else None
        ),
        "variacao_absoluta": float(variacao) if variacao is not None else None,
        "tendencia": tendencia,
    }


def obter_visao_geral(projeto_id, grupo_id, periodo):
    """Entrega a fotografia gerencial de um grupo em um período já avaliado.

    A consulta apenas organiza resultados persistidos. Entidades sem avaliação não
    recebem resultado inventado, e avaliações incompletas não entram no ranking.
    """

    _validar_periodo(periodo)
    projeto = banco.session.get(Projeto, projeto_id)
    grupo = banco.session.get(GrupoComparavel, grupo_id)
    if projeto is None:
        raise RecursoAnaliticoNaoEncontradoError("Projeto não encontrado.")
    if grupo is None:
        raise RecursoAnaliticoNaoEncontradoError("Grupo comparável não encontrado.")
    if grupo.projeto_id != projeto.id:
        raise ValueError("O grupo deve pertencer ao projeto informado.")

    base = BaseReferencia.query.filter_by(
        projeto_id=projeto.id,
        grupo_id=grupo.id,
        status="ATIVA",
    ).first()
    quantidade_entidades = Entidade.query.filter_by(
        projeto_id=projeto.id,
        grupo_id=grupo.id,
    ).count()

    if base is None:
        return {
            "projeto_id": projeto.id,
            "grupo_id": grupo.id,
            "periodo": periodo,
            "base_referencia_id": None,
            "base_referencia": None,
            "quantidade_entidades": quantidade_entidades,
            "quantidade_avaliadas": 0,
            "quantidade_incompletas": 0,
            "ranking": [],
        }

    avaliacoes = (
        banco.session.query(Avaliacao, Entidade)
        .join(Entidade, Entidade.id == Avaliacao.entidade_id)
        .filter(
            Avaliacao.projeto_id == projeto.id,
            Avaliacao.grupo_id == grupo.id,
            Avaliacao.base_referencia_id == base.id,
            Avaliacao.periodo == periodo,
        )
        .all()
    )
    calculadas = [
        (avaliacao, entidade)
        for avaliacao, entidade in avaliacoes
        if avaliacao.status == "CALCULADA" and avaliacao.global_score is not None
    ]
    calculadas.sort(
        key=lambda item: (-item[0].global_score, item[1].nome.casefold(), item[1].id)
    )

    # Bases históricas acompanham uma única entidade e, por regra, não geram ranking.
    ranking = []
    if base.modo == "ENTRE_ENTIDADES":
        ranking = [
            {
                "posicao": posicao,
                "entidade_id": entidade.id,
                "entidade_nome": entidade.nome,
                "global_score": float(avaliacao.global_score),
                "status": avaliacao.status,
            }
            for posicao, (avaliacao, entidade) in enumerate(calculadas, start=1)
        ]

    return {
        "projeto_id": projeto.id,
        "grupo_id": grupo.id,
        "periodo": periodo,
        "base_referencia_id": base.id,
        "base_referencia": _base_para_resumo(base),
        "quantidade_entidades": quantidade_entidades,
        "quantidade_avaliadas": len(calculadas),
        "quantidade_incompletas": sum(
            1 for avaliacao, _entidade in avaliacoes if avaliacao.status == "INCOMPLETA"
        ),
        "ranking": ranking,
    }


def obter_detalhe_entidade(entidade_id, base_referencia_id, periodo):
    """Reúne avaliação, eventos e evolução factual sem recompor resultados."""

    _validar_periodo(periodo)
    entidade = banco.session.get(Entidade, entidade_id)
    base = banco.session.get(BaseReferencia, base_referencia_id)
    if entidade is None:
        raise RecursoAnaliticoNaoEncontradoError("Entidade não encontrada.")
    if base is None:
        raise RecursoAnaliticoNaoEncontradoError("Base de Referência não encontrada.")
    if entidade.projeto_id != base.projeto_id or entidade.grupo_id != base.grupo_id:
        raise ValueError("A entidade deve pertencer ao projeto e ao grupo da base.")
    if base.modo == "HISTORICO_ENTIDADE" and entidade.id != base.entidade_referencia_id:
        raise ValueError("A base histórica só pode analisar sua entidade de referência.")

    avaliacao = Avaliacao.query.filter_by(
        entidade_id=entidade.id,
        base_referencia_id=base.id,
        periodo=periodo,
    ).first()
    indicadores = []
    resumo_avaliacao = None
    if avaliacao is not None:
        resumo_avaliacao = {
            "id": avaliacao.id,
            "status": avaliacao.status,
            "global_score": (
                float(avaliacao.global_score) if avaliacao.global_score is not None else None
            ),
        }
        itens = (
            banco.session.query(ItemAvaliacao, Indicador)
            .join(Indicador, Indicador.id == ItemAvaliacao.indicador_id)
            .filter(ItemAvaliacao.avaliacao_id == avaliacao.id)
            .order_by(Indicador.nome, Indicador.id)
            .all()
        )
        indicadores = [
            {
                "indicador_id": indicador.id,
                "indicador_nome": indicador.nome,
                "valor_observado": (
                    float(item.valor_observado) if item.valor_observado is not None else None
                ),
                "pontuacao_percentil": item.pontuacao_percentil,
                "peso_aplicado": float(item.peso_aplicado),
                "contribuicao_score": (
                    float(item.contribuicao_score)
                    if item.contribuicao_score is not None
                    else None
                ),
            }
            for item, indicador in itens
        ]

    eventos = [
        _evento_para_resumo(item)
        for item in Evento.query.filter_by(
            projeto_id=entidade.projeto_id,
            entidade_id=entidade.id,
        )
        .order_by(Evento.periodo, Evento.criado_em, Evento.id)
        .all()
    ]
    eventos_por_periodo = {}
    for evento in eventos:
        eventos_por_periodo.setdefault(evento["periodo"], []).append(evento)

    avaliacoes_evolucao = (
        Avaliacao.query.filter_by(
            entidade_id=entidade.id,
            base_referencia_id=base.id,
        )
        .order_by(Avaliacao.periodo, Avaliacao.id)
        .all()
    )
    evolucao = [
        {
            "periodo": item.periodo,
            "global_score": float(item.global_score) if item.global_score is not None else None,
            "status": item.status,
            "eventos": eventos_por_periodo.get(item.periodo, []),
        }
        for item in avaliacoes_evolucao
    ]

    return {
        "projeto_id": entidade.projeto_id,
        "grupo_id": entidade.grupo_id,
        "entidade_id": entidade.id,
        "entidade_nome": entidade.nome,
        "base_referencia_id": base.id,
        "base_referencia": _base_para_resumo(base),
        "periodo": periodo,
        "avaliacao": resumo_avaliacao,
        "leitura_periodo": _obter_leitura_periodo(
            entidade.id, base.id, periodo, avaliacao
        ),
        "indicadores": indicadores,
        "evolucao": evolucao,
        "eventos": eventos,
    }
