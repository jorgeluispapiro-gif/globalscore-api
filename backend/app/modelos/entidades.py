from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, UniqueConstraint, event, inspect

from app.extensoes import banco


def agora_utc():
    """Gera datas consistentes para auditoria dos registros."""

    return datetime.now(timezone.utc)


class Projeto(banco.Model):
    __tablename__ = "projetos"

    id = banco.Column(banco.Integer, primary_key=True)
    nome = banco.Column(banco.String(120), nullable=False)
    descricao = banco.Column(banco.Text)
    periodicidade = banco.Column(banco.String(20), nullable=False, default="MENSAL")
    status = banco.Column(banco.String(20), nullable=False, default="RASCUNHO")
    criado_por = banco.Column(banco.String(120), nullable=False, default="sistema")
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)
    atualizado_em = banco.Column(
        banco.DateTime(timezone=True), nullable=False, default=agora_utc, onupdate=agora_utc
    )


class GrupoComparavel(banco.Model):
    __tablename__ = "grupos_comparaveis"

    id = banco.Column(banco.Integer, primary_key=True)
    projeto_id = banco.Column(banco.ForeignKey("projetos.id"), nullable=False, index=True)
    nome = banco.Column(banco.String(120), nullable=False)
    descricao = banco.Column(banco.Text)
    ativo = banco.Column(banco.Boolean, nullable=False, default=True)
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)
    atualizado_em = banco.Column(
        banco.DateTime(timezone=True), nullable=False, default=agora_utc, onupdate=agora_utc
    )

    projeto = banco.relationship("Projeto", backref="grupos_comparaveis")


class Entidade(banco.Model):
    __tablename__ = "entidades"
    __table_args__ = (UniqueConstraint("projeto_id", "codigo", name="uq_entidade_codigo"),)

    id = banco.Column(banco.Integer, primary_key=True)
    projeto_id = banco.Column(banco.ForeignKey("projetos.id"), nullable=False, index=True)
    grupo_id = banco.Column(banco.ForeignKey("grupos_comparaveis.id"), nullable=False, index=True)
    codigo = banco.Column(banco.String(60), nullable=False)
    nome = banco.Column(banco.String(120), nullable=False)
    descricao = banco.Column(banco.Text)
    ativa = banco.Column(banco.Boolean, nullable=False, default=True)
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)
    atualizado_em = banco.Column(
        banco.DateTime(timezone=True), nullable=False, default=agora_utc, onupdate=agora_utc
    )

    projeto = banco.relationship("Projeto", backref="entidades")
    grupo = banco.relationship("GrupoComparavel", backref="entidades")


class Indicador(banco.Model):
    __tablename__ = "indicadores"
    __table_args__ = (
        UniqueConstraint("projeto_id", "codigo", name="uq_indicador_codigo"),
        CheckConstraint("peso_percentual >= 0 AND peso_percentual <= 100", name="ck_peso_indicador"),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    projeto_id = banco.Column(banco.ForeignKey("projetos.id"), nullable=False, index=True)
    codigo = banco.Column(banco.String(60), nullable=False)
    nome = banco.Column(banco.String(120), nullable=False)
    descricao = banco.Column(banco.Text)
    unidade_medida = banco.Column(banco.String(40), nullable=False)
    direcao = banco.Column(banco.String(20), nullable=False)
    peso_percentual = banco.Column(banco.Numeric(7, 4), nullable=False, default=Decimal("0"))
    obrigatorio = banco.Column(banco.Boolean, nullable=False, default=True)
    participa_global_score = banco.Column(banco.Boolean, nullable=False, default=True)
    ativo = banco.Column(banco.Boolean, nullable=False, default=True)
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)
    atualizado_em = banco.Column(
        banco.DateTime(timezone=True), nullable=False, default=agora_utc, onupdate=agora_utc
    )

    projeto = banco.relationship("Projeto", backref="indicadores")


class Observacao(banco.Model):
    __tablename__ = "observacoes"
    __table_args__ = (
        UniqueConstraint("entidade_id", "indicador_id", "periodo", name="uq_observacao_periodo"),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    projeto_id = banco.Column(banco.ForeignKey("projetos.id"), nullable=False, index=True)
    entidade_id = banco.Column(banco.ForeignKey("entidades.id"), nullable=False, index=True)
    indicador_id = banco.Column(banco.ForeignKey("indicadores.id"), nullable=False, index=True)
    periodo = banco.Column(banco.String(7), nullable=False, index=True)
    valor = banco.Column(banco.Numeric(20, 6), nullable=False)
    origem = banco.Column(banco.String(20), nullable=False, default="MANUAL")
    importacao_id = banco.Column(banco.ForeignKey("importacoes.id"), index=True)
    criado_por = banco.Column(banco.String(120), nullable=False, default="sistema")
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)
    atualizado_em = banco.Column(
        banco.DateTime(timezone=True), nullable=False, default=agora_utc, onupdate=agora_utc
    )

    entidade = banco.relationship("Entidade", backref="observacoes")
    indicador = banco.relationship("Indicador", backref="observacoes")


class Importacao(banco.Model):
    """Rastreia o lote e mantém o arquivo apenas durante o fluxo assistido."""

    __tablename__ = "importacoes"

    id = banco.Column(banco.Integer, primary_key=True)
    projeto_id = banco.Column(banco.ForeignKey("projetos.id"), nullable=False, index=True)
    nome_arquivo_original = banco.Column(banco.String(255), nullable=False)
    tipo_arquivo = banco.Column(banco.String(10), nullable=False)
    hash_sha256 = banco.Column(banco.String(64), nullable=False, index=True)
    status = banco.Column(banco.String(20), nullable=False, default="ENVIADA")
    aba_selecionada = banco.Column(banco.String(255))
    configuracao_leitura_json = banco.Column(banco.Text)
    # Fotografia estrutural sem valores das linhas; continua disponível após apagar o arquivo.
    estrutura_json = banco.Column(banco.Text)
    mapeamento_json = banco.Column(banco.Text)
    perfil_importacao_id = banco.Column(
        banco.ForeignKey(
            "perfis_importacao.id",
            name="fk_importacao_perfil_aplicado",
            use_alter=True,
        ),
        index=True,
    )
    quantidade_linhas_lidas = banco.Column(banco.Integer, nullable=False, default=0)
    quantidade_linhas_validas = banco.Column(banco.Integer, nullable=False, default=0)
    quantidade_erros = banco.Column(banco.Integer, nullable=False, default=0)
    resumo_erros_json = banco.Column(banco.Text)
    quantidade_alertas = banco.Column(banco.Integer, nullable=False, default=0)
    resumo_alertas_json = banco.Column(banco.Text)
    criado_por = banco.Column(banco.String(120), nullable=False, default="sistema")
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)
    validado_em = banco.Column(banco.DateTime(timezone=True))
    concluido_em = banco.Column(banco.DateTime(timezone=True))

    # Campo exclusivamente interno; nunca é devolvido pela API.
    caminho_arquivo_temporario = banco.Column(banco.Text)

    projeto = banco.relationship("Projeto", backref="importacoes")
    observacoes = banco.relationship("Observacao", backref="importacao", lazy=True)
    perfil_aplicado = banco.relationship(
        "PerfilImportacao", foreign_keys=[perfil_importacao_id]
    )


class PerfilImportacao(banco.Model):
    """Configuração reutilizável derivada de uma importação concluída."""

    __tablename__ = "perfis_importacao"
    __table_args__ = (
        UniqueConstraint("importacao_origem_id", name="uq_perfil_importacao_origem"),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    projeto_id = banco.Column(banco.ForeignKey("projetos.id"), nullable=False, index=True)
    nome = banco.Column(banco.String(120), nullable=False)
    versao = banco.Column(banco.Integer, nullable=False, default=1)
    importacao_origem_id = banco.Column(
        banco.ForeignKey("importacoes.id"), nullable=False, index=True
    )
    tipo_arquivo = banco.Column(banco.String(10), nullable=False)
    configuracao_leitura_json = banco.Column(banco.Text, nullable=False)
    estrutura_json = banco.Column(banco.Text, nullable=False)
    mapeamento_json = banco.Column(banco.Text, nullable=False)
    assinatura_estrutura = banco.Column(banco.String(64), nullable=False, index=True)
    versao_assinatura = banco.Column(banco.Integer, nullable=False, default=1)
    ativo = banco.Column(banco.Boolean, nullable=False, default=True)
    criado_por = banco.Column(banco.String(120), nullable=False)
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)
    atualizado_em = banco.Column(
        banco.DateTime(timezone=True), nullable=False, default=agora_utc, onupdate=agora_utc
    )

    projeto = banco.relationship("Projeto", backref="perfis_importacao")
    importacao_origem = banco.relationship(
        "Importacao",
        foreign_keys=[importacao_origem_id],
        backref="perfil_importacao_criado",
    )


class BaseReferencia(banco.Model):
    __tablename__ = "bases_referencia"
    __table_args__ = (
        UniqueConstraint("grupo_id", "versao", name="uq_base_grupo_versao"),
        CheckConstraint(
            "cobertura_minima_percentual >= 0 AND cobertura_minima_percentual <= 100",
            name="ck_cobertura_base",
        ),
        CheckConstraint("minimo_observacoes = 3", name="ck_minimo_observacoes"),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    projeto_id = banco.Column(banco.ForeignKey("projetos.id"), nullable=False, index=True)
    grupo_id = banco.Column(banco.ForeignKey("grupos_comparaveis.id"), nullable=False, index=True)
    modo = banco.Column(banco.String(30), nullable=False)
    entidade_referencia_id = banco.Column(banco.ForeignKey("entidades.id"), index=True)
    nome = banco.Column(banco.String(120), nullable=False)
    versao = banco.Column(banco.Integer, nullable=False)
    periodo_inicial = banco.Column(banco.String(7), nullable=False)
    periodo_final = banco.Column(banco.String(7), nullable=False)
    metodo_consolidacao = banco.Column(banco.String(20), nullable=False, default="MEDIA")
    cobertura_minima_percentual = banco.Column(banco.Numeric(5, 2), nullable=False, default=50)
    minimo_observacoes = banco.Column(banco.Integer, nullable=False, default=3)
    status = banco.Column(banco.String(20), nullable=False, default="RASCUNHO")
    base_anterior_id = banco.Column(banco.ForeignKey("bases_referencia.id"))
    quantidade_entidades = banco.Column(banco.Integer, nullable=False, default=0)
    quantidade_indicadores = banco.Column(banco.Integer, nullable=False, default=0)
    criada_por = banco.Column(banco.String(120), nullable=False, default="sistema")
    criada_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)
    processada_em = banco.Column(banco.DateTime(timezone=True))
    ativada_em = banco.Column(banco.DateTime(timezone=True))

    projeto = banco.relationship("Projeto", backref="bases_referencia")
    grupo = banco.relationship("GrupoComparavel", backref="bases_referencia")
    entidade_referencia = banco.relationship("Entidade", foreign_keys=[entidade_referencia_id])


class ValorConsolidadoBase(banco.Model):
    __tablename__ = "valores_consolidados_base"
    __table_args__ = (
        UniqueConstraint(
            "base_referencia_id", "entidade_id", "indicador_id", name="uq_valor_consolidado"
        ),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    base_referencia_id = banco.Column(banco.ForeignKey("bases_referencia.id"), nullable=False)
    entidade_id = banco.Column(banco.ForeignKey("entidades.id"), nullable=False)
    indicador_id = banco.Column(banco.ForeignKey("indicadores.id"), nullable=False)
    valor_consolidado = banco.Column(banco.Numeric(20, 6))
    metodo_consolidacao = banco.Column(banco.String(20), nullable=False, default="MEDIA")
    quantidade_observacoes = banco.Column(banco.Integer, nullable=False)
    quantidade_periodos_esperados = banco.Column(banco.Integer, nullable=False)
    quantidade_minima_exigida = banco.Column(banco.Integer, nullable=False)
    percentual_cobertura = banco.Column(banco.Numeric(7, 4), nullable=False)
    status_cobertura = banco.Column(banco.String(20), nullable=False)
    primeiro_periodo_encontrado = banco.Column(banco.String(7))
    ultimo_periodo_encontrado = banco.Column(banco.String(7))
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)


class ItemPopulacaoReferencia(banco.Model):
    __tablename__ = "itens_populacao_referencia"

    id = banco.Column(banco.Integer, primary_key=True)
    base_referencia_id = banco.Column(banco.ForeignKey("bases_referencia.id"), nullable=False)
    indicador_id = banco.Column(banco.ForeignKey("indicadores.id"), nullable=False)
    entidade_id = banco.Column(banco.ForeignKey("entidades.id"), nullable=False)
    periodo = banco.Column(banco.String(7))
    valor_referencia = banco.Column(banco.Numeric(20, 6), nullable=False)
    origem = banco.Column(banco.String(30), nullable=False)
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)


class IndicadorBaseReferencia(banco.Model):
    __tablename__ = "indicadores_base_referencia"
    __table_args__ = (
        UniqueConstraint("base_referencia_id", "indicador_id", name="uq_indicador_base"),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    base_referencia_id = banco.Column(banco.ForeignKey("bases_referencia.id"), nullable=False)
    indicador_id = banco.Column(banco.ForeignKey("indicadores.id"), nullable=False)
    status = banco.Column(banco.String(30), nullable=False)
    participa_global_score = banco.Column(banco.Boolean, nullable=False)
    peso_aplicado = banco.Column(banco.Numeric(7, 4), nullable=False)
    direcao_aplicada = banco.Column(banco.String(20), nullable=False)
    tamanho_populacao = banco.Column(banco.Integer, nullable=False)
    valor_minimo = banco.Column(banco.Numeric(20, 6))
    valor_maximo = banco.Column(banco.Numeric(20, 6))
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)

    indicador = banco.relationship("Indicador")


class ReguaPercentil(banco.Model):
    __tablename__ = "reguas_percentis"
    __table_args__ = (
        UniqueConstraint("indicador_base_referencia_id", "percentil", name="uq_regua_percentil"),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    indicador_base_referencia_id = banco.Column(
        banco.ForeignKey("indicadores_base_referencia.id"), nullable=False, index=True
    )
    percentil = banco.Column(banco.Integer, nullable=False)
    valor_corte = banco.Column(banco.Numeric(20, 10), nullable=False)
    metodo = banco.Column(banco.String(30), nullable=False, default="PERCENTILE_INC")
    criado_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)

    indicador_base = banco.relationship("IndicadorBaseReferencia", backref="regua")


class Avaliacao(banco.Model):
    __tablename__ = "avaliacoes"
    __table_args__ = (
        UniqueConstraint(
            "entidade_id", "periodo", "base_referencia_id", name="uq_avaliacao_periodo_base"
        ),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    projeto_id = banco.Column(banco.ForeignKey("projetos.id"), nullable=False)
    entidade_id = banco.Column(banco.ForeignKey("entidades.id"), nullable=False)
    grupo_id = banco.Column(banco.ForeignKey("grupos_comparaveis.id"), nullable=False)
    base_referencia_id = banco.Column(banco.ForeignKey("bases_referencia.id"), nullable=False)
    periodo = banco.Column(banco.String(7), nullable=False)
    global_score = banco.Column(banco.Numeric(10, 6))
    rating = banco.Column(banco.String(40))
    status = banco.Column(banco.String(20), nullable=False)
    pesos_total = banco.Column(banco.Numeric(7, 4), nullable=False)
    calculada_em = banco.Column(banco.DateTime(timezone=True), nullable=False, default=agora_utc)

    itens = banco.relationship(
        "ItemAvaliacao", backref="avaliacao", cascade="all, delete-orphan", lazy=True
    )


class ItemAvaliacao(banco.Model):
    __tablename__ = "itens_avaliacao"
    __table_args__ = (
        UniqueConstraint("avaliacao_id", "indicador_id", name="uq_item_avaliacao"),
    )

    id = banco.Column(banco.Integer, primary_key=True)
    avaliacao_id = banco.Column(banco.ForeignKey("avaliacoes.id"), nullable=False)
    indicador_id = banco.Column(banco.ForeignKey("indicadores.id"), nullable=False)
    valor_observado = banco.Column(banco.Numeric(20, 6))
    pontuacao_percentil = banco.Column(banco.Integer)
    peso_aplicado = banco.Column(banco.Numeric(7, 4), nullable=False)
    contribuicao_score = banco.Column(banco.Numeric(12, 6))
    direcao_aplicada = banco.Column(banco.String(20), nullable=False)


@event.listens_for(BaseReferencia, "before_update")
def impedir_alteracao_modo_base_ativa(mapper, conexao, base):
    """Protege as escolhas que definem matematicamente uma base já congelada."""

    estado = inspect(base)
    historico_status = estado.attrs.status.history
    status_anterior = historico_status.deleted[0] if historico_status.deleted else base.status
    if status_anterior not in {"ATIVA", "SUBSTITUIDA"}:
        return

    if estado.attrs.modo.history.has_changes() or estado.attrs.entidade_referencia_id.history.has_changes():
        raise ValueError(
            "Modo e entidade de referência são imutáveis após a ativação da base."
        )
