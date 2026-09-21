"""Cinco testes essenciais do fluxo de importação assistida."""

import io

import pytest
from openpyxl import Workbook

from app import criar_aplicacao
from app.configuracao import ConfiguracaoTeste
from app.extensoes import banco
from app.modelos import Entidade, GrupoComparavel, Importacao, Indicador, Observacao, Projeto
from app.servicos import importacoes as servico_importacoes


@pytest.fixture(scope="module")
def aplicacao_base(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("importacoes")

    class ConfiguracaoImportacaoTeste(ConfiguracaoTeste):
        DIRETORIO_IMPORTACOES_TEMPORARIAS = tmp_path

    return criar_aplicacao(ConfiguracaoImportacaoTeste)


@pytest.fixture
def ambiente(aplicacao_base):
    aplicacao = aplicacao_base
    with aplicacao.app_context():
        banco.drop_all()
        banco.create_all()
        projeto = Projeto(nome="Projeto de teste")
        banco.session.add(projeto)
        banco.session.flush()
        grupo = GrupoComparavel(projeto_id=projeto.id, nome="Grupo")
        banco.session.add(grupo)
        banco.session.flush()
        entidade = Entidade(
            projeto_id=projeto.id, grupo_id=grupo.id, codigo="001", nome="Unidade 001"
        )
        indicador = Indicador(
            projeto_id=projeto.id,
            codigo="VENDAS",
            nome="Vendas",
            unidade_medida="R$",
            direcao="MAIOR_MELHOR",
            peso_percentual=100,
        )
        banco.session.add_all([entidade, indicador])
        banco.session.commit()
        yield aplicacao, aplicacao.test_client(), projeto.id, grupo.id, entidade.id, indicador.id


def payload_largo(indicador_id, *, linha_final=None):
    configuracao = {
        "linha_inicial": 2,
        "linhas_cabecalho": [1],
        "colunas_utilizadas": [1, 2, 3],
        "delimitador": ";",
        "formato_periodo": "MM/AAAA",
        "formato_numerico": {"separador_decimal": ",", "separador_milhar": "."},
    }
    if linha_final:
        configuracao["linha_final"] = linha_final
    return {
        "configuracao_leitura": configuracao,
        "mapeamento": {
            "formato": "LARGO",
            "colunas": [
                {"indice_coluna": 1, "papel": "CODIGO_ENTIDADE"},
                {"indice_coluna": 2, "papel": "PERIODO"},
                {
                    "indice_coluna": 3,
                    "papel": "VALOR_INDICADOR",
                    "indicador": {"acao": "EXISTENTE", "indicador_id": indicador_id},
                },
            ],
        },
    }


def enviar(cliente, projeto_id, nome, conteudo):
    resposta = cliente.post(
        "/importacoes",
        data={"projeto_id": str(projeto_id), "arquivo": (io.BytesIO(conteudo), nome)},
        content_type="multipart/form-data",
    )
    assert resposta.status_code == 201, resposta.get_json()
    return resposta.get_json()["id"]


def test_csv_valido_cria_observacao(ambiente):
    _, cliente, projeto_id, _, entidade_id, indicador_id = ambiente
    importacao_id = enviar(cliente, projeto_id, "dados.csv", b"codigo;periodo;vendas\n001;01/2026;1.234,56\n")
    validacao = cliente.post(f"/importacoes/{importacao_id}/validar", json=payload_largo(indicador_id))
    assert validacao.status_code == 200
    assert validacao.get_json()["observacoes_a_criar"] == 1
    assert cliente.post(f"/importacoes/{importacao_id}/confirmar").status_code == 200
    observacao = Observacao.query.filter_by(entidade_id=entidade_id, indicador_id=indicador_id).one()
    assert float(observacao.valor) == 1234.56


def test_xlsx_valido_cria_observacao(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    livro = Workbook()
    planilha = livro.active
    planilha.title = "Dados"
    planilha.append(["codigo", "periodo", "vendas"])
    planilha.append(["001", "02/2026", 200])
    arquivo = io.BytesIO()
    livro.save(arquivo)
    importacao_id = enviar(cliente, projeto_id, "dados.xlsx", arquivo.getvalue())
    payload = payload_largo(indicador_id)
    payload["configuracao_leitura"].pop("delimitador")
    payload["configuracao_leitura"]["aba"] = "Dados"
    assert cliente.post(f"/importacoes/{importacao_id}/validar", json=payload).get_json()["status"] == "VALIDADA"
    assert cliente.post(f"/importacoes/{importacao_id}/confirmar").status_code == 200


@pytest.mark.parametrize(
    ("percentual_como", "valor_esperado"),
    [("FRACAO", 0.15), ("NUMERO", 15.0)],
)
def test_xlsx_respeita_formato_percentual(ambiente, percentual_como, valor_esperado):
    _, cliente, projeto_id, _, entidade_id, indicador_id = ambiente
    livro = Workbook()
    planilha = livro.active
    planilha.title = "Dados"
    planilha.append(["codigo", "periodo", "taxa"])
    planilha.append(["001", "03/2026", 0.15])
    planilha["C2"].number_format = "0%"
    arquivo = io.BytesIO()
    livro.save(arquivo)

    importacao_id = enviar(cliente, projeto_id, "percentual.xlsx", arquivo.getvalue())
    payload = payload_largo(indicador_id)
    payload["configuracao_leitura"].pop("delimitador")
    payload["configuracao_leitura"]["aba"] = "Dados"
    payload["configuracao_leitura"]["formato_numerico"]["percentual_como"] = percentual_como
    assert cliente.post(f"/importacoes/{importacao_id}/validar", json=payload).get_json()["status"] == "VALIDADA"
    assert cliente.post(f"/importacoes/{importacao_id}/confirmar").status_code == 200

    observacao = Observacao.query.filter_by(entidade_id=entidade_id, indicador_id=indicador_id).one()
    assert float(observacao.valor) == valor_esperado


def test_xlsx_preserva_zero_a_esquerda_no_codigo(ambiente):
    _, cliente, projeto_id, _, entidade_id, indicador_id = ambiente
    livro = Workbook()
    planilha = livro.active
    planilha.title = "Dados"
    planilha.append(["codigo", "periodo", "vendas"])
    planilha.append([1, "04/2026", 300])
    planilha["A2"].number_format = "000"
    arquivo = io.BytesIO()
    livro.save(arquivo)

    importacao_id = enviar(cliente, projeto_id, "codigo.xlsx", arquivo.getvalue())
    payload = payload_largo(indicador_id)
    payload["configuracao_leitura"].pop("delimitador")
    payload["configuracao_leitura"]["aba"] = "Dados"
    validacao = cliente.post(f"/importacoes/{importacao_id}/validar", json=payload)
    assert validacao.get_json()["status"] == "VALIDADA"
    assert validacao.get_json()["entidades_reconhecidas"] == 1
    assert cliente.post(f"/importacoes/{importacao_id}/confirmar").status_code == 200

    observacao = Observacao.query.filter_by(entidade_id=entidade_id, indicador_id=indicador_id).one()
    assert float(observacao.valor) == 300


def test_celula_vazia_nao_vira_zero(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    importacao_id = enviar(cliente, projeto_id, "vazio.csv", b"codigo;periodo;vendas\n001;01/2026;\n")
    validacao = cliente.post(f"/importacoes/{importacao_id}/validar", json=payload_largo(indicador_id))
    assert validacao.get_json()["observacoes_a_criar"] == 0
    assert cliente.post(f"/importacoes/{importacao_id}/confirmar").status_code == 200
    assert Observacao.query.count() == 0


def test_duplicidade_nao_sobrescreve_observacao(ambiente):
    _, cliente, projeto_id, _, entidade_id, indicador_id = ambiente
    banco.session.add(
        Observacao(
            projeto_id=projeto_id,
            entidade_id=entidade_id,
            indicador_id=indicador_id,
            periodo="2026-01",
            valor=999,
        )
    )
    banco.session.commit()
    importacao_id = enviar(cliente, projeto_id, "duplicado.csv", b"codigo;periodo;vendas\n001;01/2026;100\n")
    resposta = cliente.post(f"/importacoes/{importacao_id}/validar", json=payload_largo(indicador_id))
    assert resposta.get_json()["problemas"][0]["tipo"] == "DUPLICIDADE"
    assert cliente.post(f"/importacoes/{importacao_id}/confirmar").status_code == 400
    assert float(Observacao.query.one().valor) == 999


def test_erro_na_confirmacao_desfaz_todo_o_lote(ambiente, monkeypatch):
    aplicacao, cliente, projeto_id, _, _, indicador_id = ambiente
    conteudo = b"codigo;periodo;vendas\n001;01/2026;100\n001;02/2026;200\n"
    importacao_id = enviar(cliente, projeto_id, "rollback.csv", conteudo)
    assert cliente.post(f"/importacoes/{importacao_id}/validar", json=payload_largo(indicador_id)).get_json()["status"] == "VALIDADA"

    original = servico_importacoes.persistir_observacao
    chamadas = {"quantidade": 0}

    def falhar_na_segunda(**dados):
        chamadas["quantidade"] += 1
        if chamadas["quantidade"] == 2:
            raise RuntimeError("falha simulada")
        original(**dados)

    monkeypatch.setattr(servico_importacoes, "persistir_observacao", falhar_na_segunda)
    with aplicacao.app_context(), pytest.raises(RuntimeError):
        servico_importacoes.confirmar_importacao(importacao_id)
    assert Observacao.query.count() == 0
    assert banco.session.get(Importacao, importacao_id).status == "FALHA"
