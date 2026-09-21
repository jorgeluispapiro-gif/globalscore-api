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
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_PUBLIC_KEY = os.getenv("SUPABASE_PUBLIC_KEY")
    SUPABASE_AUTH_TIMEOUT_SEGUNDOS = float(
        os.getenv("SUPABASE_AUTH_TIMEOUT_SEGUNDOS", "5")
    )
    AUTENTICACAO_OBRIGATORIA = True


class ConfiguracaoTeste(Configuracao):
    """Usa banco em memória para que os testes não alterem dados locais."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    # Mantém os testes anteriores isolados; os testes de autenticação reativam a proteção.
    AUTENTICACAO_OBRIGATORIA = False
