"""Cinco testes essenciais do fluxo de importação assistida."""

import io
import json
from datetime import timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from app import criar_aplicacao
from app.configuracao import ConfiguracaoTeste
from app.extensoes import banco
from app.modelos import (
    Avaliacao,
    BaseReferencia,
    Entidade,
    GrupoComparavel,
    Importacao,
    Indicador,
    Observacao,
    PerfilImportacao,
    Projeto,
)
from app.servicos import importacoes as servico_importacoes
from app.servicos import autenticacao as servico_autenticacao


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


def enviar(cliente, projeto_id, nome, conteudo, headers=None):
    resposta = cliente.post(
        "/importacoes",
        headers=headers,
        data={"projeto_id": str(projeto_id), "arquivo": (io.BytesIO(conteudo), nome)},
        content_type="multipart/form-data",
    )
    assert resposta.status_code == 201, resposta.get_json()
    return resposta.get_json()["id"]


def criar_grupo_pela_api(cliente, projeto_id, nome, ativo=True):
    """Cria grupos nos testes pelo mesmo contrato REST usado pelo frontend."""

    return cliente.post(
        "/grupos",
        json={"projeto_id": projeto_id, "nome": nome, "ativo": ativo},
    )


def test_grupo_criacao_normal_e_mesmo_nome_em_projetos_diferentes(ambiente):
    _, cliente, projeto_id, *_ = ambiente
    outro_projeto = cliente.post("/projetos", json={"nome": "Outro projeto"}).get_json()
    assert criar_grupo_pela_api(cliente, projeto_id, "Unidades Operacionais").status_code == 201
    assert criar_grupo_pela_api(cliente, outro_projeto["id"], "Unidades Operacionais").status_code == 201


def test_grupo_post_rejeita_nome_equivalente_no_mesmo_projeto(ambiente):
    _, cliente, projeto_id, *_ = ambiente
    assert criar_grupo_pela_api(cliente, projeto_id, "Unidades Operacionais").status_code == 201
    resposta = criar_grupo_pela_api(cliente, projeto_id, "  unidades operacionais  ")
    assert resposta.status_code == 409
    assert resposta.get_json()["message"] == "Já existe um grupo com esse nome neste projeto."


def test_grupo_inativo_tambem_impede_duplicidade(ambiente):
    _, cliente, projeto_id, *_ = ambiente
    assert criar_grupo_pela_api(cliente, projeto_id, "Histórico", ativo=False).status_code == 201
    assert criar_grupo_pela_api(cliente, projeto_id, "histórico").status_code == 409


def test_grupo_patch_rejeita_nome_de_outro_grupo(ambiente):
    _, cliente, projeto_id, *_ = ambiente
    primeiro = criar_grupo_pela_api(cliente, projeto_id, "Grupo A").get_json()
    segundo = criar_grupo_pela_api(cliente, projeto_id, "Grupo B").get_json()
    resposta = cliente.patch(f"/grupos/{segundo['id']}", json={"nome": " grupo a "})
    assert resposta.status_code == 409
    assert resposta.get_json()["message"] == "Já existe um grupo com esse nome neste projeto."
    assert cliente.get(f"/grupos/{primeiro['id']}").status_code == 200


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


def cabecalho_autenticacao(token="token-valido"):
    return {"Authorization": f"Bearer {token}"}


def ativar_autenticacao_de_teste(aplicacao, monkeypatch):
    """Ativa a proteção apenas no teste atual e simula a API externa."""

    monkeypatch.setitem(aplicacao.config, "AUTENTICACAO_OBRIGATORIA", True)
    monkeypatch.setattr(
        servico_autenticacao,
        "validar_token_supabase",
        lambda _token: {"id": "usuario-supabase-123", "email": "usuario@email.com"},
    )


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


def test_rota_protegida_sem_token_retorna_401(ambiente, monkeypatch):
    aplicacao, cliente, *_ = ambiente
    ativar_autenticacao_de_teste(aplicacao, monkeypatch)
    resposta = cliente.get("/importacoes/1")
    assert resposta.status_code == 401


def test_token_invalido_retorna_401(ambiente, monkeypatch):
    aplicacao, cliente, *_ = ambiente
    monkeypatch.setitem(aplicacao.config, "AUTENTICACAO_OBRIGATORIA", True)

    def rejeitar_token(_token):
        raise servico_autenticacao.CredenciaisInvalidasError(
            "Token de autenticação inválido."
        )

    monkeypatch.setattr(servico_autenticacao, "validar_token_supabase", rejeitar_token)
    resposta = cliente.get(
        "/autenticacao/me", headers=cabecalho_autenticacao("invalido")
    )
    assert resposta.status_code == 401


def test_token_valido_permite_acesso_a_rota_protegida(ambiente, monkeypatch):
    aplicacao, cliente, *_ = ambiente
    ativar_autenticacao_de_teste(aplicacao, monkeypatch)
    # O 404 demonstra que a autenticação passou e a rota procurou o lote solicitado.
    resposta = cliente.get("/importacoes/999", headers=cabecalho_autenticacao())
    assert resposta.status_code == 404
    assert resposta.get_json()["message"] == "Importação não encontrada."


def test_me_retorna_usuario_autenticado(ambiente, monkeypatch):
    aplicacao, cliente, *_ = ambiente
    ativar_autenticacao_de_teste(aplicacao, monkeypatch)
    resposta = cliente.get("/autenticacao/me", headers=cabecalho_autenticacao())
    assert resposta.status_code == 200
    assert resposta.get_json() == {
        "id": "usuario-supabase-123",
        "email": "usuario@email.com",
    }


def test_indisponibilidade_do_supabase_retorna_503(ambiente, monkeypatch):
    aplicacao, cliente, *_ = ambiente
    monkeypatch.setitem(aplicacao.config, "AUTENTICACAO_OBRIGATORIA", True)

    def simular_indisponibilidade(_token):
        raise servico_autenticacao.ServicoAutenticacaoIndisponivelError(
            "O serviço de autenticação está temporariamente indisponível."
        )

    monkeypatch.setattr(
        servico_autenticacao, "validar_token_supabase", simular_indisponibilidade
    )
    resposta = cliente.get("/autenticacao/me", headers=cabecalho_autenticacao())
    assert resposta.status_code == 503


def test_importacao_usa_id_autenticado_como_criado_por(ambiente, monkeypatch):
    aplicacao, cliente, projeto_id, *_ = ambiente
    ativar_autenticacao_de_teste(aplicacao, monkeypatch)
    resposta = cliente.post(
        "/importacoes",
        headers=cabecalho_autenticacao(),
        data={
            "projeto_id": str(projeto_id),
            # O campo forjado não pertence mais ao contrato e deve ser ignorado.
            "criado_por": "identidade-forjada",
            "arquivo": (
                io.BytesIO(b"codigo;periodo;valor\n001;01/2026;10\n"),
                "dados.csv",
            ),
        },
        content_type="multipart/form-data",
    )
    assert resposta.status_code == 201, resposta.get_json()
    importacao = banco.session.get(Importacao, resposta.get_json()["id"])
    assert importacao.criado_por == "usuario-supabase-123"


def test_lista_importacoes_retomaveis_filtra_status_projeto_usuario_e_ordena(
    ambiente, monkeypatch
):
    aplicacao, cliente, projeto_id, *_ = ambiente
    ativar_autenticacao_de_teste(aplicacao, monkeypatch)
    primeiro = cliente.post(
        "/importacoes",
        headers=cabecalho_autenticacao(),
        data={
            "projeto_id": str(projeto_id),
            "arquivo": (io.BytesIO(b"codigo;periodo\n001;01/2026\n"), "primeiro.csv"),
        },
        content_type="multipart/form-data",
    ).get_json()
    segundo = cliente.post(
        "/importacoes",
        headers=cabecalho_autenticacao(),
        data={
            "projeto_id": str(projeto_id),
            "arquivo": (io.BytesIO(b"codigo;periodo\n001;02/2026\n"), "segundo.csv"),
        },
        content_type="multipart/form-data",
    ).get_json()
    lote_primeiro = banco.session.get(Importacao, primeiro["id"])
    lote_segundo = banco.session.get(Importacao, segundo["id"])
    lote_primeiro.status = "VALIDADA"
    lote_primeiro.criado_em = lote_segundo.criado_em - timedelta(minutes=1)
    projeto_outro = Projeto(nome="Outro projeto")
    banco.session.add(projeto_outro)
    banco.session.flush()
    banco.session.add_all(
        [
            Importacao(projeto_id=projeto_id, nome_arquivo_original="concluida.csv", tipo_arquivo="CSV", hash_sha256="a" * 64, status="CONCLUIDA", criado_por="usuario-supabase-123"),
            Importacao(projeto_id=projeto_id, nome_arquivo_original="anulada.csv", tipo_arquivo="CSV", hash_sha256="b" * 64, status="ANULADA", criado_por="usuario-supabase-123"),
            Importacao(projeto_id=projeto_id, nome_arquivo_original="cancelada.csv", tipo_arquivo="CSV", hash_sha256="c" * 64, status="CANCELADA", criado_por="usuario-supabase-123"),
            Importacao(projeto_id=projeto_id, nome_arquivo_original="falha.csv", tipo_arquivo="CSV", hash_sha256="d" * 64, status="FALHA", criado_por="usuario-supabase-123"),
            Importacao(projeto_id=projeto_id, nome_arquivo_original="outro-usuario.csv", tipo_arquivo="CSV", hash_sha256="e" * 64, status="ANALISADA", criado_por="outro-usuario"),
            Importacao(projeto_id=projeto_outro.id, nome_arquivo_original="outro-projeto.csv", tipo_arquivo="CSV", hash_sha256="f" * 64, status="ANALISADA", criado_por="usuario-supabase-123"),
        ]
    )
    banco.session.commit()

    resposta = cliente.get(
        f"/importacoes?projeto_id={projeto_id}&pendentes=true",
        headers=cabecalho_autenticacao(),
    )
    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert [item["id"] for item in dados] == [segundo["id"], primeiro["id"]]
    assert all("caminho_arquivo_temporario" not in item for item in dados)


def test_lista_importacoes_exige_projeto(ambiente, monkeypatch):
    aplicacao, cliente, *_ = ambiente
    ativar_autenticacao_de_teste(aplicacao, monkeypatch)
    resposta = cliente.get("/importacoes", headers=cabecalho_autenticacao())
    assert resposta.status_code == 400


def test_inspecao_retomada_csv_e_xlsx_reutiliza_leitor(ambiente, monkeypatch):
    aplicacao, cliente, projeto_id, *_ = ambiente
    ativar_autenticacao_de_teste(aplicacao, monkeypatch)
    csv_id = cliente.post(
        "/importacoes",
        headers=cabecalho_autenticacao(),
        data={"projeto_id": str(projeto_id), "arquivo": (io.BytesIO(b"codigo;periodo\n001;01/2026\n"), "dados.csv")},
        content_type="multipart/form-data",
    ).get_json()["id"]
    livro = Workbook()
    livro.active.append(["codigo", "periodo"])
    livro.active.append(["001", "01/2026"])
    arquivo_xlsx = io.BytesIO()
    livro.save(arquivo_xlsx)
    arquivo_xlsx.seek(0)
    xlsx_id = cliente.post(
        "/importacoes",
        headers=cabecalho_autenticacao(),
        data={"projeto_id": str(projeto_id), "arquivo": (arquivo_xlsx, "dados.xlsx")},
        content_type="multipart/form-data",
    ).get_json()["id"]

    resposta_csv = cliente.get(f"/importacoes/{csv_id}/inspecao", headers=cabecalho_autenticacao())
    resposta_xlsx = cliente.get(f"/importacoes/{xlsx_id}/inspecao", headers=cabecalho_autenticacao())
    assert resposta_csv.status_code == 200
    assert resposta_csv.get_json()["preview"][0] == ["codigo", "periodo"]
    assert resposta_xlsx.status_code == 200
    assert resposta_xlsx.get_json()["abas"][0]["preview"][0] == ["codigo", "periodo"]
    assert "caminho_arquivo_temporario" not in resposta_csv.get_json()


def test_inspecao_sem_arquivo_retorna_409_tratado(ambiente, monkeypatch):
    aplicacao, cliente, projeto_id, *_ = ambiente
    ativar_autenticacao_de_teste(aplicacao, monkeypatch)
    importacao_id = cliente.post(
        "/importacoes",
        headers=cabecalho_autenticacao(),
        data={"projeto_id": str(projeto_id), "arquivo": (io.BytesIO(b"codigo\n001\n"), "dados.csv")},
        content_type="multipart/form-data",
    ).get_json()["id"]
    importacao = banco.session.get(Importacao, importacao_id)
    Path(importacao.caminho_arquivo_temporario).unlink()

    resposta = cliente.get(f"/importacoes/{importacao_id}/inspecao", headers=cabecalho_autenticacao())
    assert resposta.status_code == 409
    assert resposta.get_json()["message"] == "O arquivo temporário desta importação não está mais disponível para retomada."


def test_usuario_diferente_nao_pode_reinspecionar_lote(ambiente, monkeypatch):
    aplicacao, cliente, projeto_id, *_ = ambiente
    monkeypatch.setitem(aplicacao.config, "AUTENTICACAO_OBRIGATORIA", True)
    monkeypatch.setattr(
        servico_autenticacao,
        "validar_token_supabase",
        lambda token: {"id": token, "email": f"{token}@email.com"},
    )
    importacao_id = cliente.post(
        "/importacoes",
        headers=cabecalho_autenticacao("usuario-a"),
        data={"projeto_id": str(projeto_id), "arquivo": (io.BytesIO(b"codigo\n001\n"), "dados.csv")},
        content_type="multipart/form-data",
    ).get_json()["id"]
    resposta = cliente.get(
        f"/importacoes/{importacao_id}/inspecao",
        headers=cabecalho_autenticacao("usuario-b"),
    )
    assert resposta.status_code == 404


def test_rotas_do_lote_respeitam_proprietario_e_preservam_dados(
    ambiente, monkeypatch
):
    """Um usuário não pode consultar nem alterar o lote criado por outro."""

    aplicacao, cliente, projeto_id, _, _, indicador_id = ambiente
    monkeypatch.setitem(aplicacao.config, "AUTENTICACAO_OBRIGATORIA", True)
    monkeypatch.setattr(
        servico_autenticacao,
        "validar_token_supabase",
        lambda token: {"id": token, "email": f"{token}@email.com"},
    )
    cabecalho_usuario_a = cabecalho_autenticacao("usuario-a")
    cabecalho_usuario_b = cabecalho_autenticacao("usuario-b")
    resposta_upload = cliente.post(
        "/importacoes",
        headers=cabecalho_usuario_a,
        data={
            "projeto_id": str(projeto_id),
            "arquivo": (
                io.BytesIO(b"codigo;periodo;vendas\n001;01/2026;100\n"),
                "dados-privados.csv",
            ),
        },
        content_type="multipart/form-data",
    )
    assert resposta_upload.status_code == 201
    importacao_id = resposta_upload.get_json()["id"]

    chamadas_usuario_b = [
        cliente.get(f"/importacoes/{importacao_id}", headers=cabecalho_usuario_b),
        cliente.get(
            f"/importacoes/{importacao_id}/inspecao", headers=cabecalho_usuario_b
        ),
        cliente.post(
            f"/importacoes/{importacao_id}/validar",
            headers=cabecalho_usuario_b,
            json=payload_largo(indicador_id),
        ),
        cliente.post(
            f"/importacoes/{importacao_id}/confirmar", headers=cabecalho_usuario_b
        ),
        cliente.get(
            f"/importacoes/{importacao_id}/observacoes", headers=cabecalho_usuario_b
        ),
        cliente.post(
            f"/importacoes/{importacao_id}/anular", headers=cabecalho_usuario_b
        ),
    ]
    assert all(resposta.status_code == 404 for resposta in chamadas_usuario_b)
    mensagens = [resposta.get_json()["message"] for resposta in chamadas_usuario_b]
    assert mensagens == ["Importação não encontrada."] * 6

    # As tentativas alheias não mudam o lote nem criam observações.
    importacao = banco.session.get(Importacao, importacao_id)
    assert importacao.status == "ANALISADA"
    assert Observacao.query.filter_by(importacao_id=importacao_id).count() == 0

    # O proprietário continua usando todas as operações quando o estado permite.
    assert cliente.get(
        f"/importacoes/{importacao_id}", headers=cabecalho_usuario_a
    ).status_code == 200
    assert cliente.get(
        f"/importacoes/{importacao_id}/inspecao", headers=cabecalho_usuario_a
    ).status_code == 200
    assert cliente.post(
        f"/importacoes/{importacao_id}/validar",
        headers=cabecalho_usuario_a,
        json=payload_largo(indicador_id),
    ).status_code == 200
    assert cliente.post(
        f"/importacoes/{importacao_id}/confirmar", headers=cabecalho_usuario_a
    ).status_code == 200
    observacoes = cliente.get(
        f"/importacoes/{importacao_id}/observacoes", headers=cabecalho_usuario_a
    )
    assert observacoes.status_code == 200
    assert len(observacoes.get_json()) == 1
    anulacao = cliente.post(
        f"/importacoes/{importacao_id}/anular", headers=cabecalho_usuario_a
    )
    assert anulacao.status_code == 200
    assert anulacao.get_json()["status"] == "ANULADA"


def test_validacao_preserva_fotografia_estrutural_dos_cabecalhos(ambiente):
    """A estrutura sobrevive ao arquivo e não inclui valores das linhas."""

    _, cliente, projeto_id, _, _, indicador_id = ambiente
    importacao_id = enviar(
        cliente,
        projeto_id,
        "estrutura.csv",
        "codigo;periodo;  Produtividade   MÉDIA  \n001;01/2026;10\n".encode(),
    )
    resposta = cliente.post(
        f"/importacoes/{importacao_id}/validar", json=payload_largo(indicador_id)
    )
    assert resposta.status_code == 200

    importacao = banco.session.get(Importacao, importacao_id)
    estrutura = json.loads(importacao.estrutura_json)
    assert estrutura["tipo_arquivo"] == "CSV"
    assert estrutura["cabecalhos"][2] == {
        "indice_coluna": 3,
        "nome_original": "Produtividade   MÉDIA",
        "nome_normalizado": "produtividade média",
    }
    assert "10" not in importacao.estrutura_json


def test_perfil_converte_indicador_criado_em_existente(ambiente):
    """O perfil aponta para o cadastro criado na confirmação, sem repetir CRIAR."""

    _, cliente, projeto_id, *_ = ambiente
    importacao_id = enviar(
        cliente,
        projeto_id,
        "novo-indicador.csv",
        b"codigo;periodo;qualidade\n001;01/2026;95\n",
    )
    payload = payload_largo(0)
    payload["mapeamento"]["colunas"][2]["indicador"] = {
        "acao": "CRIAR",
        "dados": {
            "codigo": "QUALIDADE",
            "nome": "Qualidade",
            "unidade_medida": "%",
            "direcao": "MAIOR_MELHOR",
            "peso_percentual": 0,
            "obrigatorio": False,
            "participa_global_score": False,
        },
    }
    assert cliente.post(
        f"/importacoes/{importacao_id}/validar", json=payload
    ).status_code == 200
    assert cliente.post(f"/importacoes/{importacao_id}/confirmar").status_code == 200

    resposta = cliente.post(
        "/perfis-importacao",
        json={"importacao_id": importacao_id, "nome": "Perfil mensal"},
    )
    assert resposta.status_code == 201, resposta.get_json()
    perfil = PerfilImportacao.query.one()
    indicador = Indicador.query.filter_by(projeto_id=projeto_id, codigo="QUALIDADE").one()
    mapeamento = json.loads(perfil.mapeamento_json)
    assert mapeamento["colunas"][2]["indicador"] == {
        "acao": "EXISTENTE",
        "indicador_id": indicador.id,
    }
    assert len(perfil.assinatura_estrutura) == 64
    lista = cliente.get(f"/perfis-importacao?projeto_id={projeto_id}")
    assert lista.status_code == 200
    assert [item["id"] for item in lista.get_json()] == [perfil.id]


def test_outro_usuario_nao_cria_perfil_de_lote_alheio(ambiente, monkeypatch):
    """A inexistência aparente do lote protege sua propriedade entre usuários."""

    aplicacao, cliente, projeto_id, _, _, indicador_id = ambiente
    monkeypatch.setitem(aplicacao.config, "AUTENTICACAO_OBRIGATORIA", True)
    monkeypatch.setattr(
        servico_autenticacao,
        "validar_token_supabase",
        lambda token: {"id": token, "email": f"{token}@email.com"},
    )
    usuario_a = cabecalho_autenticacao("usuario-a")
    importacao_id = cliente.post(
        "/importacoes",
        headers=usuario_a,
        data={
            "projeto_id": str(projeto_id),
            "arquivo": (
                io.BytesIO(b"codigo;periodo;vendas\n001;01/2026;100\n"),
                "privado.csv",
            ),
        },
        content_type="multipart/form-data",
    ).get_json()["id"]
    assert cliente.post(
        f"/importacoes/{importacao_id}/validar",
        headers=usuario_a,
        json=payload_largo(indicador_id),
    ).status_code == 200
    assert cliente.post(
        f"/importacoes/{importacao_id}/confirmar", headers=usuario_a
    ).status_code == 200

    resposta = cliente.post(
        "/perfis-importacao",
        headers=cabecalho_autenticacao("usuario-b"),
        json={"importacao_id": importacao_id, "nome": "Perfil indevido"},
    )
    assert resposta.status_code == 404
    assert resposta.get_json()["message"] == "Importação não encontrada."
    assert PerfilImportacao.query.count() == 0


def criar_perfil_existente(
    cliente, projeto_id, indicador_id, headers=None, nome="Perfil mensal"
):
    """Prepara um perfil real pelo fluxo público usado pelo gestor."""

    importacao_id = enviar(
        cliente,
        projeto_id,
        "origem-perfil.csv",
        b"codigo;periodo;vendas\n001;01/2026;100\n",
        headers=headers,
    )
    assert cliente.post(
        f"/importacoes/{importacao_id}/validar",
        headers=headers,
        json=payload_largo(indicador_id),
    ).status_code == 200
    assert cliente.post(
        f"/importacoes/{importacao_id}/confirmar", headers=headers
    ).status_code == 200
    resposta = cliente.post(
        "/perfis-importacao",
        headers=headers,
        json={"importacao_id": importacao_id, "nome": nome},
    )
    assert resposta.status_code == 201, resposta.get_json()
    return resposta.get_json()["id"]


def test_mesma_estrutura_reconhece_e_aplica_perfil_sem_criar_negocio(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    perfil_id = criar_perfil_existente(cliente, projeto_id, indicador_id)
    quantidades_antes = (
        Entidade.query.count(),
        Indicador.query.count(),
        Observacao.query.count(),
        Avaliacao.query.count(),
        BaseReferencia.query.count(),
    )
    nova_importacao_id = enviar(
        cliente,
        projeto_id,
        "novo-lote.csv",
        b"codigo;periodo;vendas\n001;02/2026;110\n",
    )

    reconhecimento = cliente.post(
        f"/importacoes/{nova_importacao_id}/reconhecer-perfil"
    )
    assert reconhecimento.status_code == 200
    assert reconhecimento.get_json() == {
        "resultado": "COMPATIVEL",
        "perfil_sugerido": {
            "id": perfil_id,
            "nome": "Perfil mensal",
            "versao": 1,
        },
    }
    aplicacao = cliente.post(
        f"/importacoes/{nova_importacao_id}/aplicar-perfil",
        json={"perfil_id": perfil_id},
    )
    assert aplicacao.status_code == 200, aplicacao.get_json()
    lote = banco.session.get(Importacao, nova_importacao_id)
    perfil = banco.session.get(PerfilImportacao, perfil_id)
    assert lote.perfil_importacao_id == perfil.id
    assert lote.configuracao_leitura_json == perfil.configuracao_leitura_json
    assert lote.mapeamento_json == perfil.mapeamento_json
    assert quantidades_antes == (
        Entidade.query.count(),
        Indicador.query.count(),
        Observacao.query.count(),
        Avaliacao.query.count(),
        BaseReferencia.query.count(),
    )


def test_coluna_extra_nao_e_compativel_com_perfil(ambiente):
    _, cliente, projeto_id, _, _, indicador_id = ambiente
    criar_perfil_existente(cliente, projeto_id, indicador_id)
    nova_importacao_id = enviar(
        cliente,
        projeto_id,
        "coluna-extra.csv",
        b"codigo;periodo;vendas;qualidade\n001;02/2026;110;90\n",
    )

    resposta = cliente.post(
        f"/importacoes/{nova_importacao_id}/reconhecer-perfil"
    )
    assert resposta.status_code == 200
    assert resposta.get_json() == {
        "resultado": "INCOMPATIVEL",
        "perfil_sugerido": None,
    }


def test_perfil_alheio_e_referencia_inativa_nao_podem_ser_aplicados(
    ambiente, monkeypatch
):
    aplicacao, cliente, projeto_id, _, _, indicador_id = ambiente
    monkeypatch.setitem(aplicacao.config, "AUTENTICACAO_OBRIGATORIA", True)
    monkeypatch.setattr(
        servico_autenticacao,
        "validar_token_supabase",
        lambda token: {"id": token, "email": f"{token}@email.com"},
    )
    usuario_a = cabecalho_autenticacao("usuario-a")
    usuario_b = cabecalho_autenticacao("usuario-b")
    perfil_id = criar_perfil_existente(
        cliente, projeto_id, indicador_id, headers=usuario_a
    )
    lote_usuario_b = enviar(
        cliente,
        projeto_id,
        "lote-b.csv",
        b"codigo;periodo;vendas\n001;02/2026;110\n",
        headers=usuario_b,
    )
    resposta_alheia = cliente.post(
        f"/importacoes/{lote_usuario_b}/aplicar-perfil",
        headers=usuario_b,
        json={"perfil_id": perfil_id},
    )
    assert resposta_alheia.status_code == 404
    assert resposta_alheia.get_json()["message"] == (
        "Perfil de importação não encontrado."
    )

    lote_usuario_a = enviar(
        cliente,
        projeto_id,
        "lote-a.csv",
        b"codigo;periodo;vendas\n001;03/2026;120\n",
        headers=usuario_a,
    )
    indicador = banco.session.get(Indicador, indicador_id)
    indicador.ativo = False
    banco.session.commit()
    resposta_inativa = cliente.post(
        f"/importacoes/{lote_usuario_a}/aplicar-perfil",
        headers=usuario_a,
        json={"perfil_id": perfil_id},
    )
    assert resposta_inativa.status_code == 409
    assert "precisa ser revisado" in resposta_inativa.get_json()["message"]
    assert banco.session.get(Importacao, lote_usuario_a).perfil_importacao_id is None
