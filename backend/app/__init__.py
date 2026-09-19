from pathlib import Path

from flask import Flask

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
    registrar_namespaces()
    api.init_app(aplicacao)

    with aplicacao.app_context():
        # Importar modelos registra as tabelas no metadado antes do create_all.
        import app.modelos  # noqa: F401

        banco.create_all()

    return aplicacao

