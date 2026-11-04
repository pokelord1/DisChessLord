import os
import io
import json
import uuid
import asyncio
import tempfile
import logging
import shutil

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import chess
import chess.pgn


# ============================================================
# 1. CONFIGURAÇÃO
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "A variável de ambiente DISCORD_TOKEN não foi configurada."
    )

DATA_DIR = os.getenv("DATA_DIR", "/data")

if not os.path.isdir(DATA_DIR):
    DATA_DIR = "."

ELO_FILE = os.path.join(DATA_DIR, "dischess_data.json")
BACKUP_FILE = ELO_FILE + ".backup"

K_FACTOR = 32
TEMPO_DESAFIO = 120
TEMPO_PROMOCAO = 60
TEMPO_CONFIRMACAO = 30

MOVIMENTOS_POR_PAGINA = 15

# Somente estes usuários podem executar /resetar.
USUARIOS_AUTORIZADOS_RESET = {
    1369324713498906786
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

user_data = {}

active_games = {}
pending_challenges = {}

game_locks = {}
challenge_lock = asyncio.Lock()
reset_lock = asyncio.Lock()

data_loaded = False


# ============================================================
# 2. DADOS E PERSISTÊNCIA
# ============================================================

def dados_padrao():
    """
    Perfil de jogadores novos.
    Não deve ser usado para substituir perfis existentes.
    """
    return {
        "elo": 100,
        "peak_elo": 100,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "history": []
    }


def carregar_dados():
    """
    Carrega os dados sem alterar os valores já existentes.

    Perfis antigos que guardavam apenas um número são migrados
    preservando o ELO antigo.
    """
    global user_data

    if not os.path.exists(ELO_FILE):
        user_data = {}

        logging.info(
            "Arquivo de dados não encontrado. "
            "Um novo será criado quando necessário."
        )

        return

    try:
        with open(ELO_FILE, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)

        if not isinstance(dados, dict):
            raise ValueError(
                "O arquivo JSON não contém um objeto válido."
            )

        dados_carregados = {}

        for user_id, perfil in dados.items():

            # Compatibilidade com versões antigas que
            # armazenavam somente o ELO como número.
            if isinstance(perfil, int) and not isinstance(perfil, bool):

                novo_perfil = dados_padrao()

                novo_perfil["elo"] = perfil
                novo_perfil["peak_elo"] = perfil

                dados_carregados[user_id] = novo_perfil

                continue

            if not isinstance(perfil, dict):
                raise ValueError(
                    f"Dados inválidos para o jogador {user_id}."
                )

            # Cria os campos ausentes sem substituir os existentes.
            novo_perfil = dados_padrao()
            novo_perfil.update(perfil)

            if not isinstance(novo_perfil["history"], list):
                raise ValueError(
                    f"Histórico inválido para o jogador {user_id}."
                )

            for campo in (
                "elo",
                "peak_elo",
                "wins",
                "losses",
                "draws"
            ):
                if (
                    not isinstance(novo_perfil[campo], int)
                    or isinstance(novo_perfil[campo], bool)
                ):
                    raise ValueError(
                        f"Campo {campo} inválido para o jogador {user_id}."
                    )

            dados_carregados[user_id] = novo_perfil

        user_data = dados_carregados

        logging.info(
            "%s perfis carregados com sucesso.",
            len(user_data)
        )

    except Exception:
        logging.exception("Erro ao carregar os dados.")

        raise RuntimeError(
            "O arquivo de dados está corrompido ou inacessível. "
            "O bot não será iniciado para proteger os dados existentes."
        )


def salvar_dados():
    """
    Salva os dados usando arquivo temporário e substituição atômica.
    """
    caminho_temporario = None

    try:
        os.makedirs(DATA_DIR, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=DATA_DIR,
            delete=False
        ) as arquivo:

            json.dump(
                user_data,
                arquivo,
                ensure_ascii=False,
                indent=4
            )

            caminho_temporario = arquivo.name

        os.replace(caminho_temporario, ELO_FILE)

        return True

    except Exception:
        logging.exception("Erro ao salvar os dados.")

        if (
            caminho_temporario
            and os.path.exists(caminho_temporario)
        ):
            try:
                os.remove(caminho_temporario)
            except OSError:
                pass

        return False


def obter_dados_usuario(user_id):
    user_id = str(user_id)

    if user_id not in user_data:
        # Somente jogadores realmente novos recebem 100 ELO.
        user_data[user_id] = dados_padrao()

    elif isinstance(user_data[user_id], int):
        # Migração de dados antigos sem perder o ELO.
        elo_antigo = user_data[user_id]

        user_data[user_id] = dados_padrao()

        user_data[user_id]["elo"] = elo_antigo
        user_data[user_id]["peak_elo"] = elo_antigo

    else:
        # Completa campos ausentes sem alterar os existentes.
        padrao = dados_padrao()

        for chave, valor in padrao.items():
            if chave not in user_data[user_id]:
                user_data[user_id][chave] = valor

    return user_data[user_id]


# ============================================================
# 3. ELO E PATENTES
# ============================================================

PATENTES = [
    (0, 499, "Iniciante"),
    (500, 999, "Amador"),
    (1000, 1499, "Intermediário"),
    (1500, 1999, "Mestre"),
    (2000, 2999, "Grão-Mestre"),
    (3000, 49999, "Lenda do Xadrez"),
    (50000, float("inf"), "Deus do Xadrez")
]


def obter_patente(elo):
    for minimo, maximo, nome in PATENTES:
        if minimo <= elo <= maximo:
            return nome

    return "Iniciante"


def calcular_delta_elo(
    elo_brancas,
    elo_pretas,
    resultado_brancas
):
    expectativa_brancas = 1 / (
        1 + 10 ** ((elo_pretas - elo_brancas) / 400)
    )

    expectativa_pretas = 1 - expectativa_brancas

    resultado_pretas = 1 - resultado_brancas

    delta_brancas = round(
        K_FACTOR * (resultado_brancas - expectativa_brancas)
    )

    delta_pretas = round(
        K_FACTOR * (resultado_pretas - expectativa_pretas)
    )

    return delta_brancas, delta_pretas


def atualizar_elo(user_id, delta):
    dados = obter_dados_usuario(user_id)

    dados["elo"] = max(0, dados["elo"] + delta)

    dados["peak_elo"] = max(
        dados["peak_elo"],
        dados["elo"]
    )


# ============================================================
# 4. JOGADORES OCUPADOS E LOCKS
# ============================================================

def jogador_em_jogo(user_id):
    return any(
        user_id in (game["white"], game["black"])
        for game in active_games.values()
    )


def jogador_em_desafio(user_id):
    return any(
        user_id in (
            challenge["challenger"],
            challenge["opponent"]
        )
        for challenge in pending_challenges.values()
    )


def jogador_ocupado(user_id):
    return (
        jogador_em_jogo(user_id)
        or jogador_em_desafio(user_id)
    )


def obter_lock(channel_id):
    if channel_id not in game_locks:
        game_locks[channel_id] = asyncio.Lock()

    return game_locks[channel_id]


# ============================================================
# 5. TABULEIRO E PARTIDAS
# ============================================================

def desenhar_tabuleiro(board):
    tabuleiro = board.unicode(
        borders=True,
        empty_square="·",
        orientation=chess.WHITE
    )

    return f"```{tabuleiro}```"


def criar_embed_partida(game):
    board = game["board"]

    embed = discord.Embed(
        title="♟️ DISCHESS — Partida em andamento",
        description=desenhar_tabuleiro(board),
        color=discord.Color.blurple()
    )

    brancas = bot.get_user(game["white"])
    pretas = bot.get_user(game["black"])

    nome_brancas = (
        brancas.name if brancas else str(game["white"])
    )

    nome_pretas = (
        pretas.name if pretas else str(game["black"])
    )

    embed.add_field(
        name="⚪ Brancas",
        value=nome_brancas,
        inline=True
    )

    embed.add_field(
        name="⚫ Pretas",
        value=nome_pretas,
        inline=True
    )

    turno = (
        "Brancas"
        if board.turn == chess.WHITE
        else "Pretas"
    )

    embed.add_field(
        name="Vez de jogar",
        value=turno,
        inline=False
    )

    if game.get("draw_offer") is not None:
        ofertante = bot.get_user(game["draw_offer"])

        nome_ofertante = (
            ofertante.name
            if ofertante
            else str(game["draw_offer"])
        )

        embed.add_field(
            name="🤝 Oferta de empate pendente",
            value=(
                f"{nome_ofertante} ofereceu empate. "
                "O adversário pode aceitar ou recusar."
            ),
            inline=False
        )

    embed.set_footer(
        text=f"Partida {game['id']} • DISCHESS"
    )

    return embed


def criar_partida(challenger, opponent, channel_id):
    game_id = str(uuid.uuid4()).upper()

    return {
        "id": game_id,
        "white": challenger,
        "black": opponent,
        "channel_id": channel_id,
        "board": chess.Board(),
        "moves": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "draw_offer": None,
        "selected_square": None,
        "finished": False,
        "message": None
    }


def jogador_da_vez(game):
    return (
        game["white"]
        if game["board"].turn == chess.WHITE
        else game["black"]
    )


def jogador_adversario(game, user_id):
    if user_id == game["white"]:
        return game["black"]

    return game["white"]


def motivo_fim(board):
    outcome = board.outcome()

    if outcome is None:
        return None

    if outcome.termination == chess.Termination.CHECKMATE:
        return "Xeque-mate"

    if outcome.termination == chess.Termination.STALEMATE:
        return "Afogamento"

    if outcome.termination == chess.Termination.INSUFFICIENT_MATERIAL:
        return "Material insuficiente"

    if outcome.termination == chess.Termination.SEVENTYFIVE_MOVES:
        return "Regra dos 75 movimentos"

    if outcome.termination == chess.Termination.FIVEFOLD_REPETITION:
        return "Repetição quíntupla"

    return "Empate pelas regras do xadrez"


# ============================================================
# 6. HISTÓRICO, PGN E FINALIZAÇÃO
# ============================================================

def gerar_pgn(game, resultado, motivo):
    board = chess.Board()

    partida = chess.pgn.Game()

    partida.headers["Event"] = "DISCHESS"
    partida.headers["Site"] = "Discord"
    partida.headers["Date"] = datetime.now().strftime("%Y.%m.%d")
    partida.headers["White"] = str(game["white"])
    partida.headers["Black"] = str(game["black"])
    partida.headers["Result"] = resultado
    partida.headers["Termination"] = motivo

    node = partida

    for uci in game["moves"]:
        move = chess.Move.from_uci(uci)

        if board.is_legal(move):
            board.push(move)
            node = node.add_variation(move)

    saida = io.StringIO()

    print(partida, file=saida, end="")

    return saida.getvalue()


def registrar_historico(
    game,
    resultado,
    motivo,
    elo_brancas_antes,
    elo_pretas_antes,
    delta_brancas,
    delta_pretas
):
    elo_brancas_depois = obter_dados_usuario(
        game["white"]
    )["elo"]

    elo_pretas_depois = obter_dados_usuario(
        game["black"]
    )["elo"]

    pgn = gerar_pgn(game, resultado, motivo)

    registro_base = {
        "id": game["id"],
        "date": datetime.now(timezone.utc).isoformat(),
        "result": resultado,
        "reason": motivo,
        "white": game["white"],
        "black": game["black"],
        "white_elo_before": elo_brancas_antes,
        "black_elo_before": elo_pretas_antes,
        "white_elo_after": elo_brancas_depois,
        "black_elo_after": elo_pretas_depois,
        "white_delta": delta_brancas,
        "black_delta": delta_pretas,
        "moves": list(game["moves"]),
        "pgn": pgn
    }

    for user_id in (game["white"], game["black"]):
        dados = obter_dados_usuario(user_id)

        registro = dict(registro_base)

        dados["history"].insert(0, registro)
        dados["history"] = dados["history"][:100]


def finalizar_partida(game, resultado, motivo):
    if game["finished"]:
        return None

    if active_games.get(game["channel_id"]) is not game:
        return None

    game["finished"] = True

    white = game["white"]
    black = game["black"]

    dados_brancas = obter_dados_usuario(white)
    dados_pretas = obter_dados_usuario(black)

    elo_brancas_antes = dados_brancas["elo"]
    elo_pretas_antes = dados_pretas["elo"]

    if resultado == "1-0":
        score = 1.0
        dados_brancas["wins"] += 1
        dados_pretas["losses"] += 1

    elif resultado == "0-1":
        score = 0.0
        dados_brancas["losses"] += 1
        dados_pretas["wins"] += 1

    else:
        score = 0.5
        dados_brancas["draws"] += 1
        dados_pretas["draws"] += 1

    delta_brancas, delta_pretas = calcular_delta_elo(
        elo_brancas_antes,
        elo_pretas_antes,
        score
    )

    atualizar_elo(white, delta_brancas)
    atualizar_elo(black, delta_pretas)

    registrar_historico(
        game,
        resultado,
        motivo,
        elo_brancas_antes,
        elo_pretas_antes,
        delta_brancas,
        delta_pretas
    )

    salvou = salvar_dados()

    if not salvou:
        logging.error(
            "A partida %s terminou, mas os dados não foram salvos.",
            game["id"]
        )

    active_games.pop(game["channel_id"], None)

    return {
        "delta_white": delta_brancas,
        "delta_black": delta_pretas,
        "white_elo": obter_dados_usuario(white)["elo"],
        "black_elo": obter_dados_usuario(black)["elo"],
        "saved": salvou
    }


def criar_embed_final(game, titulo, descricao, resultado_elo):
    embed = discord.Embed(
        title=titulo,
        description=(
            f"{desenhar_tabuleiro(game['board'])}\n"
            f"{descricao}"
        ),
        color=discord.Color.gold()
    )

    if resultado_elo:
        embed.add_field(
            name="ELO das Brancas",
            value=(
                f"{resultado_elo['white_elo']} "
                f"({resultado_elo['delta_white']:+})"
            ),
            inline=True
        )

        embed.add_field(
            name="ELO das Pretas",
            value=(
                f"{resultado_elo['black_elo']} "
                f"({resultado_elo['delta_black']:+})"
            ),
            inline=True
        )

        if not resultado_elo["saved"]:
            embed.add_field(
                name="⚠️ Aviso",
                value="Não foi possível salvar os dados no arquivo.",
                inline=False
            )

    return embed


# ============================================================
# 7. INTERFACE DO TABULEIRO
# ============================================================

class ChessBoardView(discord.ui.View):

    def __init__(self, game):
        super().__init__(timeout=None)

        self.game = game
        self.page = 0

        self.rebuild()

    def rebuild(self):
        self.clear_items()

        board = self.game["board"]

        if self.game["finished"]:
            return

        pecas = []

        for square, piece in board.piece_map().items():
            if piece.color == board.turn:
                pecas.append(
                    discord.SelectOption(
                        label=(
                            f"{piece.symbol()} — "
                            f"{chess.square_name(square)}"
                        ),
                        value=str(square)
                    )
                )

        if pecas:
            select = discord.ui.Select(
                placeholder="Escolha uma peça para mover",
                options=pecas,
                row=0
            )

            select.callback = self.selecionar_peca

            self.add_item(select)

        if self.game["selected_square"] is not None:
            cancelar = discord.ui.Button(
                label="Cancelar seleção",
                style=discord.ButtonStyle.danger,
                row=0
            )

            cancelar.callback = self.cancelar_selecao

            self.add_item(cancelar)

            origem = self.game["selected_square"]

            movimentos = [
                move
                for move in board.legal_moves
                if move.from_square == origem
            ]

            movimentos.sort(
                key=lambda move: (
                    chess.square_rank(move.to_square),
                    chess.square_file(move.to_square)
                )
            )

            total_paginas = max(
                1,
                (
                    len(movimentos)
                    + MOVIMENTOS_POR_PAGINA
                    - 1
                ) // MOVIMENTOS_POR_PAGINA
            )

            self.page = min(
                self.page,
                total_paginas - 1
            )

            inicio = self.page * MOVIMENTOS_POR_PAGINA
            fim = inicio + MOVIMENTOS_POR_PAGINA

            pagina = movimentos[inicio:fim]

            for indice, move in enumerate(pagina):
                nome = chess.square_name(move.to_square)

                if move.promotion:
                    nome += " (promoção)"

                botao = discord.ui.Button(
                    label=nome,
                    style=discord.ButtonStyle.primary,
                    row=1 + indice // 5
                )

                botao.callback = self.criar_callback_movimento(move)

                self.add_item(botao)

            if total_paginas > 1:
                anterior = discord.ui.Button(
                    label="◀",
                    style=discord.ButtonStyle.secondary,
                    disabled=self.page <= 0,
                    row=4
                )

                proxima = discord.ui.Button(
                    label="▶",
                    style=discord.ButtonStyle.secondary,
                    disabled=self.page >= total_paginas - 1,
                    row=4
                )

                anterior.callback = self.pagina_anterior
                proxima.callback = self.proxima_pagina

                self.add_item(anterior)
                self.add_item(proxima)

        if self.game["draw_offer"] is None:

            btn_empate = discord.ui.Button(
                label="Oferecer empate",
                emoji="🤝",
                style=discord.ButtonStyle.secondary,
                row=4
            )

            btn_empate.callback = self.callback_oferecer_empate

            self.add_item(btn_empate)

        else:

            btn_aceitar = discord.ui.Button(
                label="Aceitar empate",
                emoji="✅",
                style=discord.ButtonStyle.success,
                row=4
            )

            btn_recusar = discord.ui.Button(
                label="Recusar empate",
                emoji="❌",
                style=discord.ButtonStyle.danger,
                row=4
            )

            btn_aceitar.callback = self.callback_aceitar_empate
            btn_recusar.callback = self.callback_recusar_empate

            self.add_item(btn_aceitar)
            self.add_item(btn_recusar)

        btn_desistir = discord.ui.Button(
            label="Desistir",
            emoji="🏳️",
            style=discord.ButtonStyle.danger,
            row=4
        )

        btn_desistir.callback = self.callback_desistir_clique

        self.add_item(btn_desistir)

    async def validar_participante(self, interaction):
        if self.game["finished"]:
            await interaction.response.send_message(
                "Essa partida já terminou.",
                ephemeral=True
            )
            return False

        if active_games.get(self.game["channel_id"]) is not self.game:
            await interaction.response.send_message(
                "Essa partida não está mais ativa.",
                ephemeral=True
            )
            return False

        if interaction.user.id not in (
            self.game["white"],
            self.game["black"]
        ):
            await interaction.response.send_message(
                "Você não participa desta partida.",
                ephemeral=True
            )
            return False

        return True

    async def validar_jogador(self, interaction):
        if not await self.validar_participante(interaction):
            return False

        if interaction.user.id != jogador_da_vez(self.game):
            await interaction.response.send_message(
                "Não é sua vez de jogar.",
                ephemeral=True
            )
            return False

        return True

    async def selecionar_peca(self, interaction):
        async with obter_lock(self.game["channel_id"]):

            if not await self.validar_jogador(interaction):
                return

            valores = interaction.data.get("values", [])

            if not valores:
                await interaction.response.send_message(
                    "Nenhuma peça foi selecionada.",
                    ephemeral=True
                )
                return

            square = int(valores[0])

            piece = self.game["board"].piece_at(square)

            if piece is None or piece.color != self.game["board"].turn:
                await interaction.response.send_message(
                    "Essa peça não pode ser selecionada.",
                    ephemeral=True
                )
                return

            self.game["selected_square"] = square

            self.page = 0

            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    def criar_callback_movimento(self, move):

        async def callback(interaction):
            async with obter_lock(self.game["channel_id"]):

                if not await self.validar_jogador(interaction):
                    return

                board = self.game["board"]

                if not board.is_legal(move):
                    await interaction.response.send_message(
                        "Esse movimento não é mais válido.",
                        ephemeral=True
                    )
                    return

                if move.promotion:

                    promotion_view = PromotionView(
                        self.game,
                        move,
                        self
                    )

                    await interaction.response.edit_message(
                        content="Escolha a peça da promoção:",
                        embed=criar_embed_partida(self.game),
                        view=promotion_view
                    )

                    promotion_view.message = interaction.message

                    return

                if self.game["draw_offer"] == interaction.user.id:
                    self.game["draw_offer"] = None

                board.push(move)

                self.game["moves"].append(move.uci())

                self.game["selected_square"] = None

                self.page = 0

                await atualizar_tabuleiro(
                    interaction,
                    self.game,
                    self
                )

        return callback

    async def pagina_anterior(self, interaction):
        async with obter_lock(self.game["channel_id"]):

            if not await self.validar_jogador(interaction):
                return

            self.page = max(0, self.page - 1)

            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    async def proxima_pagina(self, interaction):
        async with obter_lock(self.game["channel_id"]):

            if not await self.validar_jogador(interaction):
                return

            self.page += 1

            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    async def cancelar_selecao(self, interaction):
        async with obter_lock(self.game["channel_id"]):

            if not await self.validar_jogador(interaction):
                return

            self.game["selected_square"] = None

            self.page = 0

            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    async def callback_oferecer_empate(self, interaction):
        async with obter_lock(self.game["channel_id"]):

            if not await self.validar_participante(interaction):
                return

            if self.game["draw_offer"] is not None:
                await interaction.response.send_message(
                    "Já existe uma oferta de empate pendente.",
                    ephemeral=True
                )
                return

            self.game["draw_offer"] = interaction.user.id

            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    async def callback_aceitar_empate(self, interaction):
        async with obter_lock(self.game["channel_id"]):

            if not await self.validar_participante(interaction):
                return

            ofertante = self.game["draw_offer"]

            if ofertante is None:
                await interaction.response.send_message(
                    "Não existe oferta de empate pendente.",
                    ephemeral=True
                )
                return

            if interaction.user.id == ofertante:
                await interaction.response.send_message(
                    "Você não pode aceitar sua própria oferta.",
                    ephemeral=True
                )
                return

            resultado_elo = finalizar_partida(
                self.game,
                "1/2-1/2",
                "Empate acordado"
            )

            if resultado_elo is None:
                await interaction.response.send_message(
                    "Não foi possível finalizar essa partida.",
                    ephemeral=True
                )
                return

            self.game["draw_offer"] = None

            embed = criar_embed_final(
                self.game,
                "🤝 DISCHESS — Empate acordado",
                "**Os jogadores concordaram com o empate!**",
                resultado_elo
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=None
            )

    async def callback_recusar_empate(self, interaction):
        async with obter_lock(self.game["channel_id"]):

            if not await self.validar_participante(interaction):
                return

            ofertante = self.game["draw_offer"]

            if ofertante is None:
                await interaction.response.send_message(
                    "Não existe oferta de empate pendente.",
                    ephemeral=True
                )
                return

            if interaction.user.id == ofertante:
                await interaction.response.send_message(
                    "Você não pode recusar sua própria oferta.",
                    ephemeral=True
                )
                return

            self.game["draw_offer"] = None

            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    async def callback_desistir_clique(self, interaction):
        async with obter_lock(self.game["channel_id"]):

            if not await self.validar_participante(interaction):
                return

            confirm_view = ConfirmDesistenciaView(
                self.game,
                self
            )

            await interaction.response.send_message(
                "⚠️ Tem certeza de que deseja desistir da partida?",
                view=confirm_view,
                ephemeral=True
            )

            confirm_view.message = await interaction.original_response()


# ============================================================
# 8. PROMOÇÃO DE PEÃO
# ============================================================

class PromotionView(discord.ui.View):

    def __init__(self, game, move, parent_view):
        super().__init__(timeout=TEMPO_PROMOCAO)

        self.game = game
        self.move = move
        self.parent_view = parent_view

        self.message = None
        self.resolved = False

    async def on_timeout(self):
        async with obter_lock(self.game["channel_id"]):

            if self.resolved:
                return

            self.resolved = True

            if self.game["finished"]:
                return

            if active_games.get(self.game["channel_id"]) is not self.game:
                return

            self.parent_view.rebuild()

            if self.message:
                try:
                    await self.message.edit(
                        content=(
                            "⌛ Tempo de promoção esgotado. "
                            "Escolha a peça novamente."
                        ),
                        embed=criar_embed_partida(self.game),
                        view=self.parent_view
                    )
                except discord.HTTPException:
                    logging.exception(
                        "Não foi possível restaurar o tabuleiro após o timeout."
                    )

    async def promover(self, interaction, piece_type):
        async with obter_lock(self.game["channel_id"]):

            if self.resolved:
                await interaction.response.send_message(
                    "Essa escolha de promoção expirou.",
                    ephemeral=True
                )
                return

            if self.game["finished"]:
                await interaction.response.send_message(
                    "Essa partida já terminou.",
                    ephemeral=True
                )
                return

            if active_games.get(self.game["channel_id"]) is not self.game:
                await interaction.response.send_message(
                    "Essa partida não está mais ativa.",
                    ephemeral=True
                )
                return

            if interaction.user.id not in (
                self.game["white"],
                self.game["black"]
            ):
                await interaction.response.send_message(
                    "Você não participa desta partida.",
                    ephemeral=True
                )
                return

            if interaction.user.id != jogador_da_vez(self.game):
                await interaction.response.send_message(
                    "Não é sua vez de jogar.",
                    ephemeral=True
                )
                return

            move_final = chess.Move(
                self.move.from_square,
                self.move.to_square,
                promotion=piece_type
            )

            board = self.game["board"]

            if not board.is_legal(move_final):
                await interaction.response.send_message(
                    "Essa promoção não é mais válida.",
                    ephemeral=True
                )
                return

            if self.game["draw_offer"] == interaction.user.id:
                self.game["draw_offer"] = None

            board.push(move_final)

            self.game["moves"].append(move_final.uci())

            self.game["selected_square"] = None

            self.resolved = True

            self.parent_view.page = 0
            self.parent_view.rebuild()

            await atualizar_tabuleiro(
                interaction,
                self.game,
                self.parent_view
            )

    @discord.ui.button(
        label="Dama",
        emoji="♕",
        style=discord.ButtonStyle.primary
    )
    async def dama(self, interaction, button):
        await self.promover(interaction, chess.QUEEN)

    @discord.ui.button(
        label="Torre",
        emoji="♖",
        style=discord.ButtonStyle.primary
    )
    async def torre(self, interaction, button):
        await self.promover(interaction, chess.ROOK)

    @discord.ui.button(
        label="Bispo",
        emoji="♗",
        style=discord.ButtonStyle.primary
    )
    async def bispo(self, interaction, button):
        await self.promover(interaction, chess.BISHOP)

    @discord.ui.button(
        label="Cavalo",
        emoji="♘",
        style=discord.ButtonStyle.primary
    )
    async def cavalo(self, interaction, button):
        await self.promover(interaction, chess.KNIGHT)


# ============================================================
# 8.1 CONFIRMAÇÃO DE DESISTÊNCIA
# ============================================================

class ConfirmDesistenciaView(discord.ui.View):

    def __init__(self, game, board_view):
        super().__init__(timeout=TEMPO_CONFIRMACAO)

        self.game = game
        self.board_view = board_view
        self.message = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

        if self.message:
            try:
                await self.message.edit(
                    content="⌛ A confirmação de desistência expirou.",
                    view=self
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(
        label="Sim, desistir",
        style=discord.ButtonStyle.danger
    )
    async def confirmar(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        async with obter_lock(self.game["channel_id"]):

            if self.game["finished"]:
                await interaction.response.send_message(
                    "Essa partida já terminou.",
                    ephemeral=True
                )
                return

            if active_games.get(self.game["channel_id"]) is not self.game:
                await interaction.response.send_message(
                    "Essa partida não está mais ativa.",
                    ephemeral=True
                )
                return

            if interaction.user.id not in (
                self.game["white"],
                self.game["black"]
            ):
                await interaction.response.send_message(
                    "Você não participa desta partida.",
                    ephemeral=True
                )
                return

            if interaction.user.id == self.game["white"]:
                resultado = "0-1"
                vencedor = "⚫ Pretas venceram!"
            else:
                resultado = "1-0"
                vencedor = "⚪ Brancas venceram!"

            resultado_elo = finalizar_partida(
                self.game,
                resultado,
                "Desistência"
            )

            if resultado_elo is None:
                await interaction.response.send_message(
                    "Não foi possível finalizar essa partida.",
                    ephemeral=True
                )
                return

            embed = criar_embed_final(
                self.game,
                "♟️ DISCHESS — Desistência",
                (
                    f"{interaction.user.mention} desistiu da partida.\n"
                    f"**{vencedor}**"
                ),
                resultado_elo
            )

            for item in self.children:
                item.disabled = True

            await interaction.response.edit_message(
                content="Desistência confirmada.",
                view=self
            )

            if self.game.get("message"):
                try:
                    await self.game["message"].edit(
                        content=None,
                        embed=embed,
                        view=None
                    )
                except discord.HTTPException:
                    logging.exception(
                        "Não foi possível atualizar o tabuleiro após desistência."
                    )

    @discord.ui.button(
        label="Cancelar",
        style=discord.ButtonStyle.secondary
    )
    async def cancelar(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="Ação cancelada.",
            view=self
        )


# ============================================================
# 9. ATUALIZAÇÃO DO TABULEIRO
# ============================================================

async def atualizar_tabuleiro(interaction, game, view):
    board = game["board"]

    if board.is_game_over():
        resultado = board.result()
        motivo = motivo_fim(board)

        resultado_elo = finalizar_partida(
            game,
            resultado,
            motivo
        )

        if resultado_elo is None:
            await interaction.response.send_message(
                "Não foi possível finalizar a partida.",
                ephemeral=True
            )
            return

        if resultado == "1-0":
            vencedor = "⚪ Brancas venceram!"

        elif resultado == "0-1":
            vencedor = "⚫ Pretas venceram!"

        else:
            vencedor = "🤝 A partida terminou empatada!"

        embed = criar_embed_final(
            game,
            "♟️ DISCHESS — Partida encerrada",
            f"**{vencedor}**\nMotivo: {motivo}",
            resultado_elo
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=None
        )

        return

    view.rebuild()

    await interaction.response.edit_message(
        content=None,
        embed=criar_embed_partida(game),
        view=view
    )


# ============================================================
# 10. DESAFIOS
# ============================================================

class ChallengeView(discord.ui.View):

    def __init__(
        self,
        challenge_id,
        challenger,
        opponent,
        channel_id
    ):
        super().__init__(timeout=TEMPO_DESAFIO)

        self.challenge_id = challenge_id
        self.challenger = challenger
        self.opponent = opponent
        self.channel_id = channel_id

        self.message = None
        self.resolved = False

    def desafio_valido(self):
        challenge = pending_challenges.get(self.challenge_id)

        if challenge is None:
            return False

        return (
            challenge["challenger"] == self.challenger
            and challenge["opponent"] == self.opponent
            and challenge["channel_id"] == self.channel_id
        )

    async def on_timeout(self):
        async with challenge_lock:

            if self.resolved:
                return

            self.resolved = True

            if self.desafio_valido():
                pending_challenges.pop(self.challenge_id, None)

            for item in self.children:
                item.disabled = True

            if self.message:
                try:
                    await self.message.edit(
                        content="⌛ O desafio expirou.",
                        view=self
                    )
                except discord.HTTPException:
                    pass

    @discord.ui.button(
        label="Aceitar",
        emoji="✅",
        style=discord.ButtonStyle.success
    )
    async def aceitar(self, interaction, button):
        async with challenge_lock:

            if interaction.user.id != self.opponent:
                await interaction.response.send_message(
                    "Somente o jogador desafiado pode aceitar.",
                    ephemeral=True
                )
                return

            if self.resolved or not self.desafio_valido():
                await interaction.response.send_message(
                    "Esse desafio já foi resolvido ou expirou.",
                    ephemeral=True
                )
                return

            if jogador_em_jogo(self.challenger) or jogador_em_jogo(self.opponent):
                await interaction.response.send_message(
                    "Um dos jogadores já está em uma partida.",
                    ephemeral=True
                )
                return

            if jogador_em_desafio(self.challenger) or jogador_em_desafio(self.opponent):
                # O desafio atual ainda está pendente, então
                # verificamos apenas se existem outros desafios.
                outros = [
                    desafio_id
                    for desafio_id, desafio in pending_challenges.items()
                    if desafio_id != self.challenge_id
                    and (
                        self.challenger in (
                            desafio["challenger"],
                            desafio["opponent"]
                        )
                        or self.opponent in (
                            desafio["challenger"],
                            desafio["opponent"]
                        )
                    )
                ]

                if outros:
                    await interaction.response.send_message(
                        "Um dos jogadores está em outro desafio pendente.",
                        ephemeral=True
                    )
                    return

            if self.channel_id in active_games:
                await interaction.response.send_message(
                    "Já existe uma partida neste canal.",
                    ephemeral=True
                )
                return

            pending_challenges.pop(self.challenge_id, None)

            self.resolved = True

            game = criar_partida(
                self.challenger,
                self.opponent,
                self.channel_id
            )

            active_games[self.channel_id] = game

            view = ChessBoardView(game)

            game["message"] = interaction.message

            await interaction.response.edit_message(
                content=None,
                embed=criar_embed_partida(game),
                view=view
            )

    @discord.ui.button(
        label="Recusar",
        emoji="❌",
        style=discord.ButtonStyle.danger
    )
    async def recusar(self, interaction, button):
        async with challenge_lock:

            if interaction.user.id not in (
                self.challenger,
                self.opponent
            ):
                await interaction.response.send_message(
                    "Você não participa deste desafio.",
                    ephemeral=True
                )
                return

            if self.resolved or not self.desafio_valido():
                await interaction.response.send_message(
                    "Esse desafio já foi resolvido ou expirou.",
                    ephemeral=True
                )
                return

            pending_challenges.pop(self.challenge_id, None)

            self.resolved = True

            for item in self.children:
                item.disabled = True

            await interaction.response.edit_message(
                content="❌ O desafio foi recusado/cancelado.",
                embed=None,
                view=self
            )


# ============================================================
# 11. COMANDO: DESAFIAR
# ============================================================

@bot.tree.command(
    name="desafiar",
    description="Desafie outro jogador para uma partida de xadrez."
)
@app_commands.describe(
    jogador="Jogador que você deseja desafiar"
)
async def desafiar(
    interaction: discord.Interaction,
    jogador: discord.Member
):
    if jogador.bot:
        await interaction.response.send_message(
            "Você não pode desafiar bots.",
            ephemeral=True
        )
        return

    if jogador.id == interaction.user.id:
        await interaction.response.send_message(
            "Você não pode desafiar a si mesmo.",
            ephemeral=True
        )
        return

    async with challenge_lock:

        if jogador_ocupado(interaction.user.id):
            await interaction.response.send_message(
                "Você já está em uma partida ou desafio pendente.",
                ephemeral=True
            )
            return

        if jogador_ocupado(jogador.id):
            await interaction.response.send_message(
                "Esse jogador já está em uma partida ou desafio pendente.",
                ephemeral=True
            )
            return

        if interaction.channel_id in active_games:
            await interaction.response.send_message(
                "Já existe uma partida neste canal.",
                ephemeral=True
            )
            return

        challenge_id = str(uuid.uuid4())

        view = ChallengeView(
            challenge_id,
            interaction.user.id,
            jogador.id,
            interaction.channel_id
        )

        pending_challenges[challenge_id] = {
            "challenger": interaction.user.id,
            "opponent": jogador.id,
            "channel_id": interaction.channel_id
        }

    try:
        await interaction.response.send_message(
            f"♟️ {jogador.mention}, você foi desafiado por "
            f"{interaction.user.mention}!\n"
            f"Você tem {TEMPO_DESAFIO} segundos para responder.",
            view=view
        )

        view.message = await interaction.original_response()

    except Exception:
        async with challenge_lock:
            pending_challenges.pop(challenge_id, None)

        raise


# ============================================================
# 12. COMANDO: PERFIL
# ============================================================

@bot.tree.command(
    name="perfil",
    description="Veja seu perfil ou o perfil de outro jogador."
)
@app_commands.describe(
    jogador="Jogador cujo perfil deseja consultar"
)
async def perfil(
    interaction: discord.Interaction,
    jogador: discord.Member = None
):
    jogador = jogador or interaction.user

    dados = obter_dados_usuario(jogador.id)

    elo = dados["elo"]
    peak_elo = dados["peak_elo"]

    partidas = (
        dados["wins"]
        + dados["losses"]
        + dados["draws"]
    )

    taxa_vitoria = (
        dados["wins"] / partidas * 100
        if partidas > 0
        else 0
    )

    patente = obter_patente(elo)

    if elo < 500:
        proximo_elo = 500
        proxima_patente = "Amador"

    elif elo < 1000:
        proximo_elo = 1000
        proxima_patente = "Intermediário"

    elif elo < 1500:
        proximo_elo = 1500
        proxima_patente = "Mestre"

    elif elo < 2000:
        proximo_elo = 2000
        proxima_patente = "Grão-Mestre"

    elif elo < 3000:
        proximo_elo = 3000
        proxima_patente = "Lenda do Xadrez"

    elif elo < 50000:
        proximo_elo = 50000
        proxima_patente = "Deus do Xadrez"

    else:
        proximo_elo = None
        proxima_patente = None

    if proximo_elo is not None:
        elo_atual_patente = {
            "Iniciante": 0,
            "Amador": 500,
            "Intermediário": 1000,
            "Mestre": 1500,
            "Grão-Mestre": 2000,
            "Lenda do Xadrez": 3000
        }.get(patente, 0)

        progresso = (
            (elo - elo_atual_patente)
            / (proximo_elo - elo_atual_patente)
        ) * 100

        progresso = max(0, min(100, progresso))

        blocos_preenchidos = round(progresso / 10)

        barra = (
            "▰" * blocos_preenchidos
            + "▱" * (10 - blocos_preenchidos)
        )

        texto_progresso = (
            f"{barra} `{progresso:.0f}%` "
            f"({elo}/{proximo_elo} ELO)"
        )

    else:
        texto_progresso = (
            "🏆 Você alcançou a patente máxima!"
        )

    embed = discord.Embed(
        title=f"👤 Perfil de: {jogador.display_name}",
        color=discord.Color.blurple()
    )

    embed.set_thumbnail(
        url=jogador.display_avatar.url
    )

    embed.add_field(
        name="🏆 ELO Atual",
        value=f"`{elo}`",
        inline=False
    )

    embed.add_field(
        name="📈 Maior ELO (Peak)",
        value=f"`{peak_elo}`",
        inline=False
    )

    embed.add_field(
        name="👑 Patente",
        value=f"`{patente}`",
        inline=False
    )

    embed.add_field(
        name="⚔️ Partidas Jogadas",
        value=f"`{partidas}`",
        inline=False
    )

    embed.add_field(
        name="🎯 Win Rate",
        value=f"`{taxa_vitoria:.1f}%`",
        inline=False
    )

    embed.add_field(
        name="📊 V / D / E",
        value=(
            f"`{dados['wins']}W` / "
            f"`{dados['losses']}L` / "
            f"`{dados['draws']}D`"
        ),
        inline=False
    )

    embed.add_field(
        name=f"🚀 Progresso para {proxima_patente or 'Deus do Xadrez'}",
        value=texto_progresso,
        inline=False
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# 13. COMANDO: RANKING
# ============================================================

@bot.tree.command(
    name="ranking",
    description="Veja o ranking de ELO do DISCHESS."
)
async def ranking(interaction: discord.Interaction):
    jogadores = []

    for user_id, dados in user_data.items():

        if isinstance(dados, int):
            elo = dados
        else:
            # O padrão correto para jogadores novos é 100.
            elo = dados.get("elo", 100)

        jogadores.append(
            (int(user_id), elo)
        )

    jogadores.sort(
        key=lambda item: item[1],
        reverse=True
    )

    if not jogadores:
        await interaction.response.send_message(
            "Ainda não existem jogadores no ranking."
        )
        return

    linhas = []

    for posicao, (user_id, elo) in enumerate(
        jogadores[:10],
        start=1
    ):
        membro = (
            interaction.guild.get_member(user_id)
            if interaction.guild
            else None
        )

        if membro:
            nome = membro.display_name
        else:
            usuario = bot.get_user(user_id)
            nome = usuario.name if usuario else f"Jogador {user_id}"

        linhas.append(
            f"**{posicao}.** {nome} — **{elo} ELO**"
        )

    embed = discord.Embed(
        title="🏆 Ranking — DISCHESS",
        description="\n".join(linhas),
        color=discord.Color.gold()
    )

    embed.set_footer(
        text="Ranking dos dados desta instalação do DISCHESS."
    )

    await interaction.response.send_message(embed=embed)


# ============================================================
# 14. COMANDO: HISTÓRICO
# ============================================================

@bot.tree.command(
    name="historico",
    description="Veja as partidas recentes de um jogador."
)
@app_commands.describe(
    jogador="Jogador cujo histórico deseja consultar"
)
async def historico(
    interaction: discord.Interaction,
    jogador: discord.Member = None
):
    jogador = jogador or interaction.user

    dados = obter_dados_usuario(jogador.id)

    historico_partidas = dados["history"]

    if not historico_partidas:
        await interaction.response.send_message(
            "Esse jogador ainda não possui partidas registradas.",
            ephemeral=True
        )
        return

    linhas = []

    for partida in historico_partidas[:10]:

        if int(partida["white"]) == jogador.id:
            adversario_id = int(partida["black"])
            resultado = partida["result"]

            if resultado == "1-0":
                situacao = "Vitória"
            elif resultado == "0-1":
                situacao = "Derrota"
            else:
                situacao = "Empate"

            delta = partida["white_delta"]

        else:
            adversario_id = int(partida["white"])
            resultado = partida["result"]

            if resultado == "0-1":
                situacao = "Vitória"
            elif resultado == "1-0":
                situacao = "Derrota"
            else:
                situacao = "Empate"

            delta = partida["black_delta"]

        adversario = bot.get_user(adversario_id)

        nome_adversario = (
            adversario.name
            if adversario
            else str(adversario_id)
        )

        linhas.append(
            f"**{situacao}** contra {nome_adversario} "
            f"— {delta:+} ELO\n"
            f"Partida `{partida['id'][:8]}`"
        )

    embed = discord.Embed(
        title=f"📜 Histórico de {jogador.display_name}",
        description="\n".join(linhas),
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(embed=embed)


# ============================================================
# 15. COMANDO: ANALISAR
# ============================================================

@bot.tree.command(
    name="analisar",
    description="Veja o PGN de uma partida."
)
@app_commands.describe(
    partida_id="ID da partida que deseja consultar (opcional)"
)
async def analisar(
    interaction: discord.Interaction,
    partida_id: str = None
):
    dados = obter_dados_usuario(interaction.user.id)

    historico_partidas = dados["history"]

    if not historico_partidas:
        await interaction.response.send_message(
            "Você ainda não possui partidas para analisar.",
            ephemeral=True
        )
        return

    partida = None

    if partida_id:
        for item in historico_partidas:
            if item["id"].upper() == partida_id.upper():
                partida = item
                break
    else:
        partida = historico_partidas[0]

    if not partida:
        await interaction.response.send_message(
            "Não encontrei essa partida no seu histórico.",
            ephemeral=True
        )
        return

    pgn = partida.get("pgn", "")

    if not pgn:
        await interaction.response.send_message(
            "Essa partida não possui PGN salvo.",
            ephemeral=True
        )
        return

    if len(pgn) > 1800:
        pgn = pgn[:1800] + "\n... (PGN reduzido)"

    embed = discord.Embed(
        title=f"♟️ PGN da partida {partida['id'][:8]}",
        description=(
            f"Resultado: **{partida['result']}**\n"
            f"Motivo: **{partida['reason']}**\n\n"
            f"```pgn\n{pgn}\n```"
        ),
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# 16. COMANDO: RESETAR
# ============================================================

class ConfirmResetView(discord.ui.View):

    def __init__(self, ids_reset, escopo, nomes):
        super().__init__(timeout=TEMPO_CONFIRMACAO)

        self.ids_reset = ids_reset
        self.escopo = escopo
        self.nomes = nomes
        self.resolvido = False

    async def on_timeout(self):
        if self.resolvido:
            return

        self.resolvido = True

        for item in self.children:
            item.disabled = True

        try:
            await self.message.edit(
                content="⌛ A confirmação do reset expirou.",
                view=self
            )
        except (discord.HTTPException, AttributeError):
            pass

    @discord.ui.button(
        label="Confirmar reset",
        emoji="⚠️",
        style=discord.ButtonStyle.danger
    )
    async def confirmar(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if interaction.user.id not in USUARIOS_AUTORIZADOS_RESET:
            await interaction.response.send_message(
                "❌ Você não tem autorização para isso.",
                ephemeral=True
            )
            return

        if self.resolvido:
            await interaction.response.send_message(
                "Essa confirmação já foi utilizada.",
                ephemeral=True
            )
            return

        async with reset_lock:
            async with challenge_lock:

                # Revalidar se algum selecionado entrou em
                # uma partida ou desafio enquanto aguardava.
                ocupados = [
                    user_id
                    for user_id in self.ids_reset
                    if jogador_ocupado(int(user_id))
                ]

                if ocupados:
                    await interaction.response.send_message(
                        "❌ O reset foi cancelado. "
                        "Um ou mais jogadores estão em partida "
                        "ou desafio pendente.",
                        ephemeral=True
                    )
                    return

                self.resolvido = True

                # Criar backup antes de alterar qualquer dado.
                try:
                    if os.path.exists(ELO_FILE):
                        shutil.copy2(ELO_FILE, BACKUP_FILE)
                    else:
                        # Se ainda não existe JSON, criar backup
                        # do estado atual em memória.
                        with open(
                            BACKUP_FILE,
                            "w",
                            encoding="utf-8"
                        ) as arquivo:
                            json.dump(
                                user_data,
                                arquivo,
                                ensure_ascii=False,
                                indent=4
                            )

                except Exception:
                    logging.exception(
                        "Não foi possível criar o backup do reset."
                    )

                    await interaction.response.send_message(
                        "❌ Não foi possível criar o backup. "
                        "Nenhum perfil foi alterado.",
                        ephemeral=True
                    )
                    return

                # Guardar estado anterior em memória.
                dados_anteriores = json.loads(
                    json.dumps(user_data)
                )

                # Executar reset.
                for user_id in self.ids_reset:
                    user_data[user_id] = dados_padrao()

                # Salvar o novo estado.
                salvou = salvar_dados()

                if not salvou:
                    user_data.clear()
                    user_data.update(dados_anteriores)

                    # Tentar recuperar o arquivo anterior.
                    try:
                        shutil.copy2(BACKUP_FILE, ELO_FILE)
                    except Exception:
                        logging.exception(
                            "Falha ao restaurar o arquivo após reset."
                        )

                    await interaction.response.send_message(
                        "❌ O salvamento falhou. "
                        "Os dados anteriores foram restaurados "
                        "em memória. Confira o backup.",
                        ephemeral=True
                    )
                    return

        for item in self.children:
            item.disabled = True

        if self.escopo == "me":
            mensagem = (
                "✅ **Seu perfil foi resetado!**\n\n"
                "🏆 ELO: 100\n"
                "📈 Peak: 100\n"
                "⚔️ Partidas: 0\n"
                "🎯 Win Rate: 0%\n"
                "📊 V / D / E: 0 / 0 / 0"
            )

        elif self.escopo == "all":
            mensagem = (
                "✅ **Reset global concluído!**\n\n"
                f"👥 Perfis resetados: **{len(self.ids_reset)}**\n"
                "🏆 Todos voltaram para **100 ELO**.\n\n"
                "💾 Backup anterior criado."
            )

        else:
            mensagem = (
                "✅ **Reset dos jogadores concluído!**\n\n"
                f"👥 Perfis resetados: **{len(self.ids_reset)}**\n"
                "🏆 Os perfis selecionados voltaram para **100 ELO**.\n\n"
                "💾 Backup anterior criado.\n\n"
                + "\n".join(self.nomes)
            )

        await interaction.response.edit_message(
            content=mensagem,
            view=self
        )

    @discord.ui.button(
        label="Cancelar",
        emoji="✖️",
        style=discord.ButtonStyle.secondary
    )
    async def cancelar(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if interaction.user.id not in USUARIOS_AUTORIZADOS_RESET:
            await interaction.response.send_message(
                "❌ Você não tem autorização para isso.",
                ephemeral=True
            )
            return

        if self.resolvido:
            await interaction.response.send_message(
                "Essa confirmação já foi utilizada.",
                ephemeral=True
            )
            return

        self.resolvido = True

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="❌ Reset cancelado. Nenhum perfil foi alterado.",
            view=self
        )


@bot.tree.command(
    name="resetar",
    description="Reseta um ou mais perfis do DISCHESS."
)
@app_commands.choices(escopo=[
    app_commands.Choice(
        name="Meu perfil",
        value="me"
    ),
    app_commands.Choice(
        name="Todos globalmente",
        value="all"
    ),
    app_commands.Choice(
        name="Jogadores marcados",
        value="selected"
    )
])
@app_commands.describe(
    escopo="Escolha quem terá o perfil resetado.",
    jogador1="Primeiro jogador.",
    jogador2="Segundo jogador.",
    jogador3="Terceiro jogador.",
    jogador4="Quarto jogador.",
    jogador5="Quinto jogador.",
    jogador6="Sexto jogador.",
    jogador7="Sétimo jogador.",
    jogador8="Oitavo jogador.",
    jogador9="Nono jogador.",
    jogador10="Décimo jogador."
)
async def resetar(
    interaction: discord.Interaction,
    escopo: app_commands.Choice[str],
    jogador1: discord.User = None,
    jogador2: discord.User = None,
    jogador3: discord.User = None,
    jogador4: discord.User = None,
    jogador5: discord.User = None,
    jogador6: discord.User = None,
    jogador7: discord.User = None,
    jogador8: discord.User = None,
    jogador9: discord.User = None,
    jogador10: discord.User = None
):
    # Autorização.
    if interaction.user.id not in USUARIOS_AUTORIZADOS_RESET:
        await interaction.response.send_message(
            "❌ Você não tem autorização para usar este comando.",
            ephemeral=True
        )
        return

    marcados = [
        jogador for jogador in [
            jogador1,
            jogador2,
            jogador3,
            jogador4,
            jogador5,
            jogador6,
            jogador7,
            jogador8,
            jogador9,
            jogador10
        ]
        if jogador is not None
    ]

    if escopo.value == "me":
        ids_reset = [str(interaction.user.id)]

    elif escopo.value == "all":
        ids_reset = list(user_data.keys())

    elif escopo.value == "selected":
        if not marcados:
            await interaction.response.send_message(
                "❌ Marque pelo menos um jogador.",
                ephemeral=True
            )
            return

        ids_reset = list({
            str(jogador.id)
            for jogador in marcados
        })

    else:
        return

    if not ids_reset:
        await interaction.response.send_message(
            "Não existem perfis registrados para resetar.",
            ephemeral=True
        )
        return

    # Não permitir reset de jogadores em partidas ou desafios.
    ocupados = [
        user_id
        for user_id in ids_reset
        if jogador_ocupado(int(user_id))
    ]

    if ocupados:
        await interaction.response.send_message(
            "❌ Não é possível resetar agora. "
            "Um ou mais jogadores estão em partidas "
            "ou desafios pendentes.",
            ephemeral=True
        )
        return

    if escopo.value == "me":
        descricao = (
            "Você está prestes a resetar **seu próprio perfil**.\n"
            "Seu ELO, Peak, estatísticas e histórico serão apagados."
        )

        nomes = []

    elif escopo.value == "all":
        descricao = (
            f"⚠️ Você está prestes a resetar **TODOS os perfis globais** "
            f"registrados nesta instalação.\n\n"
            f"Perfis afetados: **{len(ids_reset)}**\n\n"
            "Todos voltarão para 100 ELO e perderão suas estatísticas "
            "e históricos."
        )

        nomes = []

    else:
        nomes = [
            f"• {jogador.mention}"
            for jogador in marcados
        ]

        descricao = (
            "Você está prestes a resetar os seguintes jogadores:\n\n"
            + "\n".join(nomes)
            + "\n\nELO, Peak, estatísticas e históricos serão apagados."
        )

    descricao += (
        "\n\n💾 Um backup será criado antes do reset."
        "\nEssa ação não pode ser desfeita pelo próprio comando."
    )

    view = ConfirmResetView(
        ids_reset,
        escopo.value,
        nomes
    )

    await interaction.response.send_message(
        descricao,
        view=view,
        ephemeral=True
    )

    view.message = await interaction.original_response()


# ============================================================
# 17. INICIALIZAÇÃO
# ============================================================

@bot.event
async def on_ready():
    global data_loaded

    if not data_loaded:
        carregar_dados()
        data_loaded = True

    try:
        sincronizados = await bot.tree.sync()

        logging.info(
            "%s comandos sincronizados.",
            len(sincronizados)
        )

    except Exception:
        logging.exception(
            "Erro ao sincronizar os comandos."
        )

    logging.info(
        "DISCHESS conectado como %s.",
        bot.user
    )


# ============================================================
# 18. EXECUÇÃO
# ============================================================

carregar_dados()
data_loaded = True

bot.run(TOKEN)