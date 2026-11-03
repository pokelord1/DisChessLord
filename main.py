import os
import json
import discord
from discord.ext import commands
import chess

# Configuração do Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Necessário para gerir os cargos dos membros

bot = commands.Bot(command_prefix="!", intents=intents)

# Ficheiro para persistência do ELO
ELO_FILE = "elo_data.json"

# Dicionários de dados
user_elo = {}
active_games = {}  # {channel_id: {"board": chess.Board(), "white": user_id, "black": user_id}}

# Carregar ELO do ficheiro se existir
def load_elo():
    global user_elo
    if os.path.exists(ELO_FILE):
        try:
            with open(ELO_FILE, "r") as f:
                user_elo = json.load(f)
        except Exception:
            user_elo = {}

# Guardar ELO no ficheiro
def save_elo():
    with open(ELO_FILE, "w") as f:
        json.dump(user_elo, f, indent=4)

def get_user_elo(user_id):
    return user_elo.get(str(user_id), 1000)  # ELO inicial padrão: 1000

# Tabela de Patentes por ELO
PATENTES = [
    (0, 499, "Iniciante"),
    (500, 999, "Amador"),
    (1000, 1499, "Intermediario"),
    (1500, 1999, "Mestre"),
    (2000, 2999, "Grão Mestre"),
    (3000, 99999, "Lenda do Xadrez")
]

async def atualizar_cargo_elo(member: discord.Member, novo_elo: int):
    if not member:
        return
    
    # Descobre o nome do cargo correto baseado no ELO
    nome_cargo_correto = "Iniciante"
    for min_elo, max_elo, nome in PATENTES:
        if min_elo <= novo_elo <= max_elo:
            nome_cargo_correto = nome
            break
            
    guild = member.guild
    nomes_todas_patentes = [p[2] for p in PATENTES]
    
    # Remove cargos de patente antigos que o jogador possa ter
    cargos_para_remover = [role for role in member.roles if role.name in nomes_todas_patentes and role.name != nome_cargo_correto]
    if cargos_para_remover:
        try:
            await member.remove_roles(*cargos_para_remover)
        except Exception:
            pass
        
    # Procura ou cria o cargo correto no servidor
    cargo_novo = discord.utils.get(guild.roles, name=nome_cargo_correto)
    if not cargo_novo:
        try:
            cargo_novo = await guild.create_role(name=nome_cargo_correto, reason="Cargo de Patente do Dischess")
        except Exception:
            return
            
    # Atribui o cargo correto ao jogador
    if cargo_novo not in member.roles:
        try:
            await member.add_roles(cargo_novo)
        except Exception:
            pass

# Função para desenhar o tabuleiro em texto simétrico
def desenhar_tabuleiro(board: chess.Board):
    pecas_unicode = {
        'R': '♖', 'N': '♘', 'B': '♗', 'Q': '♕', 'K': '♔', 'P': '♙',
        'r': '♜', 'n': '♞', 'b': '♝', 'q': '♛', 'k': '♚', 'p': '♟',
        '.': '.'
    }
    
    linhas = []
    linhas.append("    a   b   c   d   e   f   g   h")
    linhas.append("  +---+---+---+---+---+---+---+---+")
    
    fen_board = board.fen().split()[0]
    ranks = fen_board.split('/')
    
    for i, rank in enumerate(ranks):
        row_str = f"{8 - i} |"
        for char in rank:
            if char.isdigit():
                for _ in range(int(char)):
                    row_str += " . |"
            else:
                row_str += f" {pecas_unicode.get(char, char)} |"
        row_str += f" {8 - i}"
        linhas.append(row_str)
        linhas.append("  +---+---+---+---+---+---+---+---+")
        
    linhas.append("    a   b   c   d   e   f   g   h")
    return "```\n" + "\n".join(linhas) + "\n```"

@bot.event
async def on_ready():
    load_elo()
    print(f"Dischess Hub ligado como {bot.user}!")
    try:
        synced = await bot.tree.sync()
        print(f"Sincronizados {len(synced)} comandos de barra (/ ).")
    except Exception as e:
        print(e)

@bot.tree.command(name="desafiar", description="Desafia outro membro para uma partida de xadrez.")
async def desafiar(interaction: discord.Interaction, oponente: discord.Member):
    if oponente == interaction.user:
        await interaction.response.send_message("Não podes jogar contra ti próprio!", ephemeral=True)
        return
    if oponente.bot:
        await interaction.response.send_message("Não podes jogar contra bots do Discord!", ephemeral=True)
        return

    channel_id = interaction.channel.id
    if channel_id in active_games:
        await interaction.response.send_message("Já existe uma partida de xadrez a decorrer neste canal!", ephemeral=True)
        return

    active_games[channel_id] = {
        "board": chess.Board(),
        "white": interaction.user,
        "black": oponente
    }

    board = active_games[channel_id]["board"]
    tabuleiro_txt = desenhar_tabuleiro(board)

    embed = discord.Embed(
        title="⚔️ Partida de Xadrez Iniciada!",
        description=f"**Brancas:** {interaction.user.mention} ({get_user_elo(interaction.user.id)} ELO)\n**Pretas:** {oponente.mention} ({get_user_elo(oponente.id)} ELO)\n\n**Vez de:** {interaction.user.mention} (Brancas)\n{tabuleiro_txt}\nUsa `/jogar <movimento>` (ex: e2e4)",
        color=discord.Color.blue()
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="jogar", description="Faz um movimento no xadrez (ex: e2e4).")
async def jogar(interaction: discord.Interaction, movimento: str):
    channel_id = interaction.channel.id
    if channel_id not in active_games:
        await interaction.response.send_message("Não há nenhuma partida ativa neste canal. Usa `/desafiar`!", ephemeral=True)
        return

    game = active_games[channel_id]
    board = game["board"]
    white = game["white"]
    black = game["black"]

    # Verificar quem tem a vez
    vez_atual = white if board.turn == chess.WHITE else black
    if interaction.user != vez_atual:
        await interaction.response.send_message("Não é a tua vez de jogar!", ephemeral=True)
        return

    try:
        move = chess.Move.from_uci(movimento)
        if move in board.legal_moves:
            board.push(move)
        else:
            await interaction.response.send_message("Movimento inválido segundo as regras do xadrez!", ephemeral=True)
            return
    except Exception:
        await interaction.response.send_message("Formato inválido! Usa o formato UCI (ex: e2e4, g1f3).", ephemeral=True)
        return

    # Verificar fim de jogo
    if board.is_game_over():
        result = board.result()
        
        # Atribuir/Atualizar ELO
        w_id = str(white.id)
        b_id = str(black.id)
        
        w_elo = get_user_elo(white.id)
        b_elo = get_user_elo(black.id)
        
        k = 32
        if result == "1-0": # Brancas ganham
            w_elo += int(k * (1 - 1/(1 + 10**((b_elo - w_elo)/400))))
            b_elo += int(k * (0 - 1/(1 + 10**((w_elo - b_elo)/400))))
            msg_fim = f"🏆 **Fim de Jogo!** As Brancas ({white.mention}) venceram por xeque-mate!"
        elif result == "0-1": # Pretas ganham
            w_elo += int(k * (0 - 1/(1 + 10**((b_elo - w_elo)/400))))
            b_elo += int(k * (1 - 1/(1 + 10**((w_elo - b_elo)/400))))
            msg_fim = f"🏆 **Fim de Jogo!** As Pretas ({black.mention}) venceram por xeque-mate!"
        else: # Empate
            msg_fim = "🤝 **Fim de Jogo!** A partida terminou em empate."

        user_elo[w_id] = max(100, w_elo)
        user_elo[b_id] = max(100, b_elo)
        save_elo()
        
        # Atualizar cargos automáticos de ELO
        if interaction.guild:
            await atualizar_cargo_elo(white, user_elo[w_id])
            await atualizar_cargo_elo(black, user_elo[b_id])

        del active_games[channel_id]
        tabuleiro_txt = desenhar_tabuleiro(board)
        await interaction.response.send_message(f"{msg_fim}\n{tabuleiro_txt}\nNovos ELOs:\n{white.name}: `{user_elo[w_id]}`\n{black.name}: `{user_elo[b_id]}`")
        return

    # Continuar o jogo se não acabou
    tabuleiro_txt = desenhar_tabuleiro(board)
    proxima_vez = white if board.turn == chess.WHITE else black

    embed = discord.Embed(
        title="♟️ Partida em Andamento",
        description=f"Movimento efetuado: `{movimento}`\n**Vez de:** {proxima_vez.mention}\n{tabuleiro_txt}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="elo", description="Consulta o teu ELO atual ou de outro jogador.")
async def elo(interaction: discord.Interaction, membro: discord.Member = None):
    target = membro or interaction.user
    current_elo = get_user_elo(target.id)
    
    patente_atual = "Iniciante"
    for min_elo, max_elo, nome in PATENTES:
        if min_elo <= current_elo <= max_elo:
            patente_atual = nome
            break

    embed = discord.Embed(
        title=f"📊 Estatísticas de Xadrez - {target.name}",
        description=f"**ELO:** `{current_elo}`\n**Patente:** `{patente_atual}`",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ranking", description="Mostra o ranking global do servidor.")
async def ranking(interaction: discord.Interaction):
    if not user_elo:
        await interaction.response.send_message("📊 Ainda não há registos de ELO no ranking.", ephemeral=True)
        return

    sorted_users = sorted(user_elo.items(), key=lambda item: item[1], reverse=True)
    
    description = ""
    for index, (uid, elo_val) in enumerate(sorted_users[:10], start=1):
        patente = "Iniciante"
        for min_elo, max_elo, nome in PATENTES:
            if min_elo <= elo_val <= max_elo:
                patente = nome
                break
                
        user_obj = interaction.guild.get_member(int(uid))
        name = user_obj.name if user_obj else f"Utilizador ({uid})"
        description += f"**{index}.** {name} — **{elo_val} ELO** *({patente})*\n"

    embed = discord.Embed(
        title="👑 Top 10 - Ranking Global de Xadrez",
        description=description,
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed)

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("ERRO: O token do Discord não foi encontrado nas variáveis de ambiente.")
