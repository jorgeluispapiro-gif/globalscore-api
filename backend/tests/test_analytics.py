from decimal import Decimal

import pytest

from app import criar_aplicacao
from app.configuracao import ConfiguracaoTeste
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


@pytest.fixture(scope="module")
def aplicacao_analytics(tmp_path_factory):
    diretorio_temporario = tmp_path_factory.mktemp("analytics")

    class ConfiguracaoAnalyticsTeste(ConfiguracaoTeste):
        DIRETORIO_IMPORTACOES_TEMPORARIAS = diretorio_temporario

    return criar_aplicacao(ConfiguracaoAnalyticsTeste)


@pytest.fixture
def ambiente_analitico(aplicacao_analytics):
    aplicacao = aplicacao_analytics
    with aplicacao.app_context():
        banco.drop_all()
        banco.create_all()
        projeto = Projeto(nome="Projeto analítico")
        banco.session.add(projeto)
        banco.session.flush()
        grupo = GrupoComparavel(projeto_id=projeto.id, nome="Filiais")
        banco.session.add(grupo)
        banco.session.flush()
        entidades = [
            Entidade(
                projeto_id=projeto.id,
                grupo_id=grupo.id,
                codigo=f"00{indice}",
                nome=nome,
            )
            for indice, nome in enumerate(
                ["Unidade Centro", "Unidade Norte", "Unidade Sul"], start=1
            )
        ]
        indicadores = [
            Indicador(
                projeto_id=projeto.id,
                codigo="VENDAS",
                nome="Vendas",
                unidade_medida="R$",
                direcao="MAIOR_MELHOR",
                peso_percentual=60,
            ),
            Indicador(
                projeto_id=projeto.id,
                codigo="PRAZO",
                nome="Prazo médio",
                unidade_medida="dias",
                direcao="MENOR_MELHOR",
                peso_percentual=40,
            ),
        ]
        banco.session.add_all(entidades + indicadores)
        banco.session.flush()
        base = BaseReferencia(
            projeto_id=projeto.id,
            grupo_id=grupo.id,
            modo="ENTRE_ENTIDADES",
            nome="Base vigente",
            versao=1,
            periodo_inicial="2025-01",
            periodo_final="2025-12",
            status="ATIVA",
        )
        banco.session.add(base)
        banco.session.flush()

        avaliacao_centro_janeiro = _criar_avaliacao(
            projeto.id, grupo.id, base.id, entidades[0].id, "2026-01", "70"
        )
        avaliacao_norte_janeiro = _criar_avaliacao(
            projeto.id, grupo.id, base.id, entidades[1].id, "2026-01", "90"
        )
        _criar_avaliacao(
            projeto.id,
            grupo.id,
            base.id,
            entidades[2].id,
            "2026-01",
            None,
            status="INCOMPLETA",
        )
        avaliacao_centro_fevereiro = _criar_avaliacao(
            projeto.id, grupo.id, base.id, entidades[0].id, "2026-02", "76"
        )
        banco.session.flush()
        banco.session.add_all(
            [
                ItemAvaliacao(
                    avaliacao_id=avaliacao_centro_fevereiro.id,
                    indicador_id=indicadores[0].id,
                    valor_observado=Decimal("1250"),
                    pontuacao_percentil=80,
                    peso_aplicado=Decimal("60"),
                    contribuicao_score=Decimal("48"),
                    direcao_aplicada="MAIOR_MELHOR",
                ),
                ItemAvaliacao(
                    avaliacao_id=avaliacao_centro_fevereiro.id,
                    indicador_id=indicadores[1].id,
                    valor_observado=Decimal("4.5"),
                    pontuacao_percentil=70,
                    peso_aplicado=Decimal("40"),
                    contribuicao_score=Decimal("28"),
                    direcao_aplicada="MENOR_MELHOR",
                ),
            ]
        )
        banco.session.commit()
        yield {
            "cliente": aplicacao.test_client(),
            "projeto_id": projeto.id,
            "grupo_id": grupo.id,
            "base_id": base.id,
            "entidades": [entidade.id for entidade in entidades],
            "avaliacao_centro_janeiro_id": avaliacao_centro_janeiro.id,
            "avaliacao_norte_janeiro_id": avaliacao_norte_janeiro.id,
        }


def _criar_avaliacao(
    projeto_id, grupo_id, base_id, entidade_id, periodo, global_score, status="CALCULADA"
):
    avaliacao = Avaliacao(
        projeto_id=projeto_id,
        grupo_id=grupo_id,
        base_referencia_id=base_id,
        entidade_id=entidade_id,
        periodo=periodo,
        global_score=Decimal(global_score) if global_score is not None else None,
        status=status,
        pesos_total=Decimal("100"),
    )
    banco.session.add(avaliacao)
    return avaliacao


def test_overview_ordena_ranking_e_separa_incompletas(ambiente_analitico):
    resposta = ambiente_analitico["cliente"].get(
        "/analytics/overview",
        query_string={
            "projeto_id": ambiente_analitico["projeto_id"],
            "grupo_id": ambiente_analitico["grupo_id"],
            "periodo": "2026-01",
        },
    )

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados["base_referencia_id"] == ambiente_analitico["base_id"]
    assert dados["base_referencia"]["nome"] == "Base vigente"
    assert dados["quantidade_entidades"] == 3
    assert dados["quantidade_avaliadas"] == 2
    assert dados["quantidade_incompletas"] == 1
    assert [item["entidade_nome"] for item in dados["ranking"]] == [
        "Unidade Norte",
        "Unidade Centro",
    ]
    assert [item["posicao"] for item in dados["ranking"]] == [1, 2]
    assert [item["global_score"] for item in dados["ranking"]] == [90.0, 70.0]


def test_overview_informa_entidade_de_referencia_da_base_historica(ambiente_analitico):
    grupo = GrupoComparavel(
        projeto_id=ambiente_analitico["projeto_id"], nome="Acompanhamento individual"
    )
    banco.session.add(grupo)
    banco.session.flush()
    entidade = Entidade(
        projeto_id=ambiente_analitico["projeto_id"],
        grupo_id=grupo.id,
        codigo="HIST-001",
        nome="Unidade histórica",
    )
    banco.session.add(entidade)
    banco.session.flush()
    base = BaseReferencia(
        projeto_id=ambiente_analitico["projeto_id"],
        grupo_id=grupo.id,
        modo="HISTORICO_ENTIDADE",
        entidade_referencia_id=entidade.id,
        nome="Histórico próprio",
        versao=1,
        periodo_inicial="2025-01",
        periodo_final="2025-12",
        status="ATIVA",
    )
    banco.session.add(base)
    banco.session.commit()

    resposta = ambiente_analitico["cliente"].get(
        "/analytics/overview",
        query_string={
            "projeto_id": ambiente_analitico["projeto_id"],
            "grupo_id": grupo.id,
            "periodo": "2026-01",
        },
    )

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados["base_referencia"]["entidade_referencia_id"] == entidade.id
    assert dados["ranking"] == []


def test_detalhe_retorna_indicadores_e_evolucao_cronologica(ambiente_analitico):
    resposta = ambiente_analitico["cliente"].get(
        f"/analytics/entidades/{ambiente_analitico['entidades'][0]}",
        query_string={
            "base_referencia_id": ambiente_analitico["base_id"],
            "periodo": "2026-02",
        },
    )

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados["avaliacao"]["global_score"] == 76.0
    assert [item["periodo"] for item in dados["evolucao"]] == ["2026-01", "2026-02"]
    assert [item["global_score"] for item in dados["evolucao"]] == [70.0, 76.0]
    assert dados["indicadores"] == [
        {
            "indicador_id": dados["indicadores"][0]["indicador_id"],
            "indicador_nome": "Prazo médio",
            "valor_observado": 4.5,
            "pontuacao_percentil": 70,
            "peso_aplicado": 40.0,
            "contribuicao_score": 28.0,
        },
        {
            "indicador_id": dados["indicadores"][1]["indicador_id"],
            "indicador_nome": "Vendas",
            "valor_observado": 1250.0,
            "pontuacao_percentil": 80,
            "peso_aplicado": 60.0,
            "contribuicao_score": 48.0,
        },
    ]


def test_periodo_sem_resultado_retorna_estado_vazio_previsivel(ambiente_analitico):
    overview = ambiente_analitico["cliente"].get(
        "/analytics/overview",
        query_string={
            "projeto_id": ambiente_analitico["projeto_id"],
            "grupo_id": ambiente_analitico["grupo_id"],
            "periodo": "2026-03",
        },
    ).get_json()
    detalhe = ambiente_analitico["cliente"].get(
        f"/analytics/entidades/{ambiente_analitico['entidades'][1]}",
        query_string={
            "base_referencia_id": ambiente_analitico["base_id"],
            "periodo": "2026-02",
        },
    )

    assert overview["quantidade_avaliadas"] == 0
    assert overview["quantidade_incompletas"] == 0
    assert overview["ranking"] == []
    assert detalhe.status_code == 200
    dados_detalhe = detalhe.get_json()
    assert dados_detalhe["avaliacao"] is None
    assert dados_detalhe["indicadores"] == []
    assert dados_detalhe["evolucao"] == [
        {
            "periodo": "2026-01",
            "global_score": 90.0,
            "status": "CALCULADA",
            "eventos": [],
        }
    ]


def test_entidade_inexistente_retorna_404(ambiente_analitico):
    resposta = ambiente_analitico["cliente"].get(
        "/analytics/entidades/99999",
        query_string={
            "base_referencia_id": ambiente_analitico["base_id"],
            "periodo": "2026-01",
        },
    )

    assert resposta.status_code == 404
    assert resposta.get_json()["message"] == "Entidade não encontrada."


def test_detalhe_relaciona_eventos_com_a_evolucao(ambiente_analitico):
    banco.session.add_all(
        [
            Evento(
                projeto_id=ambiente_analitico["projeto_id"],
                entidade_id=ambiente_analitico["entidades"][0],
                periodo="2026-02",
                titulo="Treinamento da equipe",
            ),
            Evento(
                projeto_id=ambiente_analitico["projeto_id"],
                entidade_id=ambiente_analitico["entidades"][0],
                periodo="2026-03",
                titulo="Mudança operacional",
            ),
        ]
    )
    banco.session.commit()

    resposta = ambiente_analitico["cliente"].get(
        f"/analytics/entidades/{ambiente_analitico['entidades'][0]}",
        query_string={
            "base_referencia_id": ambiente_analitico["base_id"],
            "periodo": "2026-02",
        },
    )

    dados = resposta.get_json()
    assert resposta.status_code == 200
    assert [evento["periodo"] for evento in dados["eventos"]] == ["2026-02", "2026-03"]
    assert dados["evolucao"][1]["eventos"][0]["titulo"] == "Treinamento da equipe"
    assert dados["evolucao"][0]["eventos"] == []


def test_leitura_do_periodo_informa_variacao_e_tendencia(ambiente_analitico):
    resposta = ambiente_analitico["cliente"].get(
        f"/analytics/entidades/{ambiente_analitico['entidades'][0]}",
        query_string={
            "base_referencia_id": ambiente_analitico["base_id"],
            "periodo": "2026-02",
        },
    )

    assert resposta.get_json()["leitura_periodo"] == {
        "global_score_atual": 76.0,
        "global_score_anterior": 70.0,
        "variacao_absoluta": 6.0,
        "tendencia": "SUBIU",
    }


def test_primeiro_periodo_nao_possui_comparacao_anterior(ambiente_analitico):
    resposta = ambiente_analitico["cliente"].get(
        f"/analytics/entidades/{ambiente_analitico['entidades'][0]}",
        query_string={
            "base_referencia_id": ambiente_analitico["base_id"],
            "periodo": "2026-01",
        },
    )

    assert resposta.get_json()["leitura_periodo"] == {
        "global_score_atual": 70.0,
        "global_score_anterior": None,
        "variacao_absoluta": None,
        "tendencia": "SEM_COMPARACAO",
    }
