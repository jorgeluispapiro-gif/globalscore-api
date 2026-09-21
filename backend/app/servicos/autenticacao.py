"""Integra a API própria ao serviço externo Supabase Auth."""

import json
from functools import wraps
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app, g, request


class CredenciaisInvalidasError(Exception):
    """Representa ausência, formato incorreto ou rejeição do token."""


class ServicoAutenticacaoIndisponivelError(Exception):
    """Representa falha de configuração, rede ou resposta do Supabase."""


def extrair_token_bearer(cabecalho_autorizacao):
    """Extrai somente o formato HTTP esperado: Authorization: Bearer <token>."""

    partes = (cabecalho_autorizacao or "").strip().split()
    if len(partes) != 2 or partes[0].lower() != "bearer" or not partes[1]:
        raise CredenciaisInvalidasError("Token de autenticação ausente ou inválido.")
    return partes[1]


def validar_token_supabase(token):
    """Consulta o usuário no Supabase para validar o access token recebido.

    A chave pública identifica o projeto Supabase. A senha do usuário nunca passa
    pelo GlobalScore e o token não é armazenado nem incluído em mensagens de erro.
    """

    url_base = (current_app.config.get("SUPABASE_URL") or "").rstrip("/")
    chave_publica = current_app.config.get("SUPABASE_PUBLIC_KEY") or ""
    if not url_base or not chave_publica:
        raise ServicoAutenticacaoIndisponivelError(
            "O serviço de autenticação não está configurado."
        )

    requisicao = Request(
        f"{url_base}/auth/v1/user",
        headers={"Authorization": f"Bearer {token}", "apikey": chave_publica},
        method="GET",
    )
    try:
        with urlopen(
            requisicao, timeout=current_app.config["SUPABASE_AUTH_TIMEOUT_SEGUNDOS"]
        ) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except HTTPError as erro:
        if erro.code in {400, 401, 403}:
            raise CredenciaisInvalidasError("Token de autenticação inválido.") from erro
        raise ServicoAutenticacaoIndisponivelError(
            "O serviço de autenticação está temporariamente indisponível."
        ) from erro
    except (URLError, TimeoutError, OSError) as erro:
        raise ServicoAutenticacaoIndisponivelError(
            "O serviço de autenticação está temporariamente indisponível."
        ) from erro
    except (json.JSONDecodeError, UnicodeDecodeError) as erro:
        raise ServicoAutenticacaoIndisponivelError(
            "O serviço de autenticação retornou uma resposta inválida."
        ) from erro

    identificador = (dados.get("id") or dados.get("sub")) if isinstance(dados, dict) else None
    if not identificador:
        raise ServicoAutenticacaoIndisponivelError(
            "O serviço de autenticação retornou uma resposta inválida."
        )
    return {"id": str(identificador), "email": dados.get("email")}


def autenticacao_obrigatoria(funcao):
    """Protege uma rota e disponibiliza o usuário validado em ``flask.g``."""

    @wraps(funcao)
    def funcao_protegida(*args, **kwargs):
        # Os testes legados usam bypass controlado; produção exige autenticação.
        if not current_app.config.get("AUTENTICACAO_OBRIGATORIA", True):
            g.usuario_autenticado = {"id": "usuario-teste", "email": None}
            return funcao(*args, **kwargs)
        try:
            token = extrair_token_bearer(request.headers.get("Authorization"))
            g.usuario_autenticado = validar_token_supabase(token)
        except CredenciaisInvalidasError as erro:
            return {"message": str(erro)}, 401
        except ServicoAutenticacaoIndisponivelError as erro:
            return {"message": str(erro)}, 503
        return funcao(*args, **kwargs)

    return funcao_protegida
