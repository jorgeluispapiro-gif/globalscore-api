from decimal import Decimal

from app.servicos.motor_percentil import (
    calcular_global_score,
    calcular_percentil_inc,
    construir_regua_percentil,
    pontuar_valor,
    populacao_sem_variabilidade,
)


def test_percentile_inc_faz_interpolacao():
    # Com quatro valores, P30 ocupa a posição 0,9 entre 10 e 20: corte igual a 19.
    resultado = calcular_percentil_inc([10, 20, 30, 40], 30)
    assert resultado == Decimal("19")


def test_maior_melhor_entrega_maior_nivel_atingido():
    regua = construir_regua_percentil([10, 20, 30, 40, 50], "MAIOR_MELHOR")
    assert regua[62] == Decimal("34.8")
    assert regua[63] == Decimal("35.2")
    assert pontuar_valor(35, regua, "MAIOR_MELHOR") == 62


def test_menor_melhor_inverte_a_orientacao_dos_cortes():
    regua = construir_regua_percentil([10, 20, 30, 40, 50], "MENOR_MELHOR")
    assert regua[62] == Decimal("25.2")
    assert regua[63] == Decimal("24.8")
    assert pontuar_valor(25, regua, "MENOR_MELHOR") == 62


def test_populacao_constante_e_sem_variabilidade():
    assert populacao_sem_variabilidade([20, 20, 20, 20]) is True
    assert populacao_sem_variabilidade([20, 20, 21]) is False


def test_global_score_ponderado():
    resultado = calcular_global_score(
        [
            {"pontuacao": 80, "peso": 60},
            {"pontuacao": 50, "peso": 40},
        ]
    )
    assert resultado == Decimal("68")

