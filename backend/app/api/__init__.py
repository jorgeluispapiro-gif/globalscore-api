from flask import Blueprint
from flask_restx import Api


def init_api(aplicacao):
    """Inicializa uma instância isolada do Flask-RESTX com Blueprint para a aplicação."""
    blueprint_api = Blueprint("api", __name__, url_prefix="")

    api = Api(
        blueprint_api,
        title="GlobalScore API",
        version="1.0",
        description="API para avaliações comparativas com bases percentílicas congeladas.",
        doc="/docs",
        authorizations={
            "Bearer": {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "Informe: Bearer <access_token do Supabase>",
            }
        },
    )

    from app.api.rotas import (
        namespace_analytics,
        namespace_autenticacao,
        namespace_avaliacoes,
        namespace_bases,
        namespace_entidades,
        namespace_eventos,
        namespace_grupos,
        namespace_importacoes,
        namespace_indicadores,
        namespace_observacoes,
        namespace_perfis_importacao,
        namespace_projetos,
        namespace_sistema,
    )

    api.add_namespace(namespace_sistema)
    api.add_namespace(namespace_autenticacao)
    api.add_namespace(namespace_projetos)
    api.add_namespace(namespace_grupos)
    api.add_namespace(namespace_entidades)
    api.add_namespace(namespace_eventos)
    api.add_namespace(namespace_indicadores)
    api.add_namespace(namespace_observacoes)
    api.add_namespace(namespace_importacoes)
    api.add_namespace(namespace_perfis_importacao)
    api.add_namespace(namespace_bases)
    api.add_namespace(namespace_avaliacoes)
    api.add_namespace(namespace_analytics)

    aplicacao.register_blueprint(blueprint_api)
    return api
