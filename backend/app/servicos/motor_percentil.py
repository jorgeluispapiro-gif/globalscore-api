from decimal import Decimal, ROUND_FLOOR


def para_decimal(valor):
    """Converte entradas numéricas sem introduzir imprecisão binária de float."""

    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


def calcular_percentil_inc(valores, percentil):
    """Reproduz a interpolação inclusiva usada por PERCENTILE.INC.

    O percentil deve estar entre 0 e 100. A função retorna o valor de corte com
    toda a precisão disponível; arredondamentos pertencem apenas à apresentação.
    """

    populacao = sorted(para_decimal(valor) for valor in valores)
    if not populacao:
        raise ValueError("A população não pode estar vazia.")
    if percentil < 0 or percentil > 100:
        raise ValueError("O percentil deve estar entre 0 e 100.")
    if len(populacao) == 1:
        return populacao[0]

    posicao = Decimal(len(populacao) - 1) * Decimal(percentil) / Decimal(100)
    indice_inferior = int(posicao.to_integral_value(rounding=ROUND_FLOOR))
    if indice_inferior == len(populacao) - 1:
        return populacao[-1]

    fracao = posicao - Decimal(indice_inferior)
    inferior = populacao[indice_inferior]
    superior = populacao[indice_inferior + 1]
    return inferior + fracao * (superior - inferior)


def construir_regua_percentil(valores, direcao):
    """Constrói os 101 cortes já orientados ao significado de desempenho."""

    if direcao not in {"MAIOR_MELHOR", "MENOR_MELHOR"}:
        raise ValueError("Direção deve ser MAIOR_MELHOR ou MENOR_MELHOR.")

    regua = {}
    for pontuacao in range(101):
        percentil_populacao = pontuacao if direcao == "MAIOR_MELHOR" else 100 - pontuacao
        regua[pontuacao] = calcular_percentil_inc(valores, percentil_populacao)
    return regua


def pontuar_valor(valor_observado, regua, direcao):
    """Retorna o maior nível inteiro cujo corte foi efetivamente atingido."""

    valor = para_decimal(valor_observado)
    if direcao == "MAIOR_MELHOR":
        niveis = [nivel for nivel, corte in regua.items() if valor >= para_decimal(corte)]
    elif direcao == "MENOR_MELHOR":
        niveis = [nivel for nivel, corte in regua.items() if valor <= para_decimal(corte)]
    else:
        raise ValueError("Direção deve ser MAIOR_MELHOR ou MENOR_MELHOR.")

    # Um desempenho pior que P0 continua limitado ao piso da escala.
    return max(niveis, default=0)


def populacao_sem_variabilidade(valores):
    """Indica se a população existe, mas não diferencia desempenhos."""

    populacao = [para_decimal(valor) for valor in valores]
    return bool(populacao) and min(populacao) == max(populacao)


def calcular_global_score(itens):
    """Calcula a soma ponderada sem normalizar pesos automaticamente.

    Cada item deve conter `pontuacao` e `peso`. A soma dos pesos precisa ser
    exatamente 100, conforme a regra aprovada para transparência do cálculo.
    """

    pesos = [para_decimal(item["peso"]) for item in itens]
    if sum(pesos, Decimal("0")) != Decimal("100"):
        raise ValueError("Os pesos dos indicadores participantes devem totalizar 100%.")

    return sum(
        para_decimal(item["pontuacao"]) * para_decimal(item["peso"]) / Decimal("100")
        for item in itens
    )

