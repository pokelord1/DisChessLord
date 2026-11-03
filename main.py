import os
import json
import discord
from discord.ext import commands
from discord.ui import View, button
import chess

# Configuração do Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ELO_FILE = "elo_data.json"
user_elo = {}
active_games = {}  # {channel_id: {"board": chess.Board(), "white": user_id, "black": user_id, "draw_offered_by": None}}

# Carregar dados do ELO e estatísticas
def load_elo():
    global user_elo
    if os.path.exists(ELO_FILE):
        try:
            with open(ELO_FILE, "r") as f:
                user_elo = json.load(f)
        except Exception:
            user_elo = {}

# Guardar dados no ficheiro
def save_elo():
    with open(ELO_FILE, "w") as f:
        json.dump(user_elo, f, indent=4)

# Retorna a estrutura do perfil do jogador
def get_user_data(user_id):
    uid = str(user_id)
    if uid not in user_elo:
        user_elo[uid] = {
            "elo": 1000,
            "peak_elo": 1000,
            "wins": 0,
            "losses": 0,
            "draws": 0
        }
    else:
        # Garantir compatibilidade com dados antigos que só tinham int
        if isinstance(user_elo[uid], int):
            old_val = user_elo[uid]
            user_elo[uid] = {
                "elo": old_val,
                "peak_elo": old_val,
                "wins": 0,
                "losses": 0,
                "draws": 0
            }
    return user_elo[uid]

PATENTES = [
    (0, 499, "Iniciante"),
    (500, 999, "Amador"),
    (1000, 1499, "Intermediario"),
    (1500, 1999, "Mestre"),
    (2000, 2999, "Grão Mestre"),
    (3000, 99999, "Lenda do Xadrez")
]

def obter_patente(elo: int):
    for min_elo, max_elo, nome in PATENTES:
        if min_elo <= elo <= max_elo:
            return nome
    return "Iniciante"

async def atualizar_cargo_elo(member: discord.Member, novo_elo: int, canal=None):
    if not member:
        return
    
    nome_cargo_correto = obter_patente(novo_elo)
    guild = member.guild
    nomes_todas_patentes = [p[2] for p in PATENTES]
    
    # Verificar se subiu de patente para mandar o Rank Up!
    cargo_antigo = None
    for r in member.roles:
        if r.name in nomes_todas_patentes:
            cargo_antigo = r.name
            break

    cargos_para_remover = [role for role in member.roles if role.name in nomes_todas_patentes and role.name != nome_cargo_correto]
    if cargos_para_remover:
        try:
            await member.remove_roles(*cargos_para_remover)
        except Exception:
            pass
        
    cargo_novo = discord.utils.get(guild.roles, name=nome_cargo_correto)
    if not cargo_novo:
        try:
            cargo_novo = await guild.create_role(name=nome_cargo_correto, reason="Cargo de Patente do Dischess")
        except Exception:
            return
            
    if cargo_novo not in member.roles:
        try:
            await member.add_roles(cargo_novo)
            # Anúncio de Subida de Patente (Rank Up!)
            if cargo_antigo and cargo_antigo != nome_cargo_correto and canal:
                idx_antigo = nomes_todas_patentes.index(cargo_antigo) if cargo_antigo in nomes_todas_patentes else -1
                idx_novo = nomes_todas_patentes.index(nome_cargo_correto)
                if idx_novo > idx_antigo:
                    await canal.send(f"🎉 **RANK UP!** {member.mention} subiu de patente e agora é **{nome_cargo_correto}**! 👑")
        except Exception:
            pass

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

def registrar_resultado(vencedor_id, perdedor_id, eh_empate=False):
    if eh_empate:
        data1 = get_user_data(vencedor_id)
        data2 = get_user_data(perdedor_id)
        data1["draws"] += 1
        data2["draws"] += 1
    else:
        v_data = get_user_data(vencedor_id)
        p_data = get_user_data(perdedor_id)
        v_data["wins"] += 1
        p_data["losses"] += 1

# Componente Visual com Botões de Ação
class ChessControlView(View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @button(label="Propor / Aceitar Empate", style=discord.ButtonStyle.secondary, emoji="🤝")
    async def btn_empate(self, interaction: discord.Interaction, button_obj: discord.ui.Button):
        if self.channel_id not in active_games:
            await interaction.response.send_message("Partida não encontrada.", ephemeral=True)
            return

        game = active_games[self.channel_id]
        if interaction.user not in (game["white"], game["black"]):
            await interaction.response.send_message("Não estás a participar nesta partida!", ephemeral=True)
            return

        if game.get("draw_offered_by") is None:
            game["draw_offered_by"] = interaction.user
            await interaction.response.send_message(f"🤝 {interaction.user.mention} propôs um empate! O oponente pode clicar no botão para aceitar.", ephemeral=False)
        elif game["draw_offered_by"] == interaction.user:
            await interaction.response.send_message("Já propuseste um empate. Aguarda a resposta do oponente!", ephemeral=True)
        else:
            white, black = game["white"], game["black"]
            registrar_resultado(white.id, black.id, eh_empate=True)
            save_elo()
            del active_games[self.channel_id]
            await interaction.response.send_message(f"🤝 **Empate Aceito!** A partida entre {white.mention} e {black.mention} terminou em empate.")

    @button(label="Desistir / Render-se", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def btn_desistir(self, interaction: discord.Interaction, button_obj: discord.ui.Button):
        if self.channel_id not in active_games:
            await interaction.response.send_message("Partida não encontrada.", ephemeral=True)
            return

        game = active_games[self.channel_id]
        if interaction.user not in (game["white"], game["black"]):
            await interaction.response.send_message("Não estás a participar nesta partida!", ephemeral=True)
            return

        white, black = game["white"], game["black"]
        vencedor = black if interaction.user == white else white
        perdedor = interaction.user

        w_data = get_user_data(white.id)
        b_data = get_user_data(black.id)
        w_elo, b_elo = w_data["elo"], b_data["elo"]
        k = 32

        if vencedor == white:
            w_elo += int(k * (1 - 1/(1 + 10**((b_elo - w_elo)/400))))
            b_elo += int(k * (0 - 1/(1 + 10**((w_elo - b_elo)/400))))
        else:
            w_elo += int(k * (0 - 1/(1 + 10**((b_elo - w_elo)/400))))
            b_elo += int(k * (1 - 1/(1 + 10**((w_elo - b_elo)/400))))

        w_data["elo"] = max(100, w_elo)
        b_data["elo"] = max(100, b_elo)
        w_data["peak_elo"] = max(w_data["peak_elo"], w_data["elo"])
        b_data["peak_elo"] = max(b_data["peak_elo"], b_data["elo"])

        registrar_resultado(vencedor.id, perdedor.id, eh_empate=False)
        save_elo()

        if interaction.guild:
            await atualizar_cargo_elo(white, w_data["elo"], interaction.channel)
            await atualizar_cargo_elo(black, b_data["elo"], interaction.channel)

        del active_games[self.channel_id]
        await interaction.response.send_message(f"🏳️ {perdedor.mention} desistiu da partida!\n🏆 **Vencedor:** {vencedor.mention}\n\nNovos ELOs:\n{white.name}: `{w_data['elo']}`\n{black.name}: `{b_data['elo']}`")

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
        "black": oponente,
        "draw_offered_by": None
    }

    board = active_games[channel_id]["board"]
    tabuleiro_txt = desenhar_tabuleiro(board)
    view = ChessControlView(channel_id)

    u1_elo = get_user_data(interaction.user.id)["elo"]
    u2_elo = get_user_data(oponente.id)["elo"]

    embed = discord.Embed(
        title="⚔️ Partida de Xadrez Iniciada!",
        description=f"**Brancas:** {interaction.user.mention} ({u1_elo} ELO)\n**Pretas:** {oponente.mention} ({u2_elo} ELO)\n\n**Vez de:** {interaction.user.mention} (Brancas)\n{tabuleiro_txt}\nUsa `/jogar <movimento>` (ex: e2e4)",
        color=discord.Color.blue()
    )

    await interaction.response.send_message(embed=embed, view=view)

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

    vez_atual = white if board.turn == chess.WHITE else black
    if interaction.user != vez_atual:
        await interaction.response.send_message("Não é a tua vez de jogar!", ephemeral=True)
        return

    try:
        move = chess.Move.from_uci(movimento)
        if move in board.legal_moves:
            board.push(move)
            game["draw_offered_by"] = None
        else:
            await interaction.response.send_message("Movimento inválido segundo as regras do xadrez!", ephemeral=True)
            return
    except Exception:
        await interaction.response.send_message("Formato inválido! Usa o formato UCI (ex: e2e4, g1f3).", ephemeral=True)
        return

    if board.is_game_over():
        result = board.result()
        w_data = get_user_data(white.id)
        b_data = get_user_data(black.id)
        w_elo, b_elo = w_data["elo"], b_data["elo"]
        k = 32

        if result == "1-0":
            w_elo += int(k * (1 - 1/(1 + 10**((b_elo - w_elo)/400))))
            b_elo += int(k * (0 - 1/(1 + 10**((w_elo - b_elo)/400))))
            msg_fim = f"🏆 **Fim de Jogo!** As Brancas ({white.mention}) venceram por xeque-mate!"
            registrar_resultado(white.id, black.id, eh_empate=False)
        elif result == "0-1":
            w_elo += int(k * (0 - 1/(1 + 10**((b_elo - w_elo)/400))))
            b_elo += int(k * (1 - 1/(1 + 10**((w_elo - b_elo)/400))))
            msg_fim = f"🏆 **Fim de Jogo!** As Pretas ({black.mention}) venceram por xeque-mate!"
            registrar_resultado(black.id, white.id, eh_empate=False)
        else:
            msg_fim = "🤝 **Fim de Jogo!** A partida terminou em empate."
            registrar_resultado(white.id, black.id, eh_empate=True)

        w_data["elo"] = max(100, w_elo)
        b_data["elo"] = max(100, b_elo)
        w_data["peak_elo"] = max(w_data["peak_elo"], w_data["elo"])
        b_data["peak_elo"] = max(b_data["peak_elo"], b_data["elo"])
        save_elo()

        if interaction.guild:
            await atualizar_cargo_elo(white, w_data["elo"], interaction.channel)
            await atualizar_cargo_elo(black, b_data["elo"], interaction.channel)

        del active_games[channel_id]
        tabuleiro_txt = desenhar_tabuleiro(board)
        await interaction.response.send_message(f"{msg_fim}\n{tabuleiro_txt}\nNovos ELOs:\n{white.name}: `{w_data['elo']}`\n{black.name}: `{b_data['elo']}`")
        return

    tabuleiro_txt = desenhar_tabuleiro(board)
    proxima_vez = white if board.turn == chess.WHITE else black
    view = ChessControlView(channel_id)

    embed = discord.Embed(
        title="♟️ Partida em Andamento",
        description=f"Movimento efetuado: `{movimento}`\n**Vez de:** {proxima_vez.mention}\n{tabuleiro_txt}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="perfil", description="Exibe a Carteirinha de Enxadrista (a tua ou de outro membro).")
async def perfil(interaction: discord.Interaction, membro: discord.Member = None):
    target = membro or interaction.user
    data = get_user_data(target.id)

    elo_atual = data["elo"]
    peak_elo = data["peak_elo"]
    wins = data["wins"]
    losses = data["losses"]
    draws = data["draws"]
    total_jogos = wins + losses + draws

    win_rate = (wins / total_jogos * 100) if total_jogos > 0 else 0.0
    patente_atual = obter_patente(elo_atual)

    # Calcular progresso para a próxima patente
    proxima_patente_nome = "Lenda Máxima"
    min_p, max_p = 0, 3000
    for min_elo, max_elo, nome in PATENTES:
        if min_elo <= elo_atual <= max_elo:
            min_p, max_p = min_elo, max_elo
            idx = PATENTES.index((min_elo, max_elo, nome))
            if idx + 1 < len(PATENTES):
                proxima_patente_nome = PATENTES[idx + 1][2]
                max_p = PATENTES[idx + 1][0]
            break

    intervalo = max_p - min_p
    progresso_pct = min(1.0, max(0.0, (elo_atual - min_p) / intervalo)) if intervalo > 0 else 1.0
    blocos = int(progresso_pct * 10)
    barra = "▰" * blocos + "▱" * (10 - blocos)

    embed = discord.Embed(
        title=f"📜 Carteirinha de Enxadrista — {target.name}",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=target.display_avatar.url)

    embed.add_field(name="🏆 ELO Atual", value=f"`{elo_atual}`", inline=True)
    embed.add_field(name="📈 Maior ELO (Peak)", value=f"`{peak_elo}`", inline=True)
    embed.add_field(name="👑 Patente", value=f"`{patente_atual}`", inline=True)

    embed.add_field(name="⚔️ Partidas Jogadas", value=f"`{total_jogos}`", inline=True)
    embed.add_field(name="🎯 Win Rate", value=f"`{win_rate:.1f}%`", inline=True)
    embed.add_field(name="📊 V / D / E", value=f"`{wins}W` / `{losses}L` / `{draws}D`", inline=True)

    embed.add_field(
        name=f"🚀 Progresso para {proxima_patente_nome}",
        value=f"{barra} `{int(progresso_pct*100)}%` ({elo_atual}/{max_p} ELO)",
        inline=False
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ranking", description="Mostra o ranking global do servidor.")
async def ranking(interaction: discord.Interaction):
    if not user_elo:
        await interaction.response.send_message("📊 Ainda não há registos de ELO no ranking.", ephemeral=True)
        return

    sorted_users = sorted(
        user_elo.items(),
        key=lambda item: item[1]["elo"] if isinstance(item[1], dict) else item[1],
        reverse=True
    )
    
    description = ""
    for index, (uid, udata) in enumerate(sorted_users[:10], start=1):
        elo_val = udata["elo"] if isinstance(udata, dict) else udata
        patente = obter_patente(elo_val)
                
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
