from pathlib import Path

from flask import Flask
from werkzeug.exceptions import RequestEntityTooLarge

from app.api import api, registrar_namespaces
from app.configuracao import Configuracao
from app.extensoes import banco


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

    return aplicacao
