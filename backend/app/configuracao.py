import os
from pathlib import Path


class Configuracao:
    """Configurações compartilhadas pela API do GlobalScore."""

    DIRETORIO_BACKEND = Path(__file__).resolve().parent.parent
    CAMINHO_BANCO_PADRAO = DIRETORIO_BACKEND / "dados" / "globalscore.db"

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "GLOBALSCORE_BANCO_URL",
        f"sqlite:///{CAMINHO_BANCO_PADRAO}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False


class ConfiguracaoTeste(Configuracao):
    """Usa banco em memória para que os testes não alterem dados locais."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

