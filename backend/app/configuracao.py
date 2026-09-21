import os
from pathlib import Path


class Configuracao:
    """Configurações compartilhadas pela API do GlobalScore."""

    DIRETORIO_BACKEND = Path(__file__).resolve().parent.parent
    CAMINHO_BANCO_PADRAO = DIRETORIO_BACKEND / "dados" / "globalscore.db"
    DIRETORIO_IMPORTACOES_TEMPORARIAS = Path(
        os.getenv("GLOBALSCORE_DIRETORIO_IMPORTACOES", "/tmp/globalscore_importacoes")
    )

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "GLOBALSCORE_BANCO_URL",
        f"sqlite:///{CAMINHO_BANCO_PADRAO}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024


class ConfiguracaoTeste(Configuracao):
    """Usa banco em memória para que os testes não alterem dados locais."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
