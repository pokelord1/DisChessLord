import os
import io
import json
import uuid
import asyncio
import tempfile
import logging
import shutil
import copy
import math
import secrets
from datetime import datetime, timezone, timedelta

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
ADMIN_FILE = os.path.join(DATA_DIR, "dischess_admin.json")

K_FACTOR = 32
TEMPO_DESAFIO = 120
TEMPO_PROMOCAO = 60
TEMPO_CONFIRMACAO = 30
MOVIMENTOS_POR_PAGINA = 15

USUARIOS_AUTORIZADOS_RESET = {
    1369324713498906786
}

USUARIOS_AUTORIZADOS_DEV = {
    1369324713498906786
}

PATENTES_NOMES = [
    "Iniciante",
    "Amador",
    "Intermediário",
    "Mestre",
    "Grão-Mestre",
    "Lenda do Xadrez I",
    "Lenda do Xadrez II",
    "Lenda do Xadrez III",
    "Lenda do Xadrez IV",
    "Lenda do Xadrez V",
    "Lenda do Xadrez VI",
    "Lenda do Xadrez VII",
    "Lenda do Xadrez VIII",
    "Lenda do Xadrez IX",
    "Lenda do Xadrez X",
    "Lenda do Xadrez XI",
    "Lenda do Xadrez XII",
    "Lenda do Xadrez XIII",
    "Lenda do Xadrez XIV",
    "Lenda do Xadrez XV",
    "Lenda do Xadrez XVI",
    "Lenda do Xadrez XVII",
    "Lenda do Xadrez XVIII",
    "Lenda do Xadrez XIX",
    "Lenda do Xadrez XX",
    "Lenda do Xadrez XXI",
    "Deus do Xadrez"
]

PATENTES = [
    (0, 499, "Iniciante"),
    (500, 999, "Amador"),
    (1000, 1499, "Intermediário"),
    (1500, 1999, "Mestre"),
    (2000, 2999, "Grão-Mestre"),
    (3000, 4999, "Lenda do Xadrez I"),
    (5000, 6999, "Lenda do Xadrez II"),
    (7000, 8999, "Lenda do Xadrez III"),
    (9000, 11999, "Lenda do Xadrez IV"),
    (12000, 13999, "Lenda do Xadrez V"),
    (14000, 15999, "Lenda do Xadrez VI"),
    (16000, 17999, "Lenda do Xadrez VII"),
    (18000, 20999, "Lenda do Xadrez VIII"),
    (21000, 22999, "Lenda do Xadrez IX"),
    (23000, 24999, "Lenda do Xadrez X"),
    (25000, 26999, "Lenda do Xadrez XI"),
    (27000, 29999, "Lenda do Xadrez XII"),
    (30000, 31999, "Lenda do Xadrez XIII"),
    (32000, 33999, "Lenda do Xadrez XIV"),
    (34000, 35999, "Lenda do Xadrez XV"),
    (36000, 38999, "Lenda do Xadrez XVI"),
    (39000, 40999, "Lenda do Xadrez XVII"),
    (41000, 42999, "Lenda do Xadrez XVIII"),
    (43000, 44999, "Lenda do Xadrez XIX"),
    (45000, 47999, "Lenda do Xadrez XX"),
    (48000, 49999, "Lenda do Xadrez XXI"),
    (50000, float("inf"), "Deus do Xadrez")
]

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

admin_data = {
    "events": {},
    "tournaments": {},
    "pieces": {},
    "settings": {}
}

active_games = {}
pending_challenges = {}

game_locks = {}
challenge_lock = asyncio.Lock()
reset_lock = asyncio.Lock()
admin_lock = asyncio.Lock()

data_loaded = False


# ============================================================
# 2. UTILITÁRIOS
# ============================================================

def agora_utc():
    return datetime.now(timezone.utc)


def data_iso():
    return agora_utc().isoformat()


def gerar_id():
    return str(uuid.uuid4()).upper()


def copiar_json(dados):
    return json.loads(
        json.dumps(dados, ensure_ascii=False)
    )


def salvar_json_atomico(caminho, dados):
    temporario = None

    try:
        diretorio = os.path.dirname(caminho) or "."
        os.makedirs(diretorio, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=diretorio,
            delete=False
        ) as arquivo:
            json.dump(
                dados,
                arquivo,
                ensure_ascii=False,
                indent=4
            )

            temporario = arquivo.name

        os.replace(temporario, caminho)

        return True

    except Exception:
        logging.exception(
            "Erro ao salvar arquivo %s.",
            caminho
        )

        if temporario and os.path.exists(temporario):
            try:
                os.remove(temporario)
            except OSError:
                pass

        return False


def membro_em_guild(guild, user_id):
    if guild is None:
        return None

    return guild.get_member(int(user_id))


def autorizado_dev(user_id):
    return int(user_id) in USUARIOS_AUTORIZADOS_DEV


def embed_erro(texto):
    return discord.Embed(
        title="❌ Erro",
        description=texto,
        color=discord.Color.red()
    )


def embed_sucesso(texto):
    return discord.Embed(
        title="✅ Sucesso",
        description=texto,
        color=discord.Color.green()
    )


# ============================================================
# 3. DADOS E PERSISTÊNCIA DOS JOGADORES
# ============================================================

def dados_padrao():
    return {
        "chesscoins": 0,
        "command_tickets": 0,
        "daily_last_claim": None,
        "achievements": [],
        "current_win_streak": 0,
        "ranking_ativo": False,
        "elo": 100,
        "peak_elo": 100,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "history": [],
        "patente_override": None,
        "titulo": None,
        "nickname": None
    }


def carregar_dados():
    global user_data

    if not os.path.exists(ELO_FILE):
        user_data = {}

        logging.info(
            "Arquivo de jogadores ainda não existe."
        )

        return

    try:
        with open(ELO_FILE, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)

        if not isinstance(dados, dict):
            raise ValueError("JSON de jogadores inválido.")

        carregados = {}

        for user_id, perfil in dados.items():

            if isinstance(perfil, int) and not isinstance(perfil, bool):
                novo = dados_padrao()
                novo["elo"] = perfil
                novo["peak_elo"] = perfil

                carregados[user_id] = novo
                continue

            if not isinstance(perfil, dict):
                raise ValueError(
                    f"Perfil inválido: {user_id}"
                )

            novo = dados_padrao()
            novo.update(perfil)

            if not isinstance(novo["history"], list):
                raise ValueError(
                    f"Histórico inválido: {user_id}"
                )

            for campo in (
                "elo",
                "peak_elo",
                "wins",
                "losses",
                "draws"
            ):
                if (
                    not isinstance(novo[campo], int)
                    or isinstance(novo[campo], bool)
                ):
                    raise ValueError(
                        f"Campo {campo} inválido: {user_id}"
                    )

            carregados[user_id] = novo

        user_data = carregados

        logging.info(
            "%s perfis carregados.",
            len(user_data)
        )

    except Exception:
        logging.exception("Erro ao carregar jogadores.")

        raise RuntimeError(
            "Arquivo de jogadores corrompido. "
            "Inicialização interrompida para proteger os dados."
        )


def salvar_dados():
    return salvar_json_atomico(
        ELO_FILE,
        user_data
    )


def obter_dados_usuario(user_id):
    user_id = str(user_id)
    if user_id not in user_data:
        user_data[user_id] = dados_padrao()
    elif isinstance(user_data[user_id], int):
        elo_antigo = user_data[user_id]
        user_data[user_id] = dados_padrao()
        user_data[user_id]["elo"] = elo_antigo
        user_data[user_id]["peak_elo"] = elo_antigo
    else:
        padrao = dados_padrao()
        for chave, valor in padrao.items():
            if chave not in user_data[user_id]:
                user_data[user_id][chave] = valor
    return user_data[user_id]


def obter_chesscoins(user_id: int) -> int:
    return max(0, int(obter_dados_usuario(user_id).get("chesscoins", 0)))


def alterar_chesscoins(user_id: int, quantia: int) -> bool:
    dados = obter_dados_usuario(user_id)
    saldo_anterior = obter_chesscoins(user_id)
    novo_saldo = saldo_anterior + int(quantia)
    if novo_saldo < 0:
        return False
    dados["chesscoins"] = novo_saldo
    if not salvar_dados():
        dados["chesscoins"] = saldo_anterior
        return False
    return True

ACHIEVEMENTS = {
    "primeira_partida": {"name": "Primeiros movimentos", "description": "Jogue sua primeira partida.", "reward": 100},
    "sequencia_5": {"name": "Em chamas", "description": "Vença 5 partidas seguidas.", "reward": 250},
    "patente_amador": {"name": "Subindo de nível", "description": "Alcance a patente Amador.", "reward": 150},
    "patente_intermediario": {"name": "Já entende do jogo", "description": "Alcance a patente Intermediário.", "reward": 300},
}

def verificar_conquistas(user_id):
    dados = obter_dados_usuario(user_id)
    partidas = len(dados.get("history", []))
    patente = patente_do_perfil(dados)
    condicoes = {
        "primeira_partida": partidas >= 1,
        "sequencia_5": int(dados.get("current_win_streak", 0)) >= 5,
        "patente_amador": patente in PATENTES_NOMES[PATENTES_NOMES.index("Amador"):],
        "patente_intermediario": patente in PATENTES_NOMES[PATENTES_NOMES.index("Intermediário"):],
    }
    desbloqueadas = dados.setdefault("achievements", [])
    novas = []
    for chave, concluiu in condicoes.items():
        if concluiu and chave not in desbloqueadas:
            desbloqueadas.append(chave)
            recompensa = ACHIEVEMENTS[chave]["reward"]
            dados["chesscoins"] = obter_chesscoins(user_id) + recompensa
            novas.append((ACHIEVEMENTS[chave]["name"], recompensa))
    return novas




@bot.tree.command(name="daily", description="Resgate 50 ChessCoins diariamente.")
async def daily(interaction):
    dados = obter_dados_usuario(interaction.user.id)
    hoje = datetime.now(timezone.utc).date().isoformat()
    if dados.get("daily_last_claim") == hoje:
        await interaction.response.send_message("Você já resgatou seu daily hoje. Volte depois da virada do dia UTC.", ephemeral=True)
        return
    anterior = int(dados.get("chesscoins", 0))
    data_anterior = dados.get("daily_last_claim")
    dados["chesscoins"] = anterior + 50
    dados["daily_last_claim"] = hoje
    if not salvar_dados():
        dados["chesscoins"] = anterior
        dados["daily_last_claim"] = data_anterior
        await interaction.response.send_message("Não consegui salvar seu daily. Tente novamente.", ephemeral=True)
        return
    await interaction.response.send_message(f"🪙 Você recebeu **50 ChessCoins**! Saldo: **{anterior + 50}**.", ephemeral=True)


@bot.tree.command(name="loja", description="Veja a loja de itens do DisChessLord.")
async def loja(interaction):
    embed = discord.Embed(
        title="🛒 Loja DisChessLord",
        description="A loja ainda está sendo preparada. Em breve você poderá trocar ChessCoins por itens e personalizações.\n\nSeus tickets administrativos poderão oferecer benefícios e descontos especiais.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Seu saldo", value=f"🪙 {obter_chesscoins(interaction.user.id)} ChessCoins", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="conquistas", description="Veja suas conquistas e recompensas.")
async def conquistas(interaction):
    dados = obter_dados_usuario(interaction.user.id)
    novas = verificar_conquistas(interaction.user.id)
    if novas and not salvar_dados():
        await interaction.response.send_message("Não consegui salvar suas conquistas. Tente novamente.", ephemeral=True)
        return
    desbloqueadas = set(dados.get("achievements", []))
    embed = discord.Embed(title="🏅 Suas conquistas", color=discord.Color.gold())
    for chave, info in ACHIEVEMENTS.items():
        estado = "✅ Desbloqueada" if chave in desbloqueadas else "🔒 Bloqueada"
        embed.add_field(name=f"{info['name']} — {estado}", value=f"{info['description']}\nRecompensa: **{info['reward']} ChessCoins**", inline=False)
    if novas:
        embed.description = "Novas conquistas resgatadas: " + ", ".join(f"{nome} (+{valor})" for nome, valor in novas)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="saldo", description="Veja seu saldo de ChessCoins.")
async def cmd_saldo(interaction: discord.Interaction):
    saldo = obter_chesscoins(interaction.user.id)
    embed = discord.Embed(
        title="🪙 Sua carteira de ChessCoins",
        description=f"Você possui **{saldo} ChessCoins**.",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


def mapa_numeros_partidas():
    """Converte IDs antigos aleatórios em números estáveis para exibição."""
    registros = {}
    maior_numero = 0
    for perfil in user_data.values():
        if not isinstance(perfil, dict):
            continue
        for partida in perfil.get("history", []):
            pid = str(partida.get("id", ""))
            if pid.isdigit():
                maior_numero = max(maior_numero, int(pid))
            elif pid:
                atual = registros.get(pid)
                data = str(partida.get("date", ""))
                if atual is None or data < atual:
                    registros[pid] = data
    ordenados = sorted(registros, key=lambda pid: (registros[pid], pid))
    return {pid: maior_numero + i + 1 for i, pid in enumerate(ordenados)}


def numero_exibicao_partida(partida_id):
    pid = str(partida_id)
    if pid.isdigit():
        return int(pid)
    return mapa_numeros_partidas().get(pid, 0)


def nome_jogador(user_id, guild=None):
    """Retorna o nick global e identifica visualmente a conta oficial do dev."""
    dados = obter_dados_usuario(user_id)
    nickname = dados.get("nickname")
    if nickname:
        if autorizado_dev(user_id):
            return f"☑️ {nickname}"
        return nickname
    membro = membro_em_guild(guild, user_id)
    if membro:
        nome = membro.display_name
    else:
        usuario = bot.get_user(int(user_id))
        nome = (usuario.global_name or usuario.name) if usuario else "Jogador sem nick"
    if autorizado_dev(user_id):
        return f"☑️ {nome}"
    return nome


def nickname_tem_identidade_reservada(nickname):
    """Impede que jogadores comuns imitem a equipe oficial."""
    chave = nickname.casefold()
    termos = (
        "dev", "developer", "desenvolvedor", "desenvolvedora",
        "staff", "admin", "administrador", "administradora",
        "moderador", "moderadora", "moderator"
    )
    return any(termo in chave for termo in termos)


def nickname_disponivel(nickname, user_id):
    chave = nickname.casefold()
    for outro_id, dados in user_data.items():
        if str(outro_id) == str(user_id) or not isinstance(dados, dict):
            continue
        existente = dados.get("nickname")
        if existente and existente.casefold() == chave:
            return False
    return True


# ============================================================
# 4. DADOS ADMINISTRATIVOS
# ============================================================

def dados_admin_padrao():
    return {
        "events": {},
        "tournaments": {},
        "pieces": {},
        "settings": {},
        "raffles": {}
    }


def carregar_admin():
    global admin_data

    if not os.path.exists(ADMIN_FILE):
        admin_data = dados_admin_padrao()
        logging.info("Arquivo administrativo ainda não existe.")
        return

    try:
        with open(
            ADMIN_FILE,
            "r",
            encoding="utf-8"
        ) as arquivo:
            dados = json.load(arquivo)

        if not isinstance(dados, dict):
            raise ValueError("Arquivo administrativo inválido.")

        padrao = dados_admin_padrao()
        padrao.update(dados)

        for chave in (
            "events",
            "tournaments",
            "pieces",
            "settings",
            "raffles"
        ):
            if not isinstance(padrao[chave], dict):
                raise ValueError(
                    f"Seção administrativa inválida: {chave}"
                )

        admin_data = padrao

        logging.info("Dados administrativos carregados.")

    except Exception:
        logging.exception(
            "Erro ao carregar dados administrativos."
        )

        raise RuntimeError(
            "O arquivo administrativo está corrompido."
        )


def salvar_admin():
    return salvar_json_atomico(
        ADMIN_FILE,
        admin_data
    )


# ============================================================
# 5. ELO E PATENTES
# ============================================================

def obter_patente(elo):
    for minimo, maximo, nome in PATENTES:
        if minimo <= elo <= maximo:
            return nome

    return "Iniciante"


def patente_do_perfil(dados):
    return (
        dados.get("patente_override")
        or obter_patente(dados["elo"])
    )


async def sincronizar_patentes_membros(guild, user_ids):
    """Cria os cargos de patente e mantém um único cargo correto por jogador."""
    if guild is None:
        return

    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        logging.warning("Sem permissão para gerenciar cargos no servidor %s.", guild.id)
        return

    cargos = {}
    for nome in PATENTES_NOMES:
        cargo = discord.utils.get(guild.roles, name=nome)
        if cargo is None:
            try:
                cargo = await guild.create_role(
                    name=nome,
                    reason="Cargos automáticos de patente do DisChessLord"
                )
            except (discord.Forbidden, discord.HTTPException):
                logging.exception("Não foi possível criar o cargo %s no servidor %s.", nome, guild.id)
                return
        cargos[nome] = cargo

    cargos_patente = set(cargos.values())
    cargo_legado_lenda = discord.utils.get(
        guild.roles,
        name="Lenda do Xadrez"
    )
    if cargo_legado_lenda is not None:
        cargos_patente.add(cargo_legado_lenda)

    for user_id in set(int(uid) for uid in user_ids):
        membro = guild.get_member(user_id)
        if membro is None:
            try:
                membro = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

        patente = patente_do_perfil(obter_dados_usuario(user_id))
        cargo_correto = cargos.get(patente)
        if cargo_correto is None:
            continue

        remover = [
            cargo for cargo in membro.roles
            if cargo in cargos_patente and cargo != cargo_correto
        ]
        adicionar = [cargo_correto] if cargo_correto not in membro.roles else []

        try:
            if remover:
                await membro.remove_roles(
                    *remover,
                    reason="Atualização automática de patente do DisChessLord"
                )
            if adicionar:
                await membro.add_roles(
                    cargo_correto,
                    reason="Atualização automática de patente do DisChessLord"
                )
        except (discord.Forbidden, discord.HTTPException):
            logging.exception("Não foi possível atualizar a patente de %s no servidor %s.", user_id, guild.id)


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
        K_FACTOR * (
            resultado_brancas - expectativa_brancas
        )
    )

    delta_pretas = round(
        K_FACTOR * (
            resultado_pretas - expectativa_pretas
        )
    )

    return delta_brancas, delta_pretas


def atualizar_elo(user_id, delta):
    dados = obter_dados_usuario(user_id)

    dados["elo"] = max(
        0,
        dados["elo"] + delta
    )

    dados["peak_elo"] = max(
        dados["peak_elo"],
        dados["elo"]
    )


# ============================================================
# 6. JOGADORES OCUPADOS E LOCKS
# ============================================================

def jogador_em_jogo(user_id):
    return any(
        user_id in (
            game["white"],
            game["black"]
        )
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
# 7. EVENTOS
# ============================================================

def evento_ativo(guild_id):
    agora = agora_utc()

    for evento in admin_data["events"].values():

        if str(evento.get("guild_id")) != str(guild_id):
            continue

        if evento.get("status") != "ativo":
            continue

        try:
            inicio = datetime.fromisoformat(
                evento["starts_at"]
            )

            fim = datetime.fromisoformat(
                evento["ends_at"]
            )

            if inicio <= agora < fim:
                return evento

        except (ValueError, KeyError, TypeError):
            continue

    return None


def atualizar_eventos_expirados():
    alterou = False
    agora = agora_utc()

    for evento in admin_data["events"].values():

        if evento.get("status") != "ativo":
            continue

        try:
            fim = datetime.fromisoformat(
                evento["ends_at"]
            )

            if agora >= fim:
                evento["status"] = "expirado"
                evento["expired_at"] = data_iso()
                alterou = True

        except (ValueError, KeyError, TypeError):
            continue

    if alterou:
        salvar_admin()


# ============================================================
# 8. TORNEIOS
# ============================================================

def torneio_por_id(tournament_id):
    return admin_data["tournaments"].get(
        str(tournament_id)
    )


def torneios_do_servidor(guild_id):
    return [
        torneio
        for torneio in admin_data["tournaments"].values()
        if str(torneio.get("guild_id")) == str(guild_id)
    ]


def criar_dados_torneio(
    guild_id,
    criador_id,
    nome,
    formato,
    duracao_horas,
    premio
):
    tournament_id = gerar_id()[:8]

    agora = agora_utc()

    torneio = {
        "id": tournament_id,
        "guild_id": str(guild_id),
        "creator_id": str(criador_id),
        "name": nome,
        "format": formato,
        "duration_hours": duracao_horas,
        "prize": premio,
        "status": "inscricoes",
        "created_at": agora.isoformat(),
        "ends_at": (
            agora + timedelta(hours=duracao_horas)
        ).isoformat(),
        "participants": [],
        "matches": [],
        "winner": None
    }

    return tournament_id, torneio


# ============================================================
# 9. PEÇAS PERSONALIZADAS — BIBLIOTECA
# ============================================================

BASE_PIECES = {
    "peao": chess.PAWN,
    "cavalo": chess.KNIGHT,
    "bispo": chess.BISHOP,
    "torre": chess.ROOK,
    "dama": chess.QUEEN,
    "rei": chess.KING
}


def criar_definicao_peca(
    nome,
    simbolo,
    metodo,
    offsets,
    base_piece=None,
    propriedades=None
):
    return {
        "id": gerar_id()[:8],
        "name": nome,
        "symbol": simbolo,
        "method": metodo,
        "base_piece": base_piece,
        "offsets": offsets,
        "properties": propriedades or {
            "can_capture": True,
            "can_give_check": True,
            "can_be_captured": True,
            "can_promote": False,
            "jumps_pieces": True
        },
        "created_at": data_iso(),
        "updated_at": data_iso()
    }


def obter_peca_personalizada(piece_id):
    return admin_data["pieces"].get(str(piece_id))


def listar_pecas_personalizadas():
    return list(
        admin_data["pieces"].values()
    )


def validar_offsets(texto):
    """
    Formato aceito:
    4,3;4,-3;-4,3;-4,-3;3,4;3,-4;-3,4;-3,-4
    """
    offsets = []

    for parte in texto.split(";"):
        parte = parte.strip()

        if not parte:
            continue

        valores = parte.split(",")

        if len(valores) != 2:
            raise ValueError(
                "Cada movimento deve ter dois números separados por vírgula."
            )

        dx = int(valores[0].strip())
        dy = int(valores[1].strip())

        if dx == 0 and dy == 0:
            raise ValueError(
                "Um movimento não pode ter deslocamento zero."
            )

        if abs(dx) > 7 or abs(dy) > 7:
            raise ValueError(
                "Os deslocamentos devem ficar entre -7 e 7."
            )

        par = [dx, dy]

        if par not in offsets:
            offsets.append(par)

    if not offsets:
        raise ValueError(
            "Informe pelo menos um movimento."
        )

    return offsets


def offsets_cabra():
    return [
        [4, 3], [4, -3],
        [-4, 3], [-4, -3],
        [3, 4], [3, -4],
        [-3, 4], [-3, -4]
    ]


# ============================================================
# 10. TABULEIRO E PARTIDAS CLÁSSICAS
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
        title="♟️ DisChessLord — Partida em andamento",
        description=desenhar_tabuleiro(board),
        color=discord.Color.blurple()
    )

    nome_brancas = nome_jogador(game["white"], bot.get_guild(game.get("guild_id")))
    nome_pretas = nome_jogador(game["black"], bot.get_guild(game.get("guild_id")))

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

    evento = game.get("event_snapshot")

    if evento:
        embed.add_field(
            name="🎉 Evento",
            value=evento.get("name", "Evento especial"),
            inline=False
        )

    if game.get("draw_offer") is not None:
        nome_ofertante = nome_jogador(
            game["draw_offer"],
            bot.get_guild(game.get("guild_id"))
        )

        embed.add_field(
            name="🤝 Oferta de empate pendente",
            value=(
                f"{nome_ofertante} ofereceu empate."
            ),
            inline=False
        )

    embed.set_footer(
        text=f"Partida {game['id']} • DisChessLord"
    )

    return embed


def criar_partida(
    challenger,
    opponent,
    channel_id,
    guild_id=None
):
    proximo = int(admin_data.setdefault("settings", {}).get("next_match_number", 1))
    game_id = str(proximo)
    admin_data["settings"]["next_match_number"] = proximo + 1
    if not salvar_admin():
        admin_data["settings"]["next_match_number"] = proximo
        raise RuntimeError("Não foi possível reservar o número da partida.")

    evento = (
        evento_ativo(guild_id)
        if guild_id is not None
        else None
    )

    snapshot = copy.deepcopy(evento) if evento else None

    return {
        "id": game_id,
        "white": challenger,
        "black": opponent,
        "channel_id": channel_id,
        "guild_id": guild_id,
        "board": chess.Board(),
        "moves": [],
        "started_at": data_iso(),
        "draw_offer": None,
        "selected_square": None,
        "finished": False,
        "message": None,
        "event_snapshot": snapshot,
        "custom_rules": None
    }


def jogador_da_vez(game):
    return (
        game["white"]
        if game["board"].turn == chess.WHITE
        else game["black"]
    )


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
# 11. HISTÓRICO, PGN E FINALIZAÇÃO
# ============================================================

def gerar_pgn(game, resultado, motivo):
    board = chess.Board()

    partida = chess.pgn.Game()

    partida.headers["Event"] = "DisChessLord"
    partida.headers["Site"] = "Discord"
    partida.headers["Date"] = agora_utc().strftime("%Y.%m.%d")
    partida.headers["White"] = nome_jogador(game["white"], bot.get_guild(game.get("guild_id")))
    partida.headers["Black"] = nome_jogador(game["black"], bot.get_guild(game.get("guild_id")))
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

    pgn = gerar_pgn(
        game,
        resultado,
        motivo
    )

    registro_base = {
        "id": game["id"],
        "date": data_iso(),
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
        "pgn": pgn,
        "event_snapshot": copy.deepcopy(
            game.get("event_snapshot")
        )
    }

    for user_id in (
        game["white"],
        game["black"]
    ):
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
        dados_brancas["current_win_streak"] = int(dados_brancas.get("current_win_streak", 0)) + 1
        dados_pretas["current_win_streak"] = 0
        dados_brancas["wins"] += 1
        dados_pretas["losses"] += 1
        dados_brancas["ranking_ativo"] = True
        dados_pretas["ranking_ativo"] = True
        dados_brancas["chesscoins"] = obter_chesscoins(white) + 10

    elif resultado == "0-1":
        score = 0.0
        dados_pretas["current_win_streak"] = int(dados_pretas.get("current_win_streak", 0)) + 1
        dados_brancas["current_win_streak"] = 0
        dados_brancas["losses"] += 1
        dados_pretas["wins"] += 1
        dados_brancas["ranking_ativo"] = True
        dados_pretas["ranking_ativo"] = True
        dados_pretas["chesscoins"] = obter_chesscoins(black) + 10

    else:
        score = 0.5
        dados_brancas["current_win_streak"] = 0
        dados_pretas["current_win_streak"] = 0
        dados_brancas["draws"] += 1
        dados_pretas["draws"] += 1
        dados_brancas["ranking_ativo"] = True
        dados_pretas["ranking_ativo"] = True

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

    for user_id in (white, black):
        verificar_conquistas(user_id)

    salvou = salvar_dados()

    if not salvou:
        logging.error(
            "Partida %s terminou, mas não foi salva.",
            game["id"]
        )

    guild_id = game.get("guild_id")
    if guild_id is not None:
        guild = bot.get_guild(int(guild_id))
        if guild is not None:
            asyncio.create_task(
                sincronizar_patentes_membros(guild, [white, black])
            )

    active_games.pop(
        game["channel_id"],
        None
    )

    return {
        "delta_white": delta_brancas,
        "delta_black": delta_pretas,
        "white_elo": obter_dados_usuario(white)["elo"],
        "black_elo": obter_dados_usuario(black)["elo"],
        "saved": salvou
    }


def criar_embed_final(
    game,
    titulo,
    descricao,
    resultado_elo
):
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
                value="Não foi possível salvar os dados.",
                inline=False
            )

    return embed


# ============================================================
# 12. INTERFACE DO TABULEIRO
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
                options=pecas[:25],
                row=0
            )

            select.callback = self.selecionar_peca

            self.add_item(select)

        if self.game["selected_square"] is not None:
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
                nome = chess.square_name(
                    move.to_square
                )

                if move.promotion:
                    nome += " (promoção)"

                botao = discord.ui.Button(
                    label=nome,
                    style=discord.ButtonStyle.primary,
                    row=1 + indice // 5
                )

                botao.callback = (
                    self.criar_callback_movimento(move)
                )

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

            btn_empate.callback = (
                self.callback_oferecer_empate
            )

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

            btn_aceitar.callback = (
                self.callback_aceitar_empate
            )

            btn_recusar.callback = (
                self.callback_recusar_empate
            )

            self.add_item(btn_aceitar)
            self.add_item(btn_recusar)

        btn_desistir = discord.ui.Button(
            label="Desistir",
            emoji="🏳️",
            style=discord.ButtonStyle.danger,
            row=4
        )

        btn_desistir.callback = (
            self.callback_desistir_clique
        )

        self.add_item(btn_desistir)

    async def validar_participante(self, interaction):
        if self.game["finished"]:
            await interaction.response.send_message(
                "Essa partida já terminou.",
                ephemeral=True
            )
            return False

        if active_games.get(
            self.game["channel_id"]
        ) is not self.game:
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

        if interaction.user.id != jogador_da_vez(
            self.game
        ):
            await interaction.response.send_message(
                "Não é sua vez de jogar.",
                ephemeral=True
            )
            return False

        return True

    async def selecionar_peca(self, interaction):
        async with obter_lock(
            self.game["channel_id"]
        ):

            if not await self.validar_jogador(interaction):
                return

            valores = interaction.data.get(
                "values",
                []
            )

            if not valores:
                await interaction.response.send_message(
                    "Nenhuma peça foi selecionada.",
                    ephemeral=True
                )
                return

            square = int(valores[0])

            piece = self.game["board"].piece_at(
                square
            )

            if (
                piece is None
                or piece.color != self.game["board"].turn
            ):
                await interaction.response.send_message(
                    "Essa peça não pode ser selecionada.",
                    ephemeral=True
                )
                return

            # Tocar novamente na peça selecionada cancela a seleção.
            # Selecionar outra peça simplesmente troca a origem do movimento.
            if self.game["selected_square"] == square:
                self.game["selected_square"] = None
            else:
                self.game["selected_square"] = square

            self.page = 0
            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    def criar_callback_movimento(self, move):

        async def callback(interaction):
            async with obter_lock(
                self.game["channel_id"]
            ):

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

                    promotion_view.message = (
                        interaction.message
                    )

                    return

                if (
                    self.game["draw_offer"]
                    == interaction.user.id
                ):
                    self.game["draw_offer"] = None

                board.push(move)

                self.game["moves"].append(
                    move.uci()
                )

                self.game["selected_square"] = None

                self.page = 0

                await atualizar_tabuleiro(
                    interaction,
                    self.game,
                    self
                )

        return callback

    async def pagina_anterior(self, interaction):
        async with obter_lock(
            self.game["channel_id"]
        ):

            if not await self.validar_jogador(interaction):
                return

            self.page = max(
                0,
                self.page - 1
            )

            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    async def proxima_pagina(self, interaction):
        async with obter_lock(
            self.game["channel_id"]
        ):

            if not await self.validar_jogador(interaction):
                return

            self.page += 1

            self.rebuild()

            await interaction.response.edit_message(
                embed=criar_embed_partida(self.game),
                view=self
            )

    async def cancelar_selecao(self, interaction):
        async with obter_lock(
            self.game["channel_id"]
        ):

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
        async with obter_lock(
            self.game["channel_id"]
        ):

            if not await self.validar_participante(interaction):
                return

            if self.game["draw_offer"] is not None:
                await interaction.response.send_message(
                    "Já existe uma oferta pendente.",
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
        async with obter_lock(
            self.game["channel_id"]
        ):

            if not await self.validar_participante(interaction):
                return

            ofertante = self.game["draw_offer"]

            if ofertante is None:
                await interaction.response.send_message(
                    "Não existe oferta de empate.",
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
                    "Não foi possível finalizar a partida.",
                    ephemeral=True
                )
                return

            self.game["draw_offer"] = None

            embed = criar_embed_final(
                self.game,
                "🤝 DisChessLord — Empate acordado",
                "Os jogadores concordaram com o empate!",
                resultado_elo
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=None
            )

    async def callback_recusar_empate(self, interaction):
        async with obter_lock(
            self.game["channel_id"]
        ):

            if not await self.validar_participante(interaction):
                return

            ofertante = self.game["draw_offer"]

            if ofertante is None:
                await interaction.response.send_message(
                    "Não existe oferta de empate.",
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
        async with obter_lock(
            self.game["channel_id"]
        ):

            if not await self.validar_participante(interaction):
                return

            confirm_view = ConfirmDesistenciaView(
                self.game,
                self
            )

            await interaction.response.send_message(
                "⚠️ Tem certeza de que deseja desistir?",
                view=confirm_view,
                ephemeral=True
            )

            confirm_view.message = (
                await interaction.original_response()
            )


# ============================================================
# 13. PROMOÇÃO
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
        async with obter_lock(
            self.game["channel_id"]
        ):

            if self.resolved:
                return

            self.resolved = True

            if self.game["finished"]:
                return

            if active_games.get(
                self.game["channel_id"]
            ) is not self.game:
                return

            self.parent_view.rebuild()

            if self.message:
                try:
                    await self.message.edit(
                        content=(
                            "⌛ Tempo de promoção esgotado. "
                            "Escolha novamente."
                        ),
                        embed=criar_embed_partida(self.game),
                        view=self.parent_view
                    )
                except discord.HTTPException:
                    logging.exception(
                        "Erro ao restaurar tabuleiro."
                    )

    async def promover(self, interaction, piece_type):
        async with obter_lock(
            self.game["channel_id"]
        ):

            if self.resolved:
                await interaction.response.send_message(
                    "Essa escolha expirou.",
                    ephemeral=True
                )
                return

            if self.game["finished"]:
                await interaction.response.send_message(
                    "A partida terminou.",
                    ephemeral=True
                )
                return

            if active_games.get(
                self.game["channel_id"]
            ) is not self.game:
                await interaction.response.send_message(
                    "Essa partida não está ativa.",
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

            if interaction.user.id != jogador_da_vez(
                self.game
            ):
                await interaction.response.send_message(
                    "Não é sua vez.",
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
                    "Promoção inválida.",
                    ephemeral=True
                )
                return

            if (
                self.game["draw_offer"]
                == interaction.user.id
            ):
                self.game["draw_offer"] = None

            board.push(move_final)

            self.game["moves"].append(
                move_final.uci()
            )

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
        await self.promover(
            interaction,
            chess.QUEEN
        )

    @discord.ui.button(
        label="Torre",
        emoji="♖",
        style=discord.ButtonStyle.primary
    )
    async def torre(self, interaction, button):
        await self.promover(
            interaction,
            chess.ROOK
        )

    @discord.ui.button(
        label="Bispo",
        emoji="♗",
        style=discord.ButtonStyle.primary
    )
    async def bispo(self, interaction, button):
        await self.promover(
            interaction,
            chess.BISHOP
        )

    @discord.ui.button(
        label="Cavalo",
        emoji="♘",
        style=discord.ButtonStyle.primary
    )
    async def cavalo(self, interaction, button):
        await self.promover(
            interaction,
            chess.KNIGHT
        )


# ============================================================
# 14. CONFIRMAÇÃO DE DESISTÊNCIA
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
                    content="⌛ Confirmação expirada.",
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
        interaction,
        button
    ):
        async with obter_lock(
            self.game["channel_id"]
        ):

            if self.game["finished"]:
                await interaction.response.send_message(
                    "A partida já terminou.",
                    ephemeral=True
                )
                return

            if active_games.get(
                self.game["channel_id"]
            ) is not self.game:
                await interaction.response.send_message(
                    "A partida não está ativa.",
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
                    "Não foi possível finalizar.",
                    ephemeral=True
                )
                return

            embed = criar_embed_final(
                self.game,
                "♟️ DisChessLord — Desistência",
                (
                    f"{interaction.user.mention} desistiu.\n"
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
                        "Erro ao atualizar partida."
                    )

    @discord.ui.button(
        label="Cancelar",
        style=discord.ButtonStyle.secondary
    )
    async def cancelar(
        self,
        interaction,
        button
    ):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="Ação cancelada.",
            view=self
        )


# ============================================================
# 15. ATUALIZAÇÃO DO TABULEIRO
# ============================================================

async def atualizar_tabuleiro(
    interaction,
    game,
    view
):
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
            vencedor = "🤝 Empate!"

        embed = criar_embed_final(
            game,
            "♟️ DisChessLord — Partida encerrada",
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
# 16. DESAFIOS
# ============================================================

class ChallengeView(discord.ui.View):

    def __init__(
        self,
        challenge_id,
        challenger,
        opponent,
        channel_id,
        guild_id
    ):
        super().__init__(timeout=TEMPO_DESAFIO)

        self.challenge_id = challenge_id
        self.challenger = challenger
        self.opponent = opponent
        self.channel_id = channel_id
        self.guild_id = guild_id

        self.message = None
        self.resolved = False

    def desafio_valido(self):
        challenge = pending_challenges.get(
            self.challenge_id
        )

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
                pending_challenges.pop(
                    self.challenge_id,
                    None
                )

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
    async def aceitar(
        self,
        interaction,
        button
    ):
        async with challenge_lock:

            if interaction.user.id != self.opponent:
                await interaction.response.send_message(
                    "Somente o jogador desafiado pode aceitar.",
                    ephemeral=True
                )
                return

            if self.resolved or not self.desafio_valido():
                await interaction.response.send_message(
                    "Desafio resolvido ou expirado.",
                    ephemeral=True
                )
                return

            if (
                jogador_em_jogo(self.challenger)
                or jogador_em_jogo(self.opponent)
            ):
                await interaction.response.send_message(
                    "Um dos jogadores já está em uma partida.",
                    ephemeral=True
                )
                return

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
                    "Um dos jogadores está em outro desafio.",
                    ephemeral=True
                )
                return

            if self.channel_id in active_games:
                await interaction.response.send_message(
                    "Já existe uma partida neste canal.",
                    ephemeral=True
                )
                return

            pending_challenges.pop(
                self.challenge_id,
                None
            )

            self.resolved = True

            # O evento é capturado no momento em que
            # a partida realmente começa.
            game = criar_partida(
                self.challenger,
                self.opponent,
                self.channel_id,
                self.guild_id
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
    async def recusar(
        self,
        interaction,
        button
    ):
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
                    "Desafio resolvido ou expirado.",
                    ephemeral=True
                )
                return

            pending_challenges.pop(
                self.challenge_id,
                None
            )

            self.resolved = True

            for item in self.children:
                item.disabled = True

            await interaction.response.edit_message(
                content="❌ Desafio recusado/cancelado.",
                embed=None,
                view=self
            )


# ============================================================
# 17. COMANDO DESAFIAR
# ============================================================

@bot.tree.command(
    name="desafiar",
    description="Desafie outro jogador para uma partida."
)
@app_commands.describe(
    jogador="Jogador que você deseja desafiar"
)
async def desafiar(
    interaction,
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
                "Você já está em uma partida ou desafio.",
                ephemeral=True
            )
            return

        if jogador_ocupado(jogador.id):
            await interaction.response.send_message(
                "Esse jogador já está em uma partida ou desafio.",
                ephemeral=True
            )
            return

        if interaction.channel_id in active_games:
            await interaction.response.send_message(
                "Já existe uma partida neste canal.",
                ephemeral=True
            )
            return

        challenge_id = gerar_id()

        view = ChallengeView(
            challenge_id,
            interaction.user.id,
            jogador.id,
            interaction.channel_id,
            interaction.guild_id
        )

        pending_challenges[challenge_id] = {
            "challenger": interaction.user.id,
            "opponent": jogador.id,
            "channel_id": interaction.channel_id,
            "guild_id": interaction.guild_id
        }

    try:
        await interaction.response.send_message(
            f"♟️ {jogador.mention}, você foi desafiado por "
            f"{interaction.user.mention}!\n"
            f"Você tem {TEMPO_DESAFIO} segundos.",
            view=view
        )

        view.message = (
            await interaction.original_response()
        )

    except Exception:
        async with challenge_lock:
            pending_challenges.pop(
                challenge_id,
                None
            )

        raise


# ============================================================
# 18. PERFIL
# ============================================================

@bot.tree.command(
    name="perfil",
    description="Veja seu perfil ou o perfil de outro jogador."
)
@app_commands.describe(
    jogador="Jogador cujo perfil deseja consultar"
)
async def perfil(
    interaction,
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

    patente = patente_do_perfil(dados)

    indice_patente_elo = next(
        (
            indice for indice, (minimo, maximo, nome) in enumerate(PATENTES)
            if minimo <= elo <= maximo
        ),
        0
    )

    if indice_patente_elo < len(PATENTES) - 1:
        elo_atual_patente, _, _ = PATENTES[indice_patente_elo]
        proximo_elo, _, proxima_patente = PATENTES[indice_patente_elo + 1]
    else:
        proximo_elo = None
        proxima_patente = None

    if proximo_elo is not None:
        progresso = (
            (elo - elo_atual_patente)
            / (proximo_elo - elo_atual_patente)
        ) * 100

        progresso = max(
            0,
            min(100, progresso)
        )

        blocos = round(progresso / 10)

        barra = (
            "▰" * blocos
            + "▱" * (10 - blocos)
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
        name="♟️ Nick DisChessLord",
        value=nome_jogador(jogador.id, interaction.guild) if dados.get("nickname") else "Ainda não definido — use /nick",
        inline=False
    )

    embed.add_field(
        name="🏆 ELO Atual",
        value=f"`{elo}`",
        inline=False
    )

    embed.add_field(
        name="📈 Maior ELO",
        value=f"`{peak_elo}`",
        inline=False
    )

    embed.add_field(
        name="👑 Patente",
        value=f"`{patente}`",
        inline=False
    )

    if dados.get("titulo"):
        embed.add_field(
            name="✨ Título",
            value=dados["titulo"],
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

    if interaction.guild is not None:
        await sincronizar_patentes_membros(interaction.guild, [jogador.id])

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# 19. NICKNAME GLOBAL
# ============================================================

@bot.tree.command(
    name="nick",
    description="Defina seu nick global no DisChessLord."
)
@app_commands.describe(
    nome="Nick único que será exibido nas partidas"
)
async def nick(interaction, nome: str):
    nome = " ".join(nome.strip().split())
    if len(nome) < 3 or len(nome) > 20:
        await interaction.response.send_message(
            "Seu nick precisa ter entre 3 e 20 caracteres.",
            ephemeral=True
        )
        return
    if any(ord(char) < 32 for char in nome) or "@" in nome or "`" in nome:
        await interaction.response.send_message(
            "Seu nick contém caracteres inválidos, menções ou crases.",
            ephemeral=True
        )
        return
    if (
        nickname_tem_identidade_reservada(nome)
        and not autorizado_dev(interaction.user.id)
    ):
        await interaction.response.send_message(
            "Esse nick usa um identificador reservado à equipe oficial do DisChessLord.",
            ephemeral=True
        )
        return
    if not nickname_disponivel(nome, interaction.user.id):
        await interaction.response.send_message(
            "Esse nick já está sendo usado. Escolha outro.",
            ephemeral=True
        )
        return
    dados = obter_dados_usuario(interaction.user.id)
    anterior = dados.get("nickname")
    dados["nickname"] = nome
    if not salvar_dados():
        dados["nickname"] = anterior
        await interaction.response.send_message(
            "Não foi possível salvar seu nick. Tente novamente.",
            ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"✅ Seu nick global no DisChessLord agora é **{nome}**.",
        ephemeral=True
    )


# ============================================================
# 20. RANKING
# ============================================================

@bot.tree.command(
    name="ranking",
    description="Veja o ranking de ELO."
)
async def ranking(interaction):
    jogadores = []

    for user_id, dados in user_data.items():
        if not isinstance(dados, dict) or not dados.get("ranking_ativo", False):
            continue
        elo = dados.get("elo", 100)
        jogadores.append((int(user_id), elo))

    jogadores.sort(
        key=lambda item: item[1],
        reverse=True
    )

    if not jogadores:
        await interaction.response.send_message(
            "Ainda não existem jogadores."
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

        nome = nome_jogador(user_id, interaction.guild)

        linhas.append(
            f"**{posicao}.** {nome} — **{elo} ELO**"
        )

    embed = discord.Embed(
        title="🏆 Ranking — DisChessLord",
        description="\n".join(linhas),
        color=discord.Color.gold()
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# 20. HISTÓRICO
# ============================================================

@bot.tree.command(
    name="historico",
    description="Veja as partidas recentes de um jogador."
)
@app_commands.describe(
    jogador="Jogador cujo histórico deseja consultar"
)
async def historico(
    interaction,
    jogador: discord.Member = None
):
    jogador = jogador or interaction.user

    dados = obter_dados_usuario(jogador.id)

    partidas = dados["history"]

    if not partidas:
        await interaction.response.send_message(
            "Esse jogador ainda não possui partidas.",
            ephemeral=True
        )
        return

    linhas = []

    for partida in partidas[:10]:

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

        nome = nome_jogador(adversario_id, interaction.guild)

        linhas.append(
            f"**{situacao}** contra {nome} "
            f"— {delta:+} ELO\n"
            f"Partida **#{numero_exibicao_partida(partida['id'])}**"
        )

    embed = discord.Embed(
        title=f"📜 Histórico de {jogador.display_name}",
        description="\n".join(linhas),
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# 21. ANALISAR
# ============================================================

@bot.tree.command(
    name="analisar",
    description="Veja o PGN de uma partida."
)
@app_commands.describe(
    numero_partida="Número sequencial da partida (opcional; vazio = mais recente)"
)
async def analisar(
    interaction,
    numero_partida: app_commands.Range[int, 1, 1000000] = None
):
    dados = obter_dados_usuario(
        interaction.user.id
    )

    partidas = dados["history"]

    if not partidas:
        await interaction.response.send_message(
            "Você ainda não possui partidas.",
            ephemeral=True
        )
        return

    partida = None

    if numero_partida is not None:
        for item in partidas:
            if numero_exibicao_partida(item.get("id")) == numero_partida:
                partida = item
                break
    else:
        partida = partidas[0]

    if not partida:
        await interaction.response.send_message(
            "Partida não encontrada.",
            ephemeral=True
        )
        return

    pgn = partida.get("pgn", "")

    if not pgn:
        await interaction.response.send_message(
            "Essa partida não possui PGN.",
            ephemeral=True
        )
        return

    if len(pgn) > 1800:
        pgn = pgn[:1800] + "\n... (reduzido)"

    embed = discord.Embed(
        title=f"♟️ PGN da partida #{numero_exibicao_partida(partida['id'])}",
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
# 22. RESETAR
# ============================================================

class ConfirmResetView(discord.ui.View):

    def __init__(
        self,
        ids_reset,
        escopo,
        nomes,
        owner_id,
        reset_titulo=False,
        reset_nick=False
    ):
        super().__init__(timeout=TEMPO_CONFIRMACAO)

        self.reset_titulo = reset_titulo
        self.reset_nick = reset_nick
        self.ids_reset = ids_reset
        self.escopo = escopo
        self.nomes = nomes
        self.owner_id = owner_id
        self.resolvido = False
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Essa confirmação pertence a outro usuário.",
                ephemeral=True
            )
            return False

        if interaction.user.id not in USUARIOS_AUTORIZADOS_RESET:
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return False

        return True

    async def on_timeout(self):
        if self.resolvido:
            return

        self.resolvido = True

        for item in self.children:
            item.disabled = True

        if self.message:
            try:
                await self.message.edit(
                    content="⌛ Confirmação expirada.",
                    view=self
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(
        label="Confirmar reset",
        emoji="⚠️",
        style=discord.ButtonStyle.danger
    )
    async def confirmar(
        self,
        interaction,
        button
    ):
        if self.resolvido:
            await interaction.response.send_message(
                "Confirmação já utilizada.",
                ephemeral=True
            )
            return

        async with reset_lock:
            async with challenge_lock:

                ocupados = [
                    user_id
                    for user_id in self.ids_reset
                    if jogador_ocupado(int(user_id))
                ]

                if ocupados:
                    await interaction.response.send_message(
                        "Reset cancelado: jogador ocupado.",
                        ephemeral=True
                    )
                    return

                backup = copiar_json(user_data)

                try:
                    if os.path.exists(ELO_FILE):
                        shutil.copy2(
                            ELO_FILE,
                            BACKUP_FILE
                        )
                    else:
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
                        "Falha no backup do reset."
                    )

                    await interaction.response.send_message(
                        "Não foi possível criar o backup.",
                        ephemeral=True
                    )
                    return

                for user_id in self.ids_reset:
                    anterior = user_data.get(user_id, {})
                    novo = dados_padrao()
                    if isinstance(anterior, dict):
                        novo["chesscoins"] = int(anterior.get("chesscoins", 0))
                        novo["command_tickets"] = int(anterior.get("command_tickets", 0))
                        novo["nickname"] = None if self.reset_nick else anterior.get("nickname")
                        novo["titulo"] = None if self.reset_titulo else anterior.get("titulo")
                        novo["patente_override"] = anterior.get("patente_override")
                    novo["ranking_ativo"] = False
                    user_data[user_id] = novo

                if not salvar_dados():
                    user_data.clear()
                    user_data.update(backup)

                    try:
                        shutil.copy2(
                            BACKUP_FILE,
                            ELO_FILE
                        )
                    except Exception:
                        logging.exception(
                            "Falha ao restaurar arquivo."
                        )

                    await interaction.response.send_message(
                        "Salvamento falhou. Dados restaurados em memória.",
                        ephemeral=True
                    )
                    return

                self.resolvido = True

        for item in self.children:
            item.disabled = True

        mensagem = (
            "✅ Reset concluído.\n"
            f"Perfis afetados: **{len(self.ids_reset)}**\n"
            "Todos voltaram para 100 ELO.\n"
            f"Nick resetado: **{'Sim' if self.reset_nick else 'Não'}** • "
            f"Título resetado: **{'Sim' if self.reset_titulo else 'Não'}**\n"
            "Backup anterior criado."
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
        interaction,
        button
    ):
        self.resolvido = True

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="Reset cancelado.",
            view=self
        )


@bot.tree.command(
    name="resetar",
    description="Reseta um ou mais perfis."
)
@app_commands.choices(escopo=[
    app_commands.Choice(name="Meu perfil", value="me"),
    app_commands.Choice(name="Todos globalmente", value="all"),
    app_commands.Choice(name="Jogador específico", value="selected")
])
@app_commands.choices(titulo=[
    app_commands.Choice(name="Não", value="nao"),
    app_commands.Choice(name="Sim", value="sim")
])
@app_commands.choices(nick=[
    app_commands.Choice(name="Não", value="nao"),
    app_commands.Choice(name="Sim", value="sim")
])
@app_commands.describe(
    escopo="Quem será resetado",
    titulo="Resetar também o título?",
    nick="Resetar também o nick?",
    jogador="Jogador específico (usado no escopo correspondente)"
)
async def resetar(
    interaction,
    escopo: app_commands.Choice[str],
    titulo: app_commands.Choice[str],
    nick: app_commands.Choice[str],
    jogador: discord.User = None
):
    if interaction.user.id not in USUARIOS_AUTORIZADOS_RESET:
        await interaction.response.send_message(
            "Você não tem autorização.",
            ephemeral=True
        )
        return

    if escopo.value == "me":
        ids_reset = [str(interaction.user.id)]
    elif escopo.value == "all":
        ids_reset = list(user_data.keys())
    elif escopo.value == "selected":
        if jogador is None:
            await interaction.response.send_message(
                "Selecione o jogador que será resetado.",
                ephemeral=True
            )
            return
        ids_reset = [str(jogador.id)]
    else:
        return

    if not ids_reset:
        await interaction.response.send_message(
            "Não existem perfis registrados.",
            ephemeral=True
        )
        return

    if any(jogador_ocupado(int(user_id)) for user_id in ids_reset):
        await interaction.response.send_message(
            "Um dos jogadores está ocupado.",
            ephemeral=True
        )
        return

    reset_titulo = titulo.value == "sim"
    reset_nick = nick.value == "sim"

    descricao = (
        f"Você está prestes a resetar **{len(ids_reset)} perfil(s)**.\\n"
        "ELO, Peak, estatísticas e histórico serão apagados.\\n"
        f"Nick: **{'será apagado' if reset_nick else 'será mantido'}** • "
        f"Título: **{'será apagado' if reset_titulo else 'será mantido'}**\\n"
        "Um backup será criado antes."
    )

    if jogador is not None and escopo.value == "selected":
        descricao += f"\\n\\nJogador: {jogador.mention}"

    view = ConfirmResetView(
        ids_reset,
        escopo.value,
        [],
        interaction.user.id,
        reset_titulo=reset_titulo,
        reset_nick=reset_nick
    )

    await interaction.response.send_message(
        descricao,
        view=view,
        ephemeral=True
    )
    view.message = await interaction.original_response()



# ============================================================
# DASHBOARDS DE MODERAÇÃO E SORTEIOS
# ============================================================

def autorizado_adm(interaction):
    if interaction.guild is None:
        return False
    p = interaction.user.guild_permissions
    return p.administrator or p.manage_guild or p.moderate_members


class TimeoutModal(discord.ui.Modal):
    def __init__(self, owner_id, guild_id, remover=False):
        super().__init__(title="Retirar timeout" if remover else "Aplicar timeout", timeout=180)
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.remover = remover
        self.user_id_input = discord.ui.TextInput(label="ID do usuário", max_length=25)
        self.add_item(self.user_id_input)
        if not remover:
            self.minutes_input = discord.ui.TextInput(label="Duração em minutos (1 a 40320)", max_length=5)
            self.reason_input = discord.ui.TextInput(label="Motivo", required=False, max_length=300)
            self.add_item(self.minutes_input)
            self.add_item(self.reason_input)

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_id or not autorizado_adm(interaction):
            await interaction.response.send_message("Sem autorização.", ephemeral=True)
            return
        try:
            member = interaction.guild.get_member(int(str(self.user_id_input.value)))
            if member is None or member.id == interaction.user.id or member.top_role >= interaction.user.top_role:
                raise ValueError
            if self.remover:
                await member.timeout(None, reason=f"Timeout removido por {interaction.user}")
                msg = f"Timeout removido de {member.mention}."
            else:
                minutes = int(str(self.minutes_input.value))
                if not 1 <= minutes <= 40320:
                    raise ValueError
                reason = str(self.reason_input.value).strip() or "Sem motivo informado"
                await member.timeout(datetime.now(timezone.utc) + timedelta(minutes=minutes), reason=reason)
                msg = f"Timeout aplicado em {member.mention} por {minutes} minuto(s)."
        except ValueError:
            await interaction.response.send_message("ID, hierarquia ou duração inválida. O alvo precisa estar abaixo de você.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("Não tenho permissão ou o cargo do alvo é superior ao meu.", ephemeral=True)
            return
        await interaction.response.send_message("✅ " + msg, ephemeral=True)


class SorteioCriarModal(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(title="Criar sorteio", timeout=180)
        self.owner_id = owner_id
        self.nome = discord.ui.TextInput(label="Nome do sorteio", max_length=80)
        self.premio = discord.ui.TextInput(label="Prêmio", max_length=150)
        self.custo = discord.ui.TextInput(label="Tickets por participação", default="1", max_length=3)
        self.add_item(self.nome)
        self.add_item(self.premio)
        self.add_item(self.custo)

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_id or not autorizado_dev(interaction.user.id):
            await interaction.response.send_message("Sem autorização.", ephemeral=True)
            return
        try:
            custo = int(str(self.custo.value))
            if custo < 1 or custo > 100:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("O custo deve ser de 1 a 100 tickets.", ephemeral=True)
            return
        if interaction.guild is None or not autorizado_adm(interaction):
            await interaction.response.send_message("Somente a equipe de administração pode criar sorteios.", ephemeral=True)
            return
        dados_admin = obter_dados_usuario(interaction.user.id)
        saldo_tickets = int(dados_admin.get("command_tickets", 0))
        if saldo_tickets < 1:
            await interaction.response.send_message("Você precisa de 1 ticket administrativo para criar um sorteio. Peça ao desenvolvedor para distribuir tickets.", ephemeral=True)
            return
        rid = gerar_id()
        admin_data.setdefault("raffles", {})[rid] = {
            "id": rid, "name": str(self.nome.value).strip(),
            "prize": str(self.premio.value).strip(), "ticket_cost": 0,
            "status": "aberto", "entries": {}, "created_at": data_iso(),
            "guild_id": interaction.guild_id, "channel_id": interaction.channel_id,
            "created_by": interaction.user.id
        }
        dados_admin["command_tickets"] = saldo_tickets - 1
        if not salvar_dados() or not salvar_admin():
            dados_admin["command_tickets"] = saldo_tickets
            admin_data["raffles"].pop(rid, None)
            salvar_dados()
            await interaction.response.send_message("Falha ao salvar o sorteio; ticket devolvido.", ephemeral=True)
            return
        await interaction.response.send_message(f"🎟️ Sorteio **{self.nome.value}** criado! Foi usado 1 ticket administrativo. Restam **{saldo_tickets - 1}**.", ephemeral=True)


class TicketsModal(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(title="Distribuir tickets", timeout=180)
        self.owner_id = owner_id
        self.user_id = discord.ui.TextInput(label="ID do usuário", max_length=25)
        self.quantidade = discord.ui.TextInput(label="Quantidade", max_length=6)
        self.add_item(self.user_id)
        self.add_item(self.quantidade)

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_id or not autorizado_dev(interaction.user.id):
            await interaction.response.send_message("Sem autorização.", ephemeral=True)
            return
        try:
            uid, qtd = int(str(self.user_id.value)), int(str(self.quantidade.value))
            if qtd < 1 or qtd > 100000:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("ID ou quantidade inválida.", ephemeral=True)
            return
        member = interaction.guild.get_member(uid) if interaction.guild else None
        if member is None or not autorizado_adm(interaction) or not (
            member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
            or member.guild_permissions.moderate_members
        ):
            await interaction.response.send_message("Tickets administrativos só podem ser entregues a membros da equipe de moderação/administração.", ephemeral=True)
            return
        dados = obter_dados_usuario(uid)
        anterior = int(dados.get("command_tickets", 0))
        dados["command_tickets"] = anterior + qtd
        if not salvar_dados():
            dados["command_tickets"] = anterior
            await interaction.response.send_message("Falha ao salvar; saldo restaurado.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ {qtd} ticket(s) administrativos entregues a {member.mention}. Saldo: **{anterior + qtd}**.", ephemeral=True)


class DevRafflesView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=180)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or not autorizado_dev(interaction.user.id):
            await interaction.response.send_message("Sem autorização.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Criar sorteio", emoji="🎟️", style=discord.ButtonStyle.success)
    async def criar(self, interaction, button):
        await interaction.response.send_modal(SorteioCriarModal(self.owner_id))

    @discord.ui.button(label="Distribuir tickets", emoji="🎫", style=discord.ButtonStyle.primary)
    async def tickets(self, interaction, button):
        await interaction.response.send_modal(TicketsModal(self.owner_id))

    @discord.ui.button(label="Encerrar sorteio", emoji="🔒", style=discord.ButtonStyle.danger)
    async def encerrar(self, interaction, button):
        abertos = [(k, v) for k, v in admin_data.setdefault("raffles", {}).items() if v.get("status") == "aberto"]
        if not abertos:
            await interaction.response.send_message("Não há sorteios abertos.", ephemeral=True)
            return
        rid, raffle = abertos[-1]
        entries = raffle.get("entries", {})
        weighted = [(uid, int(count)) for uid, count in entries.items() if int(count) > 0]
        total = sum(count for _, count in weighted)
        winner_id = None
        if total:
            pick = secrets.randbelow(total)
            for uid, count in weighted:
                if pick < count:
                    winner_id = uid
                    break
                pick -= count
        raffle["status"] = "encerrado"
        raffle["ended_at"] = data_iso()
        raffle["winner_id"] = winner_id
        if not salvar_admin():
            raffle["status"] = "aberto"
            raffle.pop("ended_at", None)
            raffle.pop("winner_id", None)
            await interaction.response.send_message("Falha ao salvar.", ephemeral=True)
            return
        if winner_id:
            await interaction.channel.send(
                f"🏆 **Sorteio encerrado: {raffle['name']}**\n"
                f"Prêmio: **{raffle['prize']}**\n"
                f"Vencedor: <@{winner_id}>!"
            )
            resultado = f"Sorteio encerrado. Vencedor: <@{winner_id}>."
        else:
            resultado = "Sorteio encerrado sem participantes."
        await interaction.response.send_message(f"🔒 {resultado} ID: `{rid}`", ephemeral=True)


class AdmDashboardView(discord.ui.View):
    def __init__(self, owner_id, guild_id):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or not autorizado_adm(interaction):
            await interaction.response.send_message("Sem autorização para este painel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Aplicar timeout", emoji="🔇", style=discord.ButtonStyle.danger)
    async def timeout(self, interaction, button):
        await interaction.response.send_modal(TimeoutModal(self.owner_id, self.guild_id))

    @discord.ui.button(label="Retirar timeout", emoji="🔊", style=discord.ButtonStyle.success)
    async def untimeout(self, interaction, button):
        await interaction.response.send_modal(TimeoutModal(self.owner_id, self.guild_id, True))

    @discord.ui.button(label="Atividade", emoji="📋", style=discord.ButtonStyle.primary)
    async def atividade(self, interaction, button):
        eventos = [x for x in admin_data.get("events", {}).values() if x.get("status") in ("ativo", "aberto")]
        torneios = [x for x in admin_data.get("tournaments", {}).values() if x.get("status") in ("inscricoes", "inscrições", "ativo")]
        sorteios = [x for x in admin_data.get("raffles", {}).values() if x.get("status") == "aberto"]
        def linhas(items):
            return "\n".join("• " + str(x.get("name", "Sem nome")) for x in items[:8]) or "Nenhum"
        embed = discord.Embed(title="📋 Atividade do servidor", color=discord.Color.blurple())
        embed.add_field(name="Eventos ativos", value=linhas(eventos), inline=False)
        embed.add_field(name="Torneios ativos", value=linhas(torneios), inline=False)
        embed.add_field(name="Sorteios abertos", value=linhas(sorteios), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="adm_dashboard", description="Abre o painel de moderação do servidor.")
async def adm_dashboard(interaction):
    # Reconhece a interação imediatamente para evitar expiração dos 3 segundos.
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.guild is None or not autorizado_adm(interaction):
            await interaction.edit_original_response(
                content="Você precisa de permissão de moderação ou administração em um servidor.",
                embed=None,
                view=None
            )
            return

        view = AdmDashboardView(interaction.user.id, interaction.guild_id)
        await interaction.edit_original_response(
            content=None,
            embed=discord.Embed(
                title="🛡️ DisChessLord — Painel de moderação",
                description="Moderação e consulta de atividades.",
                color=discord.Color.blurple()
            ),
            view=view
        )
    except Exception:
        logging.exception("Erro ao abrir /adm_dashboard")
        if interaction.is_expired():
            return
        await interaction.edit_original_response(
            content="Ocorreu um erro ao abrir o painel. O erro foi registrado nos logs.",
            embed=None,
            view=None
        )


@bot.tree.command(name="sorteios", description="Veja os sorteios abertos.")
async def sorteios(interaction):
    abertos = [x for x in admin_data.setdefault("raffles", {}).values()
               if x.get("status") == "aberto" and (interaction.guild_id is None or x.get("guild_id") == interaction.guild_id)]
    if not abertos:
        await interaction.response.send_message("Não há sorteios abertos.", ephemeral=True)
        return
    embed = discord.Embed(title="🎟️ Sorteios abertos", color=discord.Color.gold())
    for x in abertos[:10]:
        embed.add_field(name=x["name"], value=f"Prêmio: **{x['prize']}**\nCusto: **{x['ticket_cost']} ticket(s)** por entrada\nParticipantes: **{len(x.get('entries', {}))}**", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="participar_sorteio", description="Entre em um sorteio aberto.")
@app_commands.describe(nome_sorteio="Nome exato do sorteio")
async def participar_sorteio(interaction, nome_sorteio: str):
    nome = " ".join(nome_sorteio.strip().split()).casefold()
    encontrados = [x for x in admin_data.setdefault("raffles", {}).values()
                   if x.get("status") == "aberto" and x.get("guild_id") == interaction.guild_id
                   and " ".join(str(x.get("name", "")).split()).casefold() == nome]
    if len(encontrados) != 1:
        await interaction.response.send_message("Sorteio não encontrado ou nome duplicado. Confira /sorteios.", ephemeral=True)
        return
    raffle = encontrados[0]
    entradas = raffle.setdefault("entries", {})
    uid = str(interaction.user.id)
    entradas[uid] = int(entradas.get(uid, 0)) + 1
    if not salvar_admin():
        entradas[uid] -= 1
        await interaction.response.send_message("Não foi possível registrar sua participação.", ephemeral=True)
        return
    await interaction.response.send_message(f"🎟️ Você entrou em **{raffle['name']}**! Boa sorte.", ephemeral=True)



# ============================================================
# 23. DASHBOARD — MENU PRINCIPAL
# ============================================================

class DashboardView(discord.ui.View):

    def __init__(self, owner_id, guild_id):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Esse dashboard pertence a outro usuário.",
                ephemeral=True
            )
            return False

        if not autorizado_dev(interaction.user.id):
            await interaction.response.send_message(
                "Você não tem autorização.",
                ephemeral=True
            )
            return False

        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(
        label="Jogadores",
        emoji="👥",
        style=discord.ButtonStyle.primary,
        row=0
    )
    async def jogadores(self, interaction, button):
        view = JogadoresAdminView(
            self.owner_id,
            self.guild_id
        )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="👥 Administração de jogadores",
                description=(
                    "Escolha uma ação para administrar perfis."
                ),
                color=discord.Color.blurple()
            ),
            view=view
        )

        view.message = interaction.message

    @discord.ui.button(
        label="Eventos",
        emoji="🎉",
        style=discord.ButtonStyle.primary,
        row=0
    )
    async def eventos(self, interaction, button):
        view = EventosAdminView(
            self.owner_id,
            self.guild_id
        )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🎉 Administração de eventos",
                description=(
                    "Crie, consulte, encerre ou cancele eventos."
                ),
                color=discord.Color.blurple()
            ),
            view=view
        )

        view.message = interaction.message

    @discord.ui.button(
        label="Torneios",
        emoji="🏆",
        style=discord.ButtonStyle.primary,
        row=0
    )
    async def torneios(self, interaction, button):
        view = TorneiosAdminView(
            self.owner_id,
            self.guild_id
        )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🏆 Administração de torneios",
                description=(
                    "Gerencie inscrições e torneios internos."
                ),
                color=discord.Color.gold()
            ),
            view=view
        )

        view.message = interaction.message

    @discord.ui.button(
        label="Peças",
        emoji="♟️",
        style=discord.ButtonStyle.primary,
        row=1
    )
    async def pecas(self, interaction, button):
        view = PecasAdminView(
            self.owner_id,
            self.guild_id
        )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="♟️ Biblioteca de peças",
                description=(
                    "Crie, edite, duplique e remova peças personalizadas."
                ),
                color=discord.Color.blurple()
            ),
            view=view
        )

        view.message = interaction.message

    @discord.ui.button(
        label="Sorteios",
        emoji="🎟️",
        style=discord.ButtonStyle.success,
        row=2
    )
    async def sorteios(self, interaction, button):
        view = DevRafflesView(self.owner_id)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🎟️ Administração de sorteios",
                description="Crie sorteios, distribua tickets e encerre sorteios abertos.",
                color=discord.Color.gold()
            ),
            view=view
        )

    @discord.ui.button(
        label="Estatísticas",
        emoji="📊",
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def estatisticas(self, interaction, button):
        total_jogadores = len(user_data)
        total_eventos = len(admin_data["events"])
        total_torneios = len(admin_data["tournaments"])
        total_pecas = len(admin_data["pieces"])

        embed = discord.Embed(
            title="📊 Estatísticas do DisChessLord",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="Jogadores",
            value=str(total_jogadores),
            inline=True
        )

        embed.add_field(
            name="Partidas ativas",
            value=str(len(active_games)),
            inline=True
        )

        embed.add_field(
            name="Desafios pendentes",
            value=str(len(pending_challenges)),
            inline=True
        )

        embed.add_field(
            name="Eventos",
            value=str(total_eventos),
            inline=True
        )

        embed.add_field(
            name="Torneios",
            value=str(total_torneios),
            inline=True
        )

        embed.add_field(
            name="Peças personalizadas",
            value=str(total_pecas),
            inline=True
        )

        await interaction.response.edit_message(
            embed=embed,
            view=DashboardView(
                self.owner_id,
                self.guild_id
            )
        )

    @discord.ui.button(
        label="Voltar",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def voltar(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🛠️ DisChessLord — Dashboard",
                description="Painel administrativo principal.",
                color=discord.Color.blurple()
            ),
            view=self
        )


@bot.tree.command(
    name="dev_dashboard",
    description="Abre o painel administrativo do DisChessLord."
)
async def dev_dashboard(interaction):
    if not autorizado_dev(interaction.user.id):
        await interaction.response.send_message(
            "Você não tem autorização para abrir o dashboard.",
            ephemeral=True
        )
        return

    view = DashboardView(
        interaction.user.id,
        interaction.guild_id
    )

    view = DashboardView(
        interaction.user.id,
        interaction.guild_id
    )

    await interaction.response.send_message(
        embed=discord.Embed(
            title="🛠️ DisChessLord — Dashboard",
            description="Painel administrativo principal.",
            color=discord.Color.blurple()
        ),
        view=view,
        ephemeral=True
    )

    view.message = (
        await interaction.original_response()
    )


# ============================================================
# 24. ADMINISTRAÇÃO DE JOGADORES
# ============================================================

class JogadorEloModal(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(
            title="Alterar ELO de jogador",
            timeout=180
        )

        self.owner_id = owner_id

        self.user_id_input = discord.ui.TextInput(
            label="ID do jogador",
            placeholder="Ex.: 123456789012345678",
            max_length=25
        )

        self.elo_input = discord.ui.TextInput(
            label="Novo ELO",
            placeholder="Ex.: 1500",
            max_length=8
        )

        self.add_item(self.user_id_input)
        self.add_item(self.elo_input)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        try:
            user_id = int(str(self.user_id_input.value))
            novo_elo = int(str(self.elo_input.value))

            if novo_elo < 0 or novo_elo > 1000000:
                raise ValueError

        except ValueError:
            await interaction.response.send_message(
                "ID ou ELO inválido.",
                ephemeral=True
            )
            return

        if jogador_ocupado(user_id):
            await interaction.response.send_message(
                "Não é possível alterar o ELO de um jogador ocupado.",
                ephemeral=True
            )
            return

        async with admin_lock:
            dados = obter_dados_usuario(user_id)
            anterior = copiar_json(dados)

            dados["elo"] = novo_elo
            dados["peak_elo"] = max(
                dados["peak_elo"],
                novo_elo
            )

            if not salvar_dados():
                user_data[str(user_id)] = anterior

                await interaction.response.send_message(
                    "Falha ao salvar. Alteração revertida.",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            f"✅ ELO de `{user_id}` alterado para **{novo_elo}**.",
            ephemeral=True
        )


class JogadorPatenteModal(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(
            title="Alterar patente manual",
            timeout=180
        )

        self.owner_id = owner_id

        self.user_id_input = discord.ui.TextInput(
            label="ID do jogador",
            max_length=25
        )

        self.patente_input = discord.ui.TextInput(
            label="Patente",
            placeholder=", ".join(PATENTES_NOMES),
            max_length=40
        )

        self.add_item(self.user_id_input)
        self.add_item(self.patente_input)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        try:
            user_id = int(str(self.user_id_input.value))
        except ValueError:
            await interaction.response.send_message(
                "ID inválido.",
                ephemeral=True
            )
            return

        patente = str(self.patente_input.value).strip()

        if patente.lower() in (
            "normal",
            "automática",
            "automatica",
            "nenhuma"
        ):
            patente = None

        elif patente not in PATENTES_NOMES:
            await interaction.response.send_message(
                "Patente não reconhecida.",
                ephemeral=True
            )
            return

        dados = obter_dados_usuario(user_id)
        anterior = copiar_json(dados)

        dados["patente_override"] = patente

        if not salvar_dados():
            user_data[str(user_id)] = anterior

            await interaction.response.send_message(
                "Falha ao salvar. Alteração revertida.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            (
                "Patente manual removida."
                if patente is None
                else f"Patente alterada para **{patente}**."
            ),
            ephemeral=True
        )


class JogadorTituloModal(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(
            title="Alterar título especial",
            timeout=180
        )

        self.owner_id = owner_id

        self.user_id_input = discord.ui.TextInput(
            label="ID do jogador",
            max_length=25
        )

        self.titulo_input = discord.ui.TextInput(
            label="Título (vazio para remover)",
            required=False,
            max_length=60
        )

        self.add_item(self.user_id_input)
        self.add_item(self.titulo_input)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        try:
            user_id = int(str(self.user_id_input.value))
        except ValueError:
            await interaction.response.send_message(
                "ID inválido.",
                ephemeral=True
            )
            return

        dados = obter_dados_usuario(user_id)
        anterior = copiar_json(dados)

        titulo = str(
            self.titulo_input.value
        ).strip()

        dados["titulo"] = titulo or None

        if not salvar_dados():
            user_data[str(user_id)] = anterior

            await interaction.response.send_message(
                "Falha ao salvar.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Título atualizado.",
            ephemeral=True
        )


class CargoJogadorModal(discord.ui.Modal):
    def __init__(self, owner_id, guild_id):
        super().__init__(
            title="Adicionar cargo ao jogador",
            timeout=180
        )

        self.owner_id = owner_id
        self.guild_id = guild_id

        self.user_id_input = discord.ui.TextInput(
            label="ID do membro",
            max_length=25
        )

        self.role_id_input = discord.ui.TextInput(
            label="ID do cargo",
            max_length=25
        )

        self.add_item(self.user_id_input)
        self.add_item(self.role_id_input)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "Use o dashboard dentro de um servidor.",
                ephemeral=True
            )
            return

        if interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                "Servidor incorreto.",
                ephemeral=True
            )
            return

        try:
            user_id = int(str(self.user_id_input.value))
            role_id = int(str(self.role_id_input.value))
        except ValueError:
            await interaction.response.send_message(
                "IDs inválidos.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        membro = guild.get_member(user_id)
        cargo = guild.get_role(role_id)

        if membro is None or cargo is None:
            await interaction.response.send_message(
                "Membro ou cargo não encontrado.",
                ephemeral=True
            )
            return

        bot_member = guild.me

        if bot_member is None:
            await interaction.response.send_message(
                "Não consegui identificar o bot no servidor.",
                ephemeral=True
            )
            return

        if not bot_member.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "O bot precisa da permissão Gerenciar Cargos.",
                ephemeral=True
            )
            return

        if cargo >= bot_member.top_role:
            await interaction.response.send_message(
                "O cargo está acima ou no mesmo nível do cargo do bot.",
                ephemeral=True
            )
            return

        try:
            await membro.add_roles(
                cargo,
                reason="DisChessLord Dashboard"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Discord recusou a alteração de cargo.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Cargo **{cargo.name}** adicionado a {membro.mention}.",
            ephemeral=True
        )


class JogadoresAdminView(discord.ui.View):

    def __init__(self, owner_id, guild_id):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="Alterar ELO",
        emoji="📈",
        style=discord.ButtonStyle.primary
    )
    async def elo(self, interaction, button):
        await interaction.response.send_modal(
            JogadorEloModal(self.owner_id)
        )

    @discord.ui.button(
        label="Alterar patente",
        emoji="👑",
        style=discord.ButtonStyle.primary
    )
    async def patente(self, interaction, button):
        await interaction.response.send_modal(
            JogadorPatenteModal(self.owner_id)
        )

    @discord.ui.button(
        label="Título especial",
        emoji="✨",
        style=discord.ButtonStyle.primary
    )
    async def titulo(self, interaction, button):
        await interaction.response.send_modal(
            JogadorTituloModal(self.owner_id)
        )

    @discord.ui.button(
        label="Adicionar cargo",
        emoji="🎖️",
        style=discord.ButtonStyle.secondary
    )
    async def cargo(self, interaction, button):
        await interaction.response.send_modal(
            CargoJogadorModal(
                self.owner_id,
                self.guild_id
            )
        )

    @discord.ui.button(
        label="Voltar",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def voltar(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🛠️ DisChessLord — Dashboard",
                description="Painel administrativo principal.",
                color=discord.Color.blurple()
            ),
            view=DashboardView(
                self.owner_id,
                self.guild_id
            )
        )


# ============================================================
# 25. ADMINISTRAÇÃO DE EVENTOS
# ============================================================

class CriarEventoModal(discord.ui.Modal):
    def __init__(self, owner_id, guild_id):
        super().__init__(
            title="Criar evento",
            timeout=180
        )

        self.owner_id = owner_id
        self.guild_id = guild_id

        self.nome = discord.ui.TextInput(
            label="Nome do evento",
            max_length=80
        )

        self.descricao = discord.ui.TextInput(
            label="Descrição",
            required=False,
            max_length=300,
            style=discord.TextStyle.paragraph
        )

        self.duracao = discord.ui.TextInput(
            label="Duração em horas",
            placeholder="Ex.: 24",
            max_length=5
        )

        self.modificador = discord.ui.TextInput(
            label="Modificador/regras especiais",
            placeholder="Ex.: bônus de participação",
            required=False,
            max_length=200
        )

        self.add_item(self.nome)
        self.add_item(self.descricao)
        self.add_item(self.duracao)
        self.add_item(self.modificador)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        if self.guild_id is None:
            await interaction.response.send_message(
                "Eventos precisam ser criados dentro de um servidor.",
                ephemeral=True
            )
            return

        try:
            horas = int(str(self.duracao.value))

            if horas < 1 or horas > 720:
                raise ValueError

        except ValueError:
            await interaction.response.send_message(
                "Duração inválida. Use de 1 a 720 horas.",
                ephemeral=True
            )
            return

        evento_id = gerar_id()[:8]
        inicio = agora_utc()
        fim = inicio + timedelta(hours=horas)

        evento = {
            "id": evento_id,
            "guild_id": str(self.guild_id),
            "creator_id": str(self.owner_id),
            "name": str(self.nome.value).strip(),
            "description": str(self.descricao.value).strip(),
            "modifier": str(self.modificador.value).strip(),
            "status": "ativo",
            "starts_at": inicio.isoformat(),
            "ends_at": fim.isoformat(),
            "created_at": inicio.isoformat()
        }

        async with admin_lock:
            admin_data["events"][evento_id] = evento

            if not salvar_admin():
                admin_data["events"].pop(
                    evento_id,
                    None
                )

                await interaction.response.send_message(
                    "Falha ao salvar o evento.",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            embed=embed_sucesso(
                f"Evento **{evento['name']}** criado.\n"
                f"ID: `{evento_id}`\n"
                f"Duração: {horas} horas."
            ),
            ephemeral=True
        )


class EventoIdModal(discord.ui.Modal):
    def __init__(self, owner_id, guild_id, acao):
        super().__init__(
            title="Gerenciar evento",
            timeout=180
        )

        self.owner_id = owner_id
        self.guild_id = guild_id
        self.acao = acao

        self.evento_id = discord.ui.TextInput(
            label="ID do evento",
            max_length=12
        )

        self.add_item(self.evento_id)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        evento_id = str(
            self.evento_id.value
        ).strip().upper()

        evento = admin_data["events"].get(evento_id)

        if evento is None:
            await interaction.response.send_message(
                "Evento não encontrado.",
                ephemeral=True
            )
            return

        if str(evento.get("guild_id")) != str(self.guild_id):
            await interaction.response.send_message(
                "Esse evento pertence a outro servidor.",
                ephemeral=True
            )
            return

        if self.acao == "consultar":
            embed = discord.Embed(
                title=f"🎉 {evento['name']}",
                description=evento.get("description") or "Sem descrição.",
                color=discord.Color.blurple()
            )

            embed.add_field(
                name="ID",
                value=evento_id
            )

            embed.add_field(
                name="Status",
                value=evento.get("status", "desconhecido")
            )

            embed.add_field(
                name="Modificador",
                value=evento.get("modifier") or "Nenhum"
            )

            embed.add_field(
                name="Término",
                value=evento.get("ends_at", "Desconhecido"),
                inline=False
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )
            return

        evento_anterior = copiar_json(evento)

        if self.acao == "encerrar":
            evento["status"] = "encerrado"
            evento["ended_at"] = data_iso()

        elif self.acao == "cancelar":
            evento["status"] = "cancelado"
            evento["cancelled_at"] = data_iso()

        if not salvar_admin():
            admin_data["events"][evento_id] = evento_anterior

            await interaction.response.send_message(
                "Falha ao salvar. Alteração revertida.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Evento `{evento_id}` atualizado.",
            ephemeral=True
        )


class EventosAdminView(discord.ui.View):

    def __init__(self, owner_id, guild_id):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="Criar evento",
        emoji="➕",
        style=discord.ButtonStyle.success
    )
    async def criar(self, interaction, button):
        await interaction.response.send_modal(
            CriarEventoModal(
                self.owner_id,
                self.guild_id
            )
        )

    @discord.ui.button(
        label="Consultar evento",
        emoji="🔎",
        style=discord.ButtonStyle.primary
    )
    async def consultar(self, interaction, button):
        await interaction.response.send_modal(
            EventoIdModal(
                self.owner_id,
                self.guild_id,
                "consultar"
            )
        )

    @discord.ui.button(
        label="Encerrar evento",
        emoji="⏹️",
        style=discord.ButtonStyle.secondary
    )
    async def encerrar(self, interaction, button):
        await interaction.response.send_modal(
            EventoIdModal(
                self.owner_id,
                self.guild_id,
                "encerrar"
            )
        )

    @discord.ui.button(
        label="Cancelar evento",
        emoji="🗑️",
        style=discord.ButtonStyle.danger
    )
    async def cancelar(self, interaction, button):
        await interaction.response.send_modal(
            EventoIdModal(
                self.owner_id,
                self.guild_id,
                "cancelar"
            )
        )

    @discord.ui.button(
        label="Voltar",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def voltar(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🛠️ DisChessLord — Dashboard",
                description="Painel administrativo principal.",
                color=discord.Color.blurple()
            ),
            view=DashboardView(
                self.owner_id,
                self.guild_id
            )
        )


# ============================================================
# 26. ADMINISTRAÇÃO DE TORNEIOS
# ============================================================

class CriarTorneioModal(discord.ui.Modal):
    def __init__(self, owner_id, guild_id):
        super().__init__(
            title="Criar torneio",
            timeout=180
        )

        self.owner_id = owner_id
        self.guild_id = guild_id

        self.nome = discord.ui.TextInput(
            label="Nome do torneio",
            max_length=80
        )

        self.formato = discord.ui.TextInput(
            label="Formato",
            placeholder="suico, todos_contra_todos ou eliminacao",
            max_length=30
        )

        self.duracao = discord.ui.TextInput(
            label="Duração em horas",
            placeholder="Ex.: 48",
            max_length=5
        )

        self.premio = discord.ui.TextInput(
            label="Premiação",
            required=False,
            max_length=150
        )

        self.add_item(self.nome)
        self.add_item(self.formato)
        self.add_item(self.duracao)
        self.add_item(self.premio)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        if self.guild_id is None:
            await interaction.response.send_message(
                "Crie torneios dentro de um servidor.",
                ephemeral=True
            )
            return

        formato = str(
            self.formato.value
        ).strip().lower()

        formatos = {
            "suico",
            "todos_contra_todos",
            "eliminacao"
        }

        if formato not in formatos:
            await interaction.response.send_message(
                "Formato inválido. Use suico, todos_contra_todos ou eliminacao.",
                ephemeral=True
            )
            return

        try:
            horas = int(str(self.duracao.value))

            if horas < 1 or horas > 720:
                raise ValueError

        except ValueError:
            await interaction.response.send_message(
                "Duração inválida.",
                ephemeral=True
            )
            return

        tournament_id, torneio = criar_dados_torneio(
            self.guild_id,
            self.owner_id,
            str(self.nome.value).strip(),
            formato,
            horas,
            str(self.premio.value).strip()
        )

        async with admin_lock:
            admin_data["tournaments"][tournament_id] = torneio

            if not salvar_admin():
                admin_data["tournaments"].pop(
                    tournament_id,
                    None
                )

                await interaction.response.send_message(
                    "Falha ao salvar torneio.",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            embed=embed_sucesso(
                f"Torneio **{torneio['name']}** criado.\n"

                f"Formato: `{formato}`\n"
                "As inscrições estão abertas."
            ),
            ephemeral=True
        )


class TorneioIdModal(discord.ui.Modal):
    def __init__(self, owner_id, guild_id, acao):
        super().__init__(
            title="Gerenciar torneio",
            timeout=180
        )

        self.owner_id = owner_id
        self.guild_id = guild_id
        self.acao = acao

        self.tournament_name = discord.ui.TextInput(
            label="Nome exato do torneio",
            max_length=80
        )

        self.add_item(self.tournament_name)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        tournament_name = " ".join(str(self.tournament_name.value).split()).casefold()
        encontrados = [t for t in torneios_do_servidor(self.guild_id)
                       if " ".join(str(t.get("name", "")).split()).casefold() == tournament_name]
        if len(encontrados) != 1:
            await interaction.response.send_message("Torneio não encontrado ou nome duplicado. Use o nome exato exibido em /torneios.", ephemeral=True)
            return
        torneio = encontrados[0]
        tournament_id = torneio["id"]

        if str(torneio["guild_id"]) != str(self.guild_id):
            await interaction.response.send_message(
                "Esse torneio pertence a outro servidor.",
                ephemeral=True
            )
            return

        if self.acao == "consultar":
            embed = discord.Embed(
                title=f"🏆 {torneio['name']}",
                color=discord.Color.gold()
            )

            embed.add_field(
                name="Torneio",
                value=torneio["name"]
            )

            embed.add_field(
                name="Formato",
                value=torneio["format"]
            )

            embed.add_field(
                name="Status",
                value=torneio["status"]
            )

            embed.add_field(
                name="Participantes",
                value=str(len(torneio["participants"]))
            )

            embed.add_field(
                name="Premiação",
                value=torneio.get("prize") or "Não definida",
                inline=False
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )
            return

        anterior = copiar_json(torneio)

        if self.acao == "encerrar":
            torneio["status"] = "encerrado"
            torneio["ended_at"] = data_iso()

        elif self.acao == "cancelar":
            torneio["status"] = "cancelado"
            torneio["cancelled_at"] = data_iso()

        if not salvar_admin():
            admin_data["tournaments"][tournament_id] = anterior

            await interaction.response.send_message(
                "Falha ao salvar.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Torneio **{torneio['name']}** atualizado.",
            ephemeral=True
        )


class TorneiosAdminView(discord.ui.View):

    def __init__(self, owner_id, guild_id):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="Criar torneio",
        emoji="➕",
        style=discord.ButtonStyle.success
    )
    async def criar(self, interaction, button):
        await interaction.response.send_modal(
            CriarTorneioModal(
                self.owner_id,
                self.guild_id
            )
        )

    @discord.ui.button(
        label="Consultar torneio",
        emoji="🔎",
        style=discord.ButtonStyle.primary
    )
    async def consultar(self, interaction, button):
        await interaction.response.send_modal(
            TorneioIdModal(
                self.owner_id,
                self.guild_id,
                "consultar"
            )
        )

    @discord.ui.button(
        label="Encerrar torneio",
        emoji="⏹️",
        style=discord.ButtonStyle.secondary
    )
    async def encerrar(self, interaction, button):
        await interaction.response.send_modal(
            TorneioIdModal(
                self.owner_id,
                self.guild_id,
                "encerrar"
            )
        )

    @discord.ui.button(
        label="Cancelar torneio",
        emoji="🗑️",
        style=discord.ButtonStyle.danger
    )
    async def cancelar(self, interaction, button):
        await interaction.response.send_modal(
            TorneioIdModal(
                self.owner_id,
                self.guild_id,
                "cancelar"
            )
        )

    @discord.ui.button(
        label="Voltar",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def voltar(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🛠️ DisChessLord — Dashboard",
                description="Painel administrativo principal.",
                color=discord.Color.blurple()
            ),
            view=DashboardView(
                self.owner_id,
                self.guild_id
            )
        )


# ============================================================
# 27. BIBLIOTECA DE PEÇAS
# ============================================================

class CriarPecaModal(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(
            title="Criar peça personalizada",
            timeout=180
        )

        self.owner_id = owner_id

        self.nome = discord.ui.TextInput(
            label="Nome da peça",
            max_length=40
        )

        self.simbolo = discord.ui.TextInput(
            label="Símbolo/aparência",
            placeholder="Ex.: 🐐",
            max_length=8
        )

        self.metodo = discord.ui.TextInput(
            label="Método",
            placeholder="base ou do_zero",
            max_length=10
        )

        self.base = discord.ui.TextInput(
            label="Peça base (se usar método base)",
            placeholder="peao, cavalo, bispo, torre, dama, rei",
            required=False,
            max_length=20
        )

        self.offsets = discord.ui.TextInput(
            label="Movimentos (dx,dy separados por ;)",
            placeholder="4,3;4,-3;-4,3;-4,-3",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=300
        )

        self.add_item(self.nome)
        self.add_item(self.simbolo)
        self.add_item(self.metodo)
        self.add_item(self.base)
        self.add_item(self.offsets)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        metodo = str(
            self.metodo.value
        ).strip().lower()

        if metodo not in ("base", "do_zero"):
            await interaction.response.send_message(
                "Método deve ser `base` ou `do_zero`.",
                ephemeral=True
            )
            return

        base = str(
            self.base.value
        ).strip().lower()

        if metodo == "base" and base not in BASE_PIECES:
            await interaction.response.send_message(
                "Peça base inválida.",
                ephemeral=True
            )
            return

        try:
            offsets = validar_offsets(
                str(self.offsets.value)
            ) if str(self.offsets.value).strip() else []

        except ValueError as erro:
            await interaction.response.send_message(
                str(erro),
                ephemeral=True
            )
            return

        if metodo == "do_zero" and not offsets:
            await interaction.response.send_message(
                "Peças criadas do zero precisam de movimentos.",
                ephemeral=True
            )
            return

        peca = criar_definicao_peca(
            str(self.nome.value).strip(),
            str(self.simbolo.value).strip(),
            metodo,
            offsets,
            base if metodo == "base" else None
        )

        peca_id = peca["id"]

        async with admin_lock:
            admin_data["pieces"][peca_id] = peca

            if not salvar_admin():
                admin_data["pieces"].pop(
                    peca_id,
                    None
                )

                await interaction.response.send_message(
                    "Falha ao salvar a peça.",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            embed=embed_sucesso(
                f"Peça **{peca['name']}** criada.\n"
                f"ID: `{peca_id}`\n"
                f"Símbolo: {peca['symbol']}\n"
                "Ela foi salva na biblioteca."
            ),
            ephemeral=True
        )


class EditarPecaModal(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(
            title="Editar peça",
            timeout=180
        )

        self.owner_id = owner_id

        self.peca_id = discord.ui.TextInput(
            label="ID da peça",
            max_length=12
        )

        self.nome = discord.ui.TextInput(
            label="Novo nome",
            required=False,
            max_length=40
        )

        self.simbolo = discord.ui.TextInput(
            label="Novo símbolo",
            required=False,
            max_length=8
        )

        self.offsets = discord.ui.TextInput(
            label="Novos movimentos",
            placeholder="4,3;4,-3;-4,3;-4,-3",
            required=False,
            max_length=300
        )

        self.add_item(self.peca_id)
        self.add_item(self.nome)
        self.add_item(self.simbolo)
        self.add_item(self.offsets)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        peca_id = str(
            self.peca_id.value
        ).strip().upper()

        peca = obter_peca_personalizada(peca_id)

        if peca is None:
            await interaction.response.send_message(
                "Peça não encontrada.",
                ephemeral=True
            )
            return

        anterior = copiar_json(peca)

        nome = str(self.nome.value).strip()
        simbolo = str(self.simbolo.value).strip()
        texto_offsets = str(self.offsets.value).strip()

        if nome:
            peca["name"] = nome

        if simbolo:
            peca["symbol"] = simbolo

        if texto_offsets:
            try:
                peca["offsets"] = validar_offsets(
                    texto_offsets
                )
            except ValueError as erro:
                await interaction.response.send_message(
                    str(erro),
                    ephemeral=True
                )
                return

        peca["updated_at"] = data_iso()

        if not salvar_admin():
            admin_data["pieces"][peca_id] = anterior

            await interaction.response.send_message(
                "Falha ao salvar. Alterações revertidas.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Peça `{peca_id}` atualizada.",
            ephemeral=True
        )


class PecaIdModal(discord.ui.Modal):
    def __init__(self, owner_id, acao):
        super().__init__(
            title="Gerenciar peça",
            timeout=180
        )

        self.owner_id = owner_id
        self.acao = acao

        self.peca_id = discord.ui.TextInput(
            label="ID da peça",
            max_length=12
        )

        self.add_item(self.peca_id)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        peca_id = str(
            self.peca_id.value
        ).strip().upper()

        peca = obter_peca_personalizada(peca_id)

        if peca is None:
            await interaction.response.send_message(
                "Peça não encontrada.",
                ephemeral=True
            )
            return

        if self.acao == "consultar":
            embed = discord.Embed(
                title=f"{peca['symbol']} {peca['name']}",
                color=discord.Color.blurple()
            )

            embed.add_field(
                name="ID",
                value=peca_id
            )

            embed.add_field(
                name="Método",
                value=peca["method"]
            )

            embed.add_field(
                name="Peça base",
                value=peca.get("base_piece") or "Do zero"
            )

            embed.add_field(
                name="Movimentos",
                value=(
                    str(peca.get("offsets"))
                    if peca.get("offsets")
                    else "Herdados da peça base"
                ),
                inline=False
            )

            embed.add_field(
                name="Propriedades",
                value=str(peca.get("properties")),
                inline=False
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )
            return

        if self.acao == "duplicar":
            nova = copiar_json(peca)
            novo_id = gerar_id()[:8]

            nova["id"] = novo_id
            nova["name"] = (
                nova["name"] + " (cópia)"
            )
            nova["created_at"] = data_iso()
            nova["updated_at"] = data_iso()

            admin_data["pieces"][novo_id] = nova

            if not salvar_admin():
                admin_data["pieces"].pop(
                    novo_id,
                    None
                )

                await interaction.response.send_message(
                    "Falha ao duplicar.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"Peça duplicada. Novo ID: `{novo_id}`",
                ephemeral=True
            )
            return

        if self.acao == "excluir":
            anterior = copiar_json(peca)

            admin_data["pieces"].pop(peca_id)

            if not salvar_admin():
                admin_data["pieces"][peca_id] = anterior

                await interaction.response.send_message(
                    "Falha ao excluir.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"Peça `{peca_id}` removida da biblioteca.",
                ephemeral=True
            )


class PecasAdminView(discord.ui.View):

    def __init__(self, owner_id, guild_id):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="Criar peça",
        emoji="➕",
        style=discord.ButtonStyle.success
    )
    async def criar(self, interaction, button):
        await interaction.response.send_modal(
            CriarPecaModal(self.owner_id)
        )

    @discord.ui.button(
        label="Editar peça",
        emoji="✏️",
        style=discord.ButtonStyle.primary
    )
    async def editar(self, interaction, button):
        await interaction.response.send_modal(
            EditarPecaModal(self.owner_id)
        )

    @discord.ui.button(
        label="Consultar peça",
        emoji="🔎",
        style=discord.ButtonStyle.primary
    )
    async def consultar(self, interaction, button):
        await interaction.response.send_modal(
            PecaIdModal(
                self.owner_id,
                "consultar"
            )
        )

    @discord.ui.button(
        label="Duplicar peça",
        emoji="📄",
        style=discord.ButtonStyle.secondary
    )
    async def duplicar(self, interaction, button):
        await interaction.response.send_modal(
            PecaIdModal(
                self.owner_id,
                "duplicar"
            )
        )

    @discord.ui.button(
        label="Excluir peça",
        emoji="🗑️",
        style=discord.ButtonStyle.danger
    )
    async def excluir(self, interaction, button):
        await interaction.response.send_modal(
            PecaIdModal(
                self.owner_id,
                "excluir"
            )
        )

    @discord.ui.button(
        label="Voltar",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def voltar(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🛠️ DisChessLord — Dashboard",
                description="Painel administrativo principal.",
                color=discord.Color.blurple()
            ),
            view=DashboardView(
                self.owner_id,
                self.guild_id
            )
        )


# ============================================================
# 28. POSICIONAMENTO DE PEÇAS PERSONALIZADAS
# ============================================================

class PosicionamentoPecaModal(discord.ui.Modal):
    def __init__(self, owner_id, guild_id):
        super().__init__(
            title="Configurar posicionamento",
            timeout=180
        )

        self.owner_id = owner_id
        self.guild_id = guild_id

        self.peca_id = discord.ui.TextInput(
            label="ID da peça personalizada",
            max_length=12
        )

        self.casas = discord.ui.TextInput(
            label="Casas (ex.: a2,d2,a7,d7)",
            max_length=100
        )

        self.lados = discord.ui.TextInput(
            label="Lados: brancas, pretas ou ambos",
            max_length=20
        )

        self.modo = discord.ui.TextInput(
            label="Modo: substituir ou colocar",
            max_length=20
        )

        self.add_item(self.peca_id)
        self.add_item(self.casas)
        self.add_item(self.lados)
        self.add_item(self.modo)

    async def on_submit(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return

        peca_id = str(
            self.peca_id.value
        ).strip().upper()

        peca = obter_peca_personalizada(peca_id)

        if peca is None:
            await interaction.response.send_message(
                "Peça não encontrada.",
                ephemeral=True
            )
            return

        casas_texto = str(
            self.casas.value
        ).strip().lower()

        casas = [
            casa.strip()
            for casa in casas_texto.split(",")
            if casa.strip()
        ]

        if not casas:
            await interaction.response.send_message(
                "Informe ao menos uma casa.",
                ephemeral=True
            )
            return

        invalidas = []

        for casa in casas:
            try:
                chess.parse_square(casa)
            except ValueError:
                invalidas.append(casa)

        if invalidas:
            await interaction.response.send_message(
                "Casas inválidas: " + ", ".join(invalidas),
                ephemeral=True
            )
            return

        lados = str(
            self.lados.value
        ).strip().lower()

        if lados not in (
            "brancas",
            "pretas",
            "ambos"
        ):
            await interaction.response.send_message(
                "Lado inválido.",
                ephemeral=True
            )
            return

        modo = str(
            self.modo.value
        ).strip().lower()

        if modo not in (
            "substituir",
            "colocar"
        ):
            await interaction.response.send_message(
                "Modo inválido.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Configuração validada.\n"
            f"Peça: **{peca['name']}**\n"
            f"Casas: `{', '.join(casas)}`\n"
            f"Lados: **{lados}**\n"
            f"Modo: **{modo}**\n\n"
            "Esse posicionamento foi validado como configuração, "
            "mas ainda não é aplicado a partidas: o motor atual "
            "continua sendo o motor clássico do python-chess.",
            ephemeral=True
        )


class PosicionamentoAdminView(discord.ui.View):

    def __init__(self, owner_id, guild_id):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or not autorizado_dev(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Sem autorização.",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="Configurar casas",
        emoji="📍",
        style=discord.ButtonStyle.primary
    )
    async def configurar(self, interaction, button):
        await interaction.response.send_modal(
            PosicionamentoPecaModal(
                self.owner_id,
                self.guild_id
            )
        )


# ============================================================
# 29. COMANDO PARA POSICIONAMENTO
# ============================================================

@bot.tree.command(
    name="dev_posicionar_peca",
    description="Configura o posicionamento de uma peça personalizada."
)
async def dev_posicionar_peca(interaction):
    if not autorizado_dev(interaction.user.id):
        await interaction.response.send_message(
            "Sem autorização.",
            ephemeral=True
        )
        return

    view = PosicionamentoAdminView(
        interaction.user.id,
        interaction.guild_id
    )

    await interaction.response.send_message(
        "📍 Configuração de posicionamento de peças.",
        view=view,
        ephemeral=True
    )


# ============================================================
# 30. COMANDO DE LISTAGEM DE EVENTOS E TORNEIOS
# ============================================================

@bot.tree.command(
    name="eventos",
    description="Veja os eventos do servidor."
)
async def eventos(interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Use este comando em um servidor.",
            ephemeral=True
        )
        return

    atualizar_eventos_expirados()

    lista = [
        evento
        for evento in admin_data["events"].values()
        if str(evento.get("guild_id")) == str(interaction.guild_id)
        and evento.get("status") == "ativo"
    ]

    if not lista:
        await interaction.response.send_message(
            "Não existem eventos ativos.",
            ephemeral=True
        )
        return

    linhas = []

    for evento in lista[:10]:
        linhas.append(
            f"🎉 **{evento['name']}**\n"
            f"Término: `{evento['ends_at']}`"
        )

    embed = discord.Embed(
        title="🎉 Eventos ativos",
        description="\n\n".join(linhas),
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(
        embed=embed
    )


@bot.tree.command(
    name="torneios",
    description="Veja os torneios do servidor."
)
async def torneios(interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Use este comando em um servidor.",
            ephemeral=True
        )
        return

    lista = [
        t for t in torneios_do_servidor(interaction.guild_id)
        if t.get("status") in ("inscricoes", "em_andamento", "ativo")
    ]

    if not lista:
        await interaction.response.send_message(
            "Não existem torneios registrados.",
            ephemeral=True
        )
        return

    linhas = []

    for torneio in lista[:10]:
        linhas.append(
            f"🏆 **{torneio['name']}**\n"
            f"Status: **{torneio['status']}**\n"
            f"Prêmio: **{torneio.get('prize') or 'Não definido'}**\n"
            f"Participantes: **{len(torneio['participants'])}**"
        )

    embed = discord.Embed(
        title="🏆 Torneios do servidor",
        description="\n\n".join(linhas),
        color=discord.Color.gold()
    )

    await interaction.response.send_message(
        embed=embed
    )


@bot.tree.command(
    name="historico_torneio",
    description="Veja o histórico de torneios encerrados ou cancelados."
)
async def historico_torneio(interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Use este comando em um servidor.", ephemeral=True
        )
        return

    lista = [
        t for t in torneios_do_servidor(interaction.guild_id)
        if t.get("status") not in ("inscricoes", "em_andamento", "ativo")
    ]
    if not lista:
        await interaction.response.send_message(
            "Ainda não há torneios encerrados ou cancelados.", ephemeral=True
        )
        return

    linhas = []
    for torneio in lista[:10]:
        vencedor_id = torneio.get("winner")
        vencedor = (
            nome_jogador(vencedor_id, interaction.guild)
            if vencedor_id else "Não definido"
        )
        linhas.append(
            f"🏆 **{torneio.get('name', 'Torneio')}**\n"
            f"Status: **{torneio.get('status', 'encerrado')}** • "
            f"Prêmio: **{torneio.get('prize') or 'Não definido'}**\n"
            f"Vencedor: **{vencedor}**"
        )

    embed = discord.Embed(
        title="📚 Histórico de torneios",
        description="\n\n".join(linhas),
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed)



# ============================================================
# 31. INSCRIÇÃO EM TORNEIOS
# ============================================================

@bot.tree.command(
    name="inscrever_torneio",
    description="Inscreva-se em um torneio."
)
@app_commands.describe(
    nome_torneio="Nome exato do torneio"
)
async def inscrever_torneio(
    interaction,
    nome_torneio: str
):
    nome = " ".join(nome_torneio.strip().split()).casefold()
    encontrados = [t for t in torneios_do_servidor(interaction.guild_id)
                   if " ".join(str(t.get("name", "")).split()).casefold() == nome]
    if len(encontrados) != 1:
        await interaction.response.send_message("Torneio não encontrado ou nome duplicado. Confira /torneios e use o nome exato.", ephemeral=True)
        return
    torneio = encontrados[0]

    if torneio is None:
        await interaction.response.send_message(
            "Torneio não encontrado.",
            ephemeral=True
        )
        return

    if str(torneio["guild_id"]) != str(interaction.guild_id):
        await interaction.response.send_message(
            "Esse torneio pertence a outro servidor.",
            ephemeral=True
        )
        return

    if torneio["status"] != "inscricoes":
        await interaction.response.send_message(
            "As inscrições não estão abertas.",
            ephemeral=True
        )
        return

    user_id = str(interaction.user.id)

    if user_id in torneio["participants"]:
        await interaction.response.send_message(
            "Você já está inscrito.",
            ephemeral=True
        )
        return

    anterior = copiar_json(torneio)

    torneio["participants"].append(user_id)

    if not salvar_admin():
        admin_data["tournaments"][torneio["id"]] = anterior

        await interaction.response.send_message(
            "Falha ao salvar inscrição.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ Você se inscreveu em **{torneio['name']}**."
    )


# ============================================================
# 32. LIMPAR CHAT
# ============================================================

@bot.tree.command(
    name="limparchat",
    description="Apaga uma quantidade de mensagens deste canal (somente dono do servidor)."
)
@app_commands.describe(
    quantidade="Quantidade de mensagens para apagar (1 a 1000)"
)
@app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
async def limparchat(interaction, quantidade: app_commands.Range[int, 1, 1000]):
    if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(
            "❌ Apenas o dono oficial deste servidor pode usar este comando.",
            ephemeral=True
        )
        return

    canal = interaction.channel
    if not hasattr(canal, "purge"):
        await interaction.response.send_message(
            "❌ Este tipo de canal não permite limpar mensagens.",
            ephemeral=True
        )
        return

    permissoes = canal.permissions_for(interaction.guild.me)
    if not permissoes.manage_messages or not permissoes.read_message_history:
        await interaction.response.send_message(
            "❌ Preciso das permissões **Gerenciar mensagens** e **Ler histórico de mensagens** neste canal.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        apagadas = await canal.purge(
            limit=quantidade,
            bulk=True,
            reason=f"/limparchat usado pelo dono do servidor {interaction.user.id}"
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Não tenho permissão para apagar mensagens neste canal.",
            ephemeral=True
        )
        return
    except discord.HTTPException:
        logging.exception("Falha ao limpar o canal %s.", canal.id)
        await interaction.followup.send(
            "❌ O Discord não permitiu concluir a limpeza. Verifique minhas permissões e tente novamente.",
            ephemeral=True
        )
        return

    await interaction.followup.send(
        f"🧹 **Limpeza concluída!** Apaguei {len(apagadas)} mensagem(ns) neste canal.",
        ephemeral=True
    )


# ============================================================
# 32. COMANDO DE AJUDA
# ============================================================

@bot.tree.command(name="help", description="Veja todos os comandos do DisChessLord.")
async def help_command(interaction):
    embed = discord.Embed(title="♟️ DisChessLord — Ajuda",
                          description="Comandos disponíveis organizados por categoria.",
                          color=discord.Color.blurple())
    embed.add_field(name="♟️ Comandos normais",
        value="\`/desafiar\` — desafiar jogador\n\`/nick\` — definir nick\n\`/perfil\` — consultar perfil\n\`/ranking\` — ranking de ELO\n\`/historico\` — histórico de partidas\n\`/analisar\` — ver PGN\n\`/saldo\` — saldo de ChessCoins\n\`/eventos\` — eventos ativos\n\`/torneios\` — torneios ativos\n\`/inscrever_torneio\` — inscrever-se\n\`/sorteios\` — sorteios abertos\n\`/participar_sorteio\` — entrar em sorteio", inline=False)
    embed.add_field(name="🛡️ Comandos administrativos",
        value="\`/adm_dashboard\` — painel de moderação\n\`/limparchat\` — limpar mensagens (dono do servidor)", inline=False)
    embed.add_field(name="⚙️ Comandos de desenvolvedor",
        value="\`/dev_dashboard\` — painel completo\n\`/resetar\` — resetar perfis\n\`/dev_posicionar_peca\` — administrar peças", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# 33. INICIALIZAÇÃO
# ============================================================

@bot.event
async def on_ready():
    global data_loaded

    if not data_loaded:
        carregar_dados()
        carregar_admin()
        data_loaded = True

    atualizar_eventos_expirados()

    try:
        sincronizados = await bot.tree.sync()

        logging.info(
            "%s comandos sincronizados.",
            len(sincronizados)
        )

    except Exception:
        logging.exception(
            "Erro ao sincronizar comandos."
        )

    logging.info(
        "DisChessLord conectado como %s.",
        bot.user
    )


# ============================================================
# 34. EXECUÇÃO
# ============================================================

carregar_dados()
carregar_admin()

data_loaded = True

bot.run(TOKEN)