"""Cria perfis reutilizáveis sem aplicar automaticamente uma nova importação."""

import hashlib
import json
import unicodedata
from copy import deepcopy

from app.extensoes import banco
from app.modelos import Entidade, Importacao, Indicador, PerfilImportacao


VERSAO_ASSINATURA = 1


class PerfilPrecisaRevisaoError(ValueError):
    """Indica que uma referência persistida deixou de ser válida para aplicação."""


def normalizar_cabecalho(valor):
    """Uniformiza representação, bordas, espaços e caixa sem remover sinais."""

    texto = unicodedata.normalize("NFKC", str(valor or ""))
    return " ".join(texto.strip().split()).casefold()


def montar_fotografia_estrutura(importacao, cabecalhos, configuracao_leitura):
    """Preserva somente dados estruturais necessários depois da remoção do arquivo."""

    configuracao = configuracao_leitura or {}
    configuracao_estrutural = {
        "linha_inicial": configuracao.get("linha_inicial"),
        "linhas_cabecalho": configuracao.get("linhas_cabecalho", []),
        "colunas_utilizadas": configuracao.get("colunas_utilizadas", []),
    }
    if importacao.tipo_arquivo == "CSV":
        configuracao_estrutural["delimitador"] = configuracao.get("delimitador")
    elif importacao.tipo_arquivo == "XLSX":
        configuracao_estrutural["aba"] = configuracao.get("aba")

    return {
        "versao_estrutura": 1,
        "tipo_arquivo": importacao.tipo_arquivo,
        "configuracao_estrutural": configuracao_estrutural,
        "cabecalhos": [
            {
                "indice_coluna": item["indice_coluna"],
                "nome_original": item["nome"],
                "nome_normalizado": normalizar_cabecalho(item["nome"]),
            }
            for item in cabecalhos
        ],
    }


def gerar_assinatura_estrutura(estrutura):
    """Gera SHA-256 determinístico a partir da estrutura normalizada exata."""

    conteudo_assinavel = {
        "versao_assinatura": VERSAO_ASSINATURA,
        "tipo_arquivo": estrutura["tipo_arquivo"],
        "configuracao_estrutural": estrutura.get("configuracao_estrutural", {}),
        "cabecalhos": [
            {
                "indice_coluna": item["indice_coluna"],
                "nome_normalizado": item["nome_normalizado"],
            }
            for item in estrutura.get("cabecalhos", [])
        ],
    }
    json_canonico = json.dumps(
        conteudo_assinavel,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(json_canonico.encode("utf-8")).hexdigest()


def _normalizar_decisao_indicador(decisao, projeto_id):
    """Converte CRIAR em uma referência ao indicador efetivamente persistido."""

    if not decisao:
        return decisao
    acao = decisao.get("acao")
    if acao == "CRIAR":
        codigo = str(decisao.get("dados", {}).get("codigo") or "").strip()
        indicador = Indicador.query.filter_by(projeto_id=projeto_id, codigo=codigo).first()
        if indicador is None:
            raise ValueError(
                f"O indicador criado com código '{codigo}' não foi encontrado no projeto."
            )
        return {"acao": "EXISTENTE", "indicador_id": indicador.id}
    if acao in {"ASSOCIAR", "EXISTENTE"}:
        indicador = banco.session.get(Indicador, decisao.get("indicador_id"))
        if indicador is None or indicador.projeto_id != projeto_id:
            raise ValueError("O perfil contém um indicador que não pertence ao projeto.")
        return {"acao": "EXISTENTE", "indicador_id": indicador.id}
    return deepcopy(decisao)


def normalizar_mapeamento_perfil(mapeamento, projeto_id):
    """Remove ações transitórias e preserva apenas decisões reutilizáveis."""

    normalizado = deepcopy(mapeamento or {})
    for coluna in normalizado.get("colunas", []):
        if "indicador" in coluna:
            coluna["indicador"] = _normalizar_decisao_indicador(
                coluna.get("indicador"), projeto_id
            )

    decisoes_indicadores = normalizado.get("decisoes_indicadores", {})
    normalizado["decisoes_indicadores"] = {
        rotulo: _normalizar_decisao_indicador(decisao, projeto_id)
        for rotulo, decisao in decisoes_indicadores.items()
    }

    decisoes_entidades = normalizado.get("decisoes_entidades", {})
    normalizado["decisoes_entidades"] = {
        codigo: deepcopy(decisao)
        for codigo, decisao in decisoes_entidades.items()
        if decisao.get("acao") in {"ASSOCIAR", "IGNORAR"}
    }
    return normalizado


def validar_referencias_perfil(perfil):
    """Confere IDs persistidos sem criar ou alterar cadastros do projeto."""

    mapeamento = json.loads(perfil.mapeamento_json or "{}")
    decisoes_indicadores = [
        coluna.get("indicador")
        for coluna in mapeamento.get("colunas", [])
        if coluna.get("indicador")
    ]
    decisoes_indicadores.extend(
        mapeamento.get("decisoes_indicadores", {}).values()
    )
    for decisao in decisoes_indicadores:
        acao = decisao.get("acao")
        if acao == "IGNORAR":
            continue
        if acao not in {"ASSOCIAR", "EXISTENTE"}:
            raise PerfilPrecisaRevisaoError(
                "O perfil precisa ser revisado: existe decisão de indicador "
                "que não pode ser reutilizada."
            )
        indicador = banco.session.get(Indicador, decisao.get("indicador_id"))
        if (
            indicador is None
            or indicador.projeto_id != perfil.projeto_id
            or not indicador.ativo
        ):
            raise PerfilPrecisaRevisaoError(
                "O perfil precisa ser revisado: existe indicador ausente, inativo "
                "ou fora do projeto."
            )

    for decisao in mapeamento.get("decisoes_entidades", {}).values():
        acao = decisao.get("acao")
        if acao == "IGNORAR":
            continue
        if acao != "ASSOCIAR":
            raise PerfilPrecisaRevisaoError(
                "O perfil precisa ser revisado: existe decisão de entidade "
                "que não pode ser reutilizada."
            )
        entidade = banco.session.get(Entidade, decisao.get("entidade_id"))
        if (
            entidade is None
            or entidade.projeto_id != perfil.projeto_id
            or not entidade.ativa
        ):
            raise PerfilPrecisaRevisaoError(
                "O perfil precisa ser revisado: existe entidade associada ausente, "
                "inativa ou fora do projeto."
            )


def criar_perfil_importacao(importacao, nome, criado_por):
    """Materializa a versão 1 do perfil a partir de um lote concluído."""

    if importacao.status != "CONCLUIDA":
        raise ValueError("Somente uma importação concluída pode originar um perfil.")
    if not importacao.estrutura_json:
        raise ValueError("A importação não possui a fotografia estrutural necessária.")
    nome_limpo = str(nome or "").strip()
    if not nome_limpo:
        raise ValueError("O nome do perfil é obrigatório.")

    estrutura = json.loads(importacao.estrutura_json)
    configuracao = json.loads(importacao.configuracao_leitura_json or "{}")
    mapeamento = json.loads(importacao.mapeamento_json or "{}")
    mapeamento_normalizado = normalizar_mapeamento_perfil(mapeamento, importacao.projeto_id)

    perfil = PerfilImportacao(
        projeto_id=importacao.projeto_id,
        nome=nome_limpo,
        versao=1,
        importacao_origem_id=importacao.id,
        tipo_arquivo=importacao.tipo_arquivo,
        configuracao_leitura_json=json.dumps(configuracao, ensure_ascii=False),
        estrutura_json=json.dumps(estrutura, ensure_ascii=False),
        mapeamento_json=json.dumps(mapeamento_normalizado, ensure_ascii=False),
        assinatura_estrutura=gerar_assinatura_estrutura(estrutura),
        versao_assinatura=VERSAO_ASSINATURA,
        ativo=True,
        criado_por=criado_por,
    )
    banco.session.add(perfil)
    banco.session.commit()
    return perfil


def perfil_para_dict(perfil):
    """Expõe metadados suficientes para listar e identificar o perfil."""

    return {
        "id": perfil.id,
        "projeto_id": perfil.projeto_id,
        "nome": perfil.nome,
        "versao": perfil.versao,
        "importacao_origem_id": perfil.importacao_origem_id,
        "tipo_arquivo": perfil.tipo_arquivo,
        "assinatura_estrutura": perfil.assinatura_estrutura,
        "versao_assinatura": perfil.versao_assinatura,
        "ativo": perfil.ativo,
        "criado_por": perfil.criado_por,
        "criado_em": perfil.criado_em.isoformat(),
        "atualizado_em": perfil.atualizado_em.isoformat(),
    }
