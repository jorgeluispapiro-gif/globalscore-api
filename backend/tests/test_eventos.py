import pytest

from app import criar_aplicacao
from app.configuracao import ConfiguracaoTeste
from app.extensoes import banco
from app.modelos import Entidade, GrupoComparavel, Projeto


@pytest.fixture(scope="module")
def aplicacao_eventos(tmp_path_factory):
    diretorio_temporario = tmp_path_factory.mktemp("eventos")

    class ConfiguracaoEventosTeste(ConfiguracaoTeste):
        DIRETORIO_IMPORTACOES_TEMPORARIAS = diretorio_temporario

    return criar_aplicacao(ConfiguracaoEventosTeste)


@pytest.fixture
def ambiente_eventos(aplicacao_eventos):
    aplicacao = aplicacao_eventos

    with aplicacao.app_context():
        banco.drop_all()
        banco.create_all()
        projeto = Projeto(nome="Projeto principal")
        outro_projeto = Projeto(nome="Outro projeto")
        banco.session.add_all([projeto, outro_projeto])
        banco.session.flush()
        grupo = GrupoComparavel(projeto_id=projeto.id, nome="Filiais")
        outro_grupo = GrupoComparavel(projeto_id=outro_projeto.id, nome="Outras filiais")
        banco.session.add_all([grupo, outro_grupo])
        banco.session.flush()
        entidade = Entidade(
            projeto_id=projeto.id,
            grupo_id=grupo.id,
            codigo="001",
            nome="Unidade Centro",
        )
        outra_entidade = Entidade(
            projeto_id=outro_projeto.id,
            grupo_id=outro_grupo.id,
            codigo="002",
            nome="Unidade externa",
        )
        banco.session.add_all([entidade, outra_entidade])
        banco.session.commit()
        yield {
            "cliente": aplicacao.test_client(),
            "projeto_id": projeto.id,
            "entidade_id": entidade.id,
            "outra_entidade_id": outra_entidade.id,
        }


def _criar_evento(ambiente, periodo="2026-03", titulo="Treinamento"):
    return ambiente["cliente"].post(
        "/eventos",
        json={
            "projeto_id": ambiente["projeto_id"],
            "entidade_id": ambiente["entidade_id"],
            "periodo": periodo,
            "titulo": titulo,
            "descricao": "Capacitação da equipe.",
        },
    )


def test_cria_e_lista_eventos_da_entidade(ambiente_eventos):
    resposta_criacao = _criar_evento(ambiente_eventos)

    resposta_lista = ambiente_eventos["cliente"].get(
        "/eventos",
        query_string={
            "projeto_id": ambiente_eventos["projeto_id"],
            "entidade_id": ambiente_eventos["entidade_id"],
        },
    )

    assert resposta_criacao.status_code == 201
    assert resposta_lista.status_code == 200
    assert [(item["periodo"], item["titulo"]) for item in resposta_lista.get_json()] == [
        ("2026-03", "Treinamento")
    ]


def test_edita_e_exclui_evento(ambiente_eventos):
    evento_id = _criar_evento(ambiente_eventos).get_json()["id"]

    resposta_edicao = ambiente_eventos["cliente"].patch(
        f"/eventos/{evento_id}",
        json={"periodo": "2026-04", "titulo": "Novo procedimento"},
    )
    resposta_exclusao = ambiente_eventos["cliente"].delete(f"/eventos/{evento_id}")
    resposta_lista = ambiente_eventos["cliente"].get(
        "/eventos",
        query_string={
            "projeto_id": ambiente_eventos["projeto_id"],
            "entidade_id": ambiente_eventos["entidade_id"],
        },
    )

    assert resposta_edicao.status_code == 200
    assert resposta_edicao.get_json()["periodo"] == "2026-04"
    assert resposta_edicao.get_json()["titulo"] == "Novo procedimento"
    assert resposta_exclusao.status_code == 204
    assert resposta_lista.get_json() == []


def test_rejeita_entidade_de_outro_projeto_e_periodo_invalido(ambiente_eventos):
    resposta_vinculo = ambiente_eventos["cliente"].post(
        "/eventos",
        json={
            "projeto_id": ambiente_eventos["projeto_id"],
            "entidade_id": ambiente_eventos["outra_entidade_id"],
            "periodo": "2026-03",
            "titulo": "Evento incompatível",
        },
    )
    resposta_periodo = _criar_evento(ambiente_eventos, periodo="03/2026")

    assert resposta_vinculo.status_code == 400
    assert resposta_vinculo.get_json()["message"] == (
        "A entidade deve pertencer ao projeto informado."
    )
    assert resposta_periodo.status_code == 400
    assert resposta_periodo.get_json()["message"] == (
        "O período deve seguir o formato AAAA-MM."
    )
