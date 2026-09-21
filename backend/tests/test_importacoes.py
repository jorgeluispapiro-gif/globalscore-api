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


def preparar_lote_atipico(cliente, projeto_id, indicador_id):
    """Cria uma amostra simples cujo último valor ultrapassa 3 IQR."""

    valores = [4, 10, 20, 40, 60, 80, 100, 10000]
    linhas = ["codigo;periodo;vendas"] + [
        f"001;{mes:02d}/2026;{valor}" for mes, valor in enumerate(valores, start=1)
    ]
    importacao_id = enviar(
        cliente, projeto_id, "atipico.csv", ("\n".join(linhas) + "\n").encode()
    )
    resposta = cliente.post(
        f"/importacoes/{importacao_id}/validar", json=payload_largo(indicador_id)
    )
    return importacao_id, resposta


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


def test_detecta_valor_atipico_no_lote(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    _, resposta = preparar_lote_atipico(cliente, projeto_id, indicador_id)
    dados = resposta.get_json()
    assert dados["status"] == "VALIDADA_COM_ALERTAS"
    assert dados["quantidade_alertas"] == 1
    assert dados["alertas"][0]["tipo"] == "VALOR_ATIPICO_LOTE"
    assert dados["alertas"][0]["valor"] == "10000"


def test_valor_atipico_e_alerta_e_nao_erro(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    _, resposta = preparar_lote_atipico(cliente, projeto_id, indicador_id)
    dados = resposta.get_json()
    assert dados["quantidade_erros"] == 0
    assert dados["erros"] == []
    assert dados["observacoes_a_criar"] == 8


def test_confirmacao_com_alerta_sem_aceite_retorna_409(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    importacao_id, _ = preparar_lote_atipico(cliente, projeto_id, indicador_id)
    resposta = cliente.post(f"/importacoes/{importacao_id}/confirmar")
    assert resposta.status_code == 409
    assert Observacao.query.count() == 0


def test_aceite_explicito_preserva_valor_atipico(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    importacao_id, _ = preparar_lote_atipico(cliente, projeto_id, indicador_id)
    resposta = cliente.post(
        f"/importacoes/{importacao_id}/confirmar", json={"confirmar_alertas": True}
    )
    assert resposta.status_code == 200
    extrema = Observacao.query.filter_by(importacao_id=importacao_id, periodo="2026-08").one()
    assert float(extrema.valor) == 10000


def test_lote_concluido_sem_uso_pode_ser_anulado(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    importacao_id = enviar(
        cliente, projeto_id, "anular.csv", b"codigo;periodo;vendas\n001;01/2026;100\n"
    )
    cliente.post(f"/importacoes/{importacao_id}/validar", json=payload_largo(indicador_id))
    cliente.post(f"/importacoes/{importacao_id}/confirmar")

    resposta = cliente.post(f"/importacoes/{importacao_id}/anular")
    assert resposta.status_code == 200
    assert resposta.get_json()["status"] == "ANULADA"
    assert resposta.get_json()["observacoes_removidas"] == 1
    assert Observacao.query.filter_by(importacao_id=importacao_id).count() == 0
    assert banco.session.get(Importacao, importacao_id) is not None


def test_lote_usado_por_base_e_avaliacao_nao_pode_ser_anulado(ambiente):
    _, cliente, projeto_id, grupo_id, entidade_id, indicador_id = ambiente
    conteudo = (
        b"codigo;periodo;vendas\n001;01/2026;10\n001;02/2026;20\n"
        b"001;03/2026;30\n001;04/2026;25\n"
    )
    importacao_id = enviar(cliente, projeto_id, "dependencias.csv", conteudo)
    cliente.post(f"/importacoes/{importacao_id}/validar", json=payload_largo(indicador_id))
    assert cliente.post(f"/importacoes/{importacao_id}/confirmar").status_code == 200

    base = cliente.post(
        "/bases",
        json={
            "projeto_id": projeto_id,
            "grupo_id": grupo_id,
            "modo": "HISTORICO_ENTIDADE",
            "entidade_referencia_id": entidade_id,
            "nome": "Base usada pelo lote",
            "versao": 1,
            "periodo_inicial": "2026-01",
            "periodo_final": "2026-03",
            "cobertura_minima_percentual": 50,
        },
    )
    assert base.status_code == 201, base.get_json()
    base_id = base.get_json()["id"]
    assert cliente.post(f"/bases/{base_id}/processar").status_code == 200
    assert cliente.post(f"/bases/{base_id}/ativar").status_code == 200
    avaliacao = cliente.post(
        "/avaliacoes",
        json={
            "entidade_id": entidade_id,
            "base_referencia_id": base_id,
            "periodo": "2026-04",
        },
    )
    assert avaliacao.status_code == 201, avaliacao.get_json()

    resposta = cliente.post(f"/importacoes/{importacao_id}/anular")
    assert resposta.status_code == 409
    dados = resposta.get_json()
    assert dados["quantidade_bases_dependentes"] == 1
    assert dados["quantidade_avaliacoes_dependentes"] == 1
    assert Observacao.query.filter_by(importacao_id=importacao_id).count() == 4
