from datetime import datetime
from decimal import Decimal, ROUND_CEILING

from app.extensoes import banco
from app.modelos import (
    BaseReferencia,
    Entidade,
    Indicador,
    IndicadorBaseReferencia,
    ItemPopulacaoReferencia,
    Observacao,
    ReguaPercentil,
    ValorConsolidadoBase,
)
from app.modelos.entidades import agora_utc
from app.servicos.motor_percentil import construir_regua_percentil, populacao_sem_variabilidade


def contar_meses(periodo_inicial, periodo_final):
    """Conta meses inclusivamente, por exemplo janeiro a março resulta em três."""

    try:
        inicio = datetime.strptime(periodo_inicial, "%Y-%m")
        fim = datetime.strptime(periodo_final, "%Y-%m")
    except ValueError as erro:
        raise ValueError("Períodos devem seguir o formato AAAA-MM.") from erro
    if fim < inicio:
        raise ValueError("O período final não pode ser anterior ao período inicial.")
    return (fim.year - inicio.year) * 12 + fim.month - inicio.month + 1


def calcular_minimo_exigido(periodos_esperados, cobertura_percentual, minimo_absoluto):
    """Aplica teto ao percentual e respeita o mínimo absoluto aprovado."""

    pelo_percentual = (
        Decimal(periodos_esperados) * Decimal(cobertura_percentual) / Decimal("100")
    ).to_integral_value(rounding=ROUND_CEILING)
    return max(int(pelo_percentual), int(minimo_absoluto))


def validar_configuracao_base(base):
    if base.modo == "HISTORICO_ENTIDADE":
        if base.entidade_referencia_id is None:
            raise ValueError("A entidade de referência é obrigatória no modo histórico.")
        entidade = banco.session.get(Entidade, base.entidade_referencia_id)
        if entidade is None or entidade.projeto_id != base.projeto_id:
            raise ValueError("A entidade de referência deve pertencer ao projeto da base.")
    elif base.modo == "ENTRE_ENTIDADES":
        if base.entidade_referencia_id is not None:
            raise ValueError("A entidade de referência deve ser nula no modo entre entidades.")
    else:
        raise ValueError("Modo da Base de Referência inválido.")

    periodos = contar_meses(base.periodo_inicial, base.periodo_final)
    if periodos < base.minimo_observacoes:
        raise ValueError("O período da base não comporta o mínimo absoluto de observações.")
    return periodos


def limpar_processamento_anterior(base):
    """Permite revisar uma base antes da ativação sem deixar resultados antigos."""

    ids_indicadores_base = [
        item.id
        for item in IndicadorBaseReferencia.query.filter_by(base_referencia_id=base.id).all()
    ]
    if ids_indicadores_base:
        ReguaPercentil.query.filter(
            ReguaPercentil.indicador_base_referencia_id.in_(ids_indicadores_base)
        ).delete(synchronize_session=False)
    IndicadorBaseReferencia.query.filter_by(base_referencia_id=base.id).delete()
    ItemPopulacaoReferencia.query.filter_by(base_referencia_id=base.id).delete()
    ValorConsolidadoBase.query.filter_by(base_referencia_id=base.id).delete()


def buscar_observacoes(base, entidade, indicador):
    return (
        Observacao.query.filter_by(
            projeto_id=base.projeto_id,
            entidade_id=entidade.id,
            indicador_id=indicador.id,
        )
        .filter(Observacao.periodo >= base.periodo_inicial)
        .filter(Observacao.periodo <= base.periodo_final)
        .order_by(Observacao.periodo)
        .all()
    )


def processar_base_referencia(base_id):
    """Forma a população, gera as réguas e mantém a base ainda revisável."""

    base = banco.session.get(BaseReferencia, base_id)
    if base is None:
        raise ValueError("Base de Referência não encontrada.")
    if base.status in {"ATIVA", "SUBSTITUIDA"}:
        raise ValueError("Uma base ativada ou substituída é imutável.")

    periodos_esperados = validar_configuracao_base(base)
    minimo_exigido = calcular_minimo_exigido(
        periodos_esperados, base.cobertura_minima_percentual, base.minimo_observacoes
    )
    limpar_processamento_anterior(base)

    if base.modo == "HISTORICO_ENTIDADE":
        entidades = [banco.session.get(Entidade, base.entidade_referencia_id)]
    else:
        entidades = Entidade.query.filter_by(grupo_id=base.grupo_id, ativa=True).all()

    indicadores = Indicador.query.filter_by(projeto_id=base.projeto_id, ativo=True).all()
    entidades_elegiveis = set()

    for indicador in indicadores:
        itens_populacao = []
        for entidade in entidades:
            observacoes = buscar_observacoes(base, entidade, indicador)
            quantidade = len(observacoes)
            cobertura = Decimal(quantidade) / Decimal(periodos_esperados) * Decimal("100")
            elegivel = quantidade >= minimo_exigido
            valores = [Decimal(observacao.valor) for observacao in observacoes]
            media = sum(valores, Decimal("0")) / Decimal(quantidade) if elegivel else None

            consolidado = ValorConsolidadoBase(
                base_referencia_id=base.id,
                entidade_id=entidade.id,
                indicador_id=indicador.id,
                valor_consolidado=media,
                quantidade_observacoes=quantidade,
                quantidade_periodos_esperados=periodos_esperados,
                quantidade_minima_exigida=minimo_exigido,
                percentual_cobertura=cobertura,
                status_cobertura="ELEGIVEL" if elegivel else "INSUFICIENTE",
                primeiro_periodo_encontrado=observacoes[0].periodo if observacoes else None,
                ultimo_periodo_encontrado=observacoes[-1].periodo if observacoes else None,
            )
            banco.session.add(consolidado)

            if not elegivel:
                continue
            entidades_elegiveis.add(entidade.id)
            if base.modo == "ENTRE_ENTIDADES":
                itens_populacao.append((entidade, None, media, "MEDIA_ENTIDADE"))
            else:
                itens_populacao.extend(
                    (entidade, observacao.periodo, Decimal(observacao.valor), "OBSERVACAO_HISTORICA")
                    for observacao in observacoes
                )

        for entidade, periodo, valor, origem in itens_populacao:
            banco.session.add(
                ItemPopulacaoReferencia(
                    base_referencia_id=base.id,
                    indicador_id=indicador.id,
                    entidade_id=entidade.id,
                    periodo=periodo,
                    valor_referencia=valor,
                    origem=origem,
                )
            )

        valores_populacao = [item[2] for item in itens_populacao]
        if not valores_populacao:
            status = "SEM_DADOS"
        elif populacao_sem_variabilidade(valores_populacao):
            status = "SEM_VARIABILIDADE"
        else:
            status = "VALIDO"

        participa = indicador.participa_global_score and status == "VALIDO"
        indicador_base = IndicadorBaseReferencia(
            base_referencia_id=base.id,
            indicador_id=indicador.id,
            status=status,
            participa_global_score=participa,
            peso_aplicado=indicador.peso_percentual if participa else Decimal("0"),
            direcao_aplicada=indicador.direcao,
            tamanho_populacao=len(valores_populacao),
            valor_minimo=min(valores_populacao) if valores_populacao else None,
            valor_maximo=max(valores_populacao) if valores_populacao else None,
        )
        banco.session.add(indicador_base)
        banco.session.flush()

        if status == "VALIDO":
            regua = construir_regua_percentil(valores_populacao, indicador.direcao)
            banco.session.add_all(
                ReguaPercentil(
                    indicador_base_referencia_id=indicador_base.id,
                    percentil=percentil,
                    valor_corte=corte,
                )
                for percentil, corte in regua.items()
            )

    base.quantidade_entidades = len(entidades_elegiveis)
    base.quantidade_indicadores = len(indicadores)
    base.status = "PROCESSADA"
    base.processada_em = agora_utc()
    banco.session.commit()
    return base


def ajustar_pesos_base(base_id, pesos_por_indicador):
    """Permite ao gestor corrigir pesos antes da ativação, sem redistribuição automática."""

    base = banco.session.get(BaseReferencia, base_id)
    if base is None or base.status != "PROCESSADA":
        raise ValueError("Somente uma base processada pode ter pesos ajustados.")

    for indicador_base in IndicadorBaseReferencia.query.filter_by(base_referencia_id=base.id):
        if indicador_base.status != "VALIDO":
            indicador_base.participa_global_score = False
            indicador_base.peso_aplicado = Decimal("0")
            continue
        if indicador_base.indicador_id in pesos_por_indicador:
            peso = Decimal(str(pesos_por_indicador[indicador_base.indicador_id]))
            if peso < 0 or peso > 100:
                raise ValueError("Cada peso deve estar entre 0 e 100.")
            indicador_base.peso_aplicado = peso
            indicador_base.participa_global_score = peso > 0
    banco.session.commit()


def ativar_base_referencia(base_id):
    base = banco.session.get(BaseReferencia, base_id)
    if base is None or base.status != "PROCESSADA":
        raise ValueError("A base precisa estar processada antes da ativação.")

    participantes = IndicadorBaseReferencia.query.filter_by(
        base_referencia_id=base.id, status="VALIDO", participa_global_score=True
    ).all()
    if not participantes:
        raise ValueError("A base precisa de ao menos um indicador válido participante.")
    total = sum((Decimal(item.peso_aplicado) for item in participantes), Decimal("0"))
    if total != Decimal("100"):
        raise ValueError("Os pesos dos indicadores participantes devem totalizar 100%.")

    anteriores = BaseReferencia.query.filter_by(grupo_id=base.grupo_id, status="ATIVA").all()
    for anterior in anteriores:
        anterior.status = "SUBSTITUIDA"
    base.status = "ATIVA"
    base.ativada_em = agora_utc()
    banco.session.commit()
    return base
