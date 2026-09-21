from flask_restx import Api


api = Api(
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


def registrar_namespaces():
    """Importação tardia evita dependência circular durante a criação da aplicação."""

    from app.api.rotas import (
        namespace_avaliacoes,
        namespace_autenticacao,
        namespace_bases,
        namespace_entidades,
        namespace_grupos,
        namespace_indicadores,
        namespace_importacoes,
        namespace_observacoes,
        namespace_projetos,
        namespace_sistema,
    )

    api.add_namespace(namespace_sistema)
    api.add_namespace(namespace_autenticacao)
    api.add_namespace(namespace_projetos)
    api.add_namespace(namespace_grupos)
    api.add_namespace(namespace_entidades)
    api.add_namespace(namespace_indicadores)
    api.add_namespace(namespace_observacoes)
    api.add_namespace(namespace_importacoes)
    api.add_namespace(namespace_bases)
    api.add_namespace(namespace_avaliacoes)
