import re
from datetime import datetime
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


def gerar_periodos_mensais(periodo_inicial, periodo_final):
    """Valida e expande um intervalo mensal inclusivo no formato AAAA-MM."""

    formato_mensal = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
    if not all(
        isinstance(periodo, str) and formato_mensal.fullmatch(periodo)
        for periodo in (periodo_inicial, periodo_final)
    ):
        raise ValueError("Períodos devem seguir o formato AAAA-MM.")
    try:
        inicio = datetime.strptime(periodo_inicial, "%Y-%m")
        fim = datetime.strptime(periodo_final, "%Y-%m")
    except ValueError as erro:
        raise ValueError("Períodos devem seguir o formato AAAA-MM.") from erro
    if fim < inicio:
        raise ValueError("O período final não pode ser anterior ao período inicial.")

    periodos = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        periodos.append(f"{ano:04d}-{mes:02d}")
        if mes == 12:
            ano, mes = ano + 1, 1
        else:
            mes += 1
    return periodos


def processar_avaliacoes_em_lote(base_id, periodo_inicial, periodo_final):
    """Calcula a matriz entidade x período sem duplicar a matemática individual.

    As validações estruturais ocorrem antes do primeiro cálculo. Cada combinação
    usa ``calcular_avaliacao``, que mantém as mesmas regras de percentis, pesos e
    dados ausentes da avaliação individual.
    """

    base = banco.session.get(BaseReferencia, base_id)
    if base is None:
        raise ValueError("Base de Referência não encontrada.")
    if base.status != "ATIVA":
        raise ValueError("O processamento em lote exige uma Base de Referência ativa.")
    periodos = gerar_periodos_mensais(periodo_inicial, periodo_final)

    if base.modo == "HISTORICO_ENTIDADE":
        entidade = banco.session.get(Entidade, base.entidade_referencia_id)
        entidades = [entidade] if entidade is not None else []
    else:
        entidades = (
            Entidade.query.filter_by(grupo_id=base.grupo_id, ativa=True)
            .order_by(Entidade.id)
            .all()
        )

    resultados = []
    contadores = {
        "quantidade_processadas": 0,
        "quantidade_incompletas": 0,
        "quantidade_ja_existentes": 0,
        "quantidade_erros": 0,
    }

    for entidade in entidades:
        for periodo in periodos:
            existente = Avaliacao.query.filter_by(
                entidade_id=entidade.id,
                periodo=periodo,
                base_referencia_id=base.id,
            ).first()
            if existente is not None:
                contadores["quantidade_ja_existentes"] += 1
                resultados.append(
                    {
                        "entidade_id": entidade.id,
                        "periodo": periodo,
                        "status": "JA_EXISTENTE",
                        "avaliacao_id": existente.id,
                    }
                )
                continue

            try:
                avaliacao = calcular_avaliacao(entidade.id, base.id, periodo)
            except ValueError as erro:
                # O cálculo individual pode ter iniciado uma transação; somente a
                # combinação atual é desfeita, sem afetar commits anteriores.
                banco.session.rollback()
                contadores["quantidade_erros"] += 1
                resultados.append(
                    {
                        "entidade_id": entidade.id,
                        "periodo": periodo,
                        "status": "ERRO",
                        "erro": str(erro),
                    }
                )
                continue

            if avaliacao.status == "INCOMPLETA":
                contadores["quantidade_incompletas"] += 1
            else:
                contadores["quantidade_processadas"] += 1
            resultados.append(
                {
                    "entidade_id": entidade.id,
                    "periodo": periodo,
                    "status": avaliacao.status,
                    "avaliacao_id": avaliacao.id,
                    "global_score": (
                        float(avaliacao.global_score)
                        if avaliacao.global_score is not None
                        else None
                    ),
                }
            )

    return {
        "base_referencia_id": base.id,
        "periodo_inicial": periodo_inicial,
        "periodo_final": periodo_final,
        **contadores,
        "resultados": resultados,
    }


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
