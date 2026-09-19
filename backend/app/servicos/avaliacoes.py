from decimal import Decimal

from app.extensoes import banco
from app.modelos import (
    Avaliacao,
    BaseReferencia,
    Entidade,
    IndicadorBaseReferencia,
    ItemAvaliacao,
    Observacao,
    ReguaPercentil,
)
from app.servicos.motor_percentil import calcular_global_score, pontuar_valor


def calcular_avaliacao(entidade_id, base_id, periodo):
    """Avalia uma entidade e persiste uma memória completa do cálculo."""

    entidade = banco.session.get(Entidade, entidade_id)
    base = banco.session.get(BaseReferencia, base_id)
    if entidade is None or base is None:
        raise ValueError("Entidade ou Base de Referência não encontrada.")
    if base.status != "ATIVA":
        raise ValueError("A avaliação exige uma Base de Referência ativa.")
    if entidade.projeto_id != base.projeto_id or entidade.grupo_id != base.grupo_id:
        raise ValueError("A entidade deve pertencer ao projeto e ao grupo da base.")
    if base.modo == "HISTORICO_ENTIDADE" and entidade.id != base.entidade_referencia_id:
        raise ValueError("A base histórica só pode avaliar sua entidade de referência.")

    existente = Avaliacao.query.filter_by(
        entidade_id=entidade.id, periodo=periodo, base_referencia_id=base.id
    ).first()
    if existente:
        raise ValueError("Já existe uma avaliação dessa entidade, período e base.")

    indicadores_base = IndicadorBaseReferencia.query.filter_by(
        base_referencia_id=base.id, status="VALIDO", participa_global_score=True
    ).all()
    total_pesos = sum(
        (Decimal(item.peso_aplicado) for item in indicadores_base), Decimal("0")
    )
    if not indicadores_base or total_pesos != Decimal("100"):
        raise ValueError("A base ativa não possui pesos válidos totalizando 100%.")

    avaliacao = Avaliacao(
        projeto_id=base.projeto_id,
        entidade_id=entidade.id,
        grupo_id=base.grupo_id,
        base_referencia_id=base.id,
        periodo=periodo,
        status="CALCULADA",
        pesos_total=total_pesos,
    )
    banco.session.add(avaliacao)
    banco.session.flush()

    itens_calculo = []
    for indicador_base in indicadores_base:
        observacao = Observacao.query.filter_by(
            entidade_id=entidade.id,
            indicador_id=indicador_base.indicador_id,
            periodo=periodo,
        ).first()
        if observacao is None:
            avaliacao.status = "INCOMPLETA"
            avaliacao.global_score = None
            banco.session.add(
                ItemAvaliacao(
                    avaliacao_id=avaliacao.id,
                    indicador_id=indicador_base.indicador_id,
                    valor_observado=None,
                    pontuacao_percentil=None,
                    peso_aplicado=indicador_base.peso_aplicado,
                    contribuicao_score=None,
                    direcao_aplicada=indicador_base.direcao_aplicada,
                )
            )
            continue

        regua = {
            item.percentil: Decimal(item.valor_corte)
            for item in ReguaPercentil.query.filter_by(
                indicador_base_referencia_id=indicador_base.id
            ).all()
        }
        pontuacao = pontuar_valor(
            observacao.valor, regua, indicador_base.direcao_aplicada
        )
        contribuicao = (
            Decimal(pontuacao) * Decimal(indicador_base.peso_aplicado) / Decimal("100")
        )
        itens_calculo.append(
            {"pontuacao": pontuacao, "peso": indicador_base.peso_aplicado}
        )
        banco.session.add(
            ItemAvaliacao(
                avaliacao_id=avaliacao.id,
                indicador_id=indicador_base.indicador_id,
                valor_observado=observacao.valor,
                pontuacao_percentil=pontuacao,
                peso_aplicado=indicador_base.peso_aplicado,
                contribuicao_score=contribuicao,
                direcao_aplicada=indicador_base.direcao_aplicada,
            )
        )

    if avaliacao.status == "CALCULADA":
        avaliacao.global_score = calcular_global_score(itens_calculo)

    banco.session.commit()
    return avaliacao
