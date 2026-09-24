from pathlib import Path

from flask import Flask
from sqlalchemy import inspect, text
from werkzeug.exceptions import RequestEntityTooLarge

from app.api import api, registrar_namespaces
from app.configuracao import Configuracao
from app.extensoes import banco


def _aplicar_ajustes_aditivos_de_schema():
    """Acrescenta colunas novas sem recriar nem apagar o SQLite do gestor."""

    inspetor = inspect(banco.engine)
    if "importacoes" not in inspetor.get_table_names():
        return
    colunas = {item["name"] for item in inspetor.get_columns("importacoes")}
    comandos = []
    if "quantidade_alertas" not in colunas:
        comandos.append(
            "ALTER TABLE importacoes ADD COLUMN quantidade_alertas INTEGER NOT NULL DEFAULT 0"
        )
    if "resumo_alertas_json" not in colunas:
        comandos.append("ALTER TABLE importacoes ADD COLUMN resumo_alertas_json TEXT")
    if "estrutura_json" not in colunas:
        comandos.append("ALTER TABLE importacoes ADD COLUMN estrutura_json TEXT")
    if "perfil_importacao_id" not in colunas:
        comandos.append(
            "ALTER TABLE importacoes ADD COLUMN perfil_importacao_id INTEGER "
            "REFERENCES perfis_importacao(id)"
        )
    if comandos:
        with banco.engine.begin() as conexao:
            for comando in comandos:
                conexao.execute(text(comando))


def criar_aplicacao(configuracao=Configuracao):
    """Fábrica da aplicação: facilita execução local, Docker e testes."""

    aplicacao = Flask(__name__)
    aplicacao.config.from_object(configuracao)

    caminho_banco = aplicacao.config["SQLALCHEMY_DATABASE_URI"]
    if caminho_banco.startswith("sqlite:///") and ":memory:" not in caminho_banco:
        Path(caminho_banco.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

    banco.init_app(aplicacao)
    aplicacao.config["DIRETORIO_IMPORTACOES_TEMPORARIAS"].mkdir(parents=True, exist_ok=True)
    registrar_namespaces()
    api.init_app(aplicacao)

    @aplicacao.errorhandler(RequestEntityTooLarge)
    def tratar_upload_muito_grande(_erro):
        """Mantém o limite de upload compreensível também fora do Swagger."""

        return {"message": "O arquivo excede o limite de 10 MB."}, 413

    with aplicacao.app_context():
        # Importar modelos registra as tabelas no metadado antes do create_all.
        import app.modelos  # noqa: F401

        banco.create_all()
        _aplicar_ajustes_aditivos_de_schema()

    return aplicacao
