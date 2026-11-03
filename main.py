import os
import json
import urllib.request
import urllib.parse
import discord
from discord.ext import commands
from discord.ui import View, Button
import chess

# Configuração do Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ELO_FILE = "elo_data.json"
user_elo = {}
active_games = {}
pending_challenges = {}

ADMIN_ID = 1369324713498906786

def load_elo():
    global user_elo
    if os.path.exists(ELO_FILE):
        try:
            with open(ELO_FILE, "r") as f:
                user_elo = json.load(f)
        except Exception:
            user_elo = {}

def save_elo():
    with open(ELO_FILE, "w") as f:
        json.dump(user_elo, f, indent=4)

def get_user_data(user_id):
    uid = str(user_id)
    if uid not in user_elo:
        user_elo[uid] = {
            "elo": 100,
            "peak_elo": 100,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "match_history": []
        }
        save_elo()
    else:
        if isinstance(user_elo[uid], int):
            old_val = user_elo[uid]
            user_elo[uid] = {
                "elo": old_val,
                "peak_elo": old_val,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "match_history": []
            }
            save_elo()
        if "match_history" not in user_elo[uid]:
            user_elo[uid]["match_history"] = []
            save_elo()
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
        '.': '·'
    }
    
    linhas = []
    linhas.append("  a b c d e f g h")
    linhas.append(" ╔═══════════════╗")
    
    fen_board = board.fen().split()[0]
    ranks = fen_board.split('/')
    
    for i, rank in enumerate(ranks):
        row_str = f"{8 - i}║"
        elements = []
        for char in rank:
            if char.isdigit():
                for _ in range(int(char)):
                    elements.append("·")
            else:
                elements.append(pecas_unicode.get(char, char))
        row_str += " ".join(elements) + f"║{8 - i}"
        linhas.append(row_str)
        
    linhas.append(" ╚═══════════════╝")
    linhas.append("  a b c d e f g h")
    return "```\n" + "\n".join(linhas) + "\n```"

# --- CONSULTA AO MOTOR STOCKFISH (NUVEM LICHESS API) ---
def consultar_stockfish(fen):
    try:
        url = f"https://lichess.org/api/cloud-eval?fen={urllib.parse.quote(fen)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'DischessBot/1.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            pvs = data.get("pvs", [])
            if pvs:
                best_move = pvs[0].get("moves", "").split()[0] if pvs[0].get("moves") else None
                cp = pvs[0].get("cp")
                mate = pvs[0].get("mate")
                return {"best_move": best_move, "cp": cp, "mate": mate}
    except Exception:
        pass
    return None

def classificar_lance_stockfish(board_antes: chess.Board, move: chess.Move):
    fen_antes = board_antes.fen()
    eval_stockfish = consultar_stockfish(fen_antes)

    board_depois = board_antes.copy()
    is_capture = board_antes.is_capture(move)
    board_depois.push(move)

    if board_depois.is_checkmate():
        return "💎 Brilhante!!", "Lance de Mestre que levou ao Xeque-Mate!"

    if eval_stockfish:
        best = eval_stockfish.get("best_move")
        if best and move.uci() == best:
            if is_capture and board_antes.piece_at(move.from_square).piece_type in (chess.BISHOP, chess.KNIGHT, chess.ROOK, chess.QUEEN):
                return "💎 Brilhante!!", f"Lance recomendado pelo Stockfish (`{best}`): Sacrifício tático perfeito!"
            return "🟢 Melhor Lance (Stockfish)", f"A jogada feita (`{move.uci()}`) foi exatamente a recomendação nº 1 do Stockfish!"

    if board_depois.is_check():
        return "🟢 Ótimo Lance", "Xeque preciso mantendo pressão no Rei adversário."

    turn = board_antes.turn
    piece_moved = board_antes.piece_at(move.from_square)
    atritado = board_depois.attackers(not turn, move.to_square)
    defendido = board_depois.attackers(turn, move.to_square)
    val_peca = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}.get(piece_moved.piece_type if piece_moved else 1, 1)

    if atritado and not defendido and val_peca >= 3 and not is_capture:
        return "🔴 Erro Grave (Blunder)", f"Deixou a peça na casa `{chess.square_name(move.to_square)}` pendurada sem proteção!"

    return "📘 Lance Bom", "Desenvolvimento de jogo consistente."

def registrar_historico_partida(white_user, black_user, vencedor_id, eh_empate, motivo, lances_uci):
    w_data = get_user_data(white_user.id)
    b_data = get_user_data(black_user.id)

    id_partida_w = len(w_data["match_history"]) + 1
    id_partida_b = len(b_data["match_history"]) + 1

    if eh_empate:
        res_w, res_b = "Empate", "Empate"
        w_data["draws"] += 1
        b_data["draws"] += 1
    elif vencedor_id == white_user.id:
        res_w, res_b = "Vitória", "Derrota"
        w_data["wins"] += 1
        b_data["losses"] += 1
    else:
        res_w, res_b = "Derrota", "Vitória"
        w_data["losses"] += 1
        b_data["wins"] += 1

    match_info_w = {
        "id": id_partida_w,
        "oponente": black_user.name,
        "oponente_id": black_user.id,
        "resultado": res_w,
        "motivo": motivo,
        "cor": "Brancas",
        "lances": lances_uci
    }

    match_info_b = {
        "id": id_partida_b,
        "oponente": white_user.name,
        "oponente_id": white_user.id,
        "resultado": res_b,
        "motivo": motivo,
        "cor": "Pretas",
        "lances": lances_uci
    }

    w_data["match_history"].append(match_info_w)
    b_data["match_history"].append(match_info_b)
    save_elo()

def jogador_em_jogo_ou_desafio(user_id):
    for g in active_games.values():
        if user_id in (g["white"].id, g["black"].id):
            return True, "em uma partida ativa"
    for c in pending_challenges.values():
        if user_id in (c["challenger"].id, c["opponent"].id):
            return True, "em um desafio pendente"
    return False, ""

# --- NAVEGADOR DE ANÁLISE COM STOCKFISH ---

class MatchAnalysisView(View):
    def __init__(self, match_data, user_name):
        super().__init__(timeout=None)
        self.match_data = match_data
        self.user_name = user_name
        self.lances = match_data.get("lances", [])
        self.index = 0
        
        self.historico_estados = []
        board_temp = chess.Board()
        
        self.historico_estados.append({
            "board": board_temp.copy(),
            "move_txt": "Início do jogo",
            "nota": "🏁 Posição Inicial",
            "explicacao": "As peças foram dispostas nas posições padrão."
        })

        for move_str in self.lances:
            try:
                m = chess.Move.from_uci(move_str)
                nota, explicacao = classificar_lance_stockfish(board_temp, m)
                jogada_txt = f"{'Brancas' if board_temp.turn == chess.WHITE else 'Pretas'}: `{m.uci()}`"
                
                board_temp.push(m)
                self.historico_estados.append({
                    "board": board_temp.copy(),
                    "move_txt": jogada_txt,
                    "nota": nota,
                    "explicacao": explicacao
                })
            except Exception:
                pass

    def gerar_embed(self):
        estado = self.historico_estados[self.index]
        board = estado["board"]
        tabuleiro_txt = desenhar_tabuleiro(board)

        embed = discord.Embed(
            title=f"🦈 Análise Stockfish — Partida {self.match_data['id']}",
            description=f"**Jogador:** `{self.user_name}` | **Oponente:** `{self.match_data['oponente']}`\n**Resultado:** `{self.match_data['resultado']}` *({self.match_data['motivo']})*",
            color=discord.Color.gold()
        )

        embed.add_field(name="📍 Lance Atual", value=f"**{self.index} / {len(self.historico_estados)-1}** — {estado['move_txt']}", inline=False)
        embed.add_field(name="🧠 Análise do Motor", value=f"**{estado['nota']}**\n*{estado['explicacao']}*", inline=False)
        embed.add_field(name="♟️ Tabuleiro", value=tabuleiro_txt, inline=False)

        return embed

    @discord.ui.button(label="⏮️ Início", style=discord.ButtonStyle.secondary)
    async def inicio(self, interaction: discord.Interaction, button: Button):
        self.index = 0
        await interaction.response.edit_message(embed=self.gerar_embed(), view=self)

    @discord.ui.button(label="◀️ Voltar", style=discord.ButtonStyle.primary)
    async def voltar(self, interaction: discord.Interaction, button: Button):
        if self.index > 0:
            self.index -= 1
        await interaction.response.edit_message(embed=self.gerar_embed(), view=self)

    @discord.ui.button(label="Avançar ▶️", style=discord.ButtonStyle.primary)
    async def avancar(self, interaction: discord.Interaction, button: Button):
        if self.index < len(self.historico_estados) - 1:
            self.index += 1
        await interaction.response.edit_message(embed=self.gerar_embed(), view=self)

    @discord.ui.button(label="⏭️ Fim", style=discord.ButtonStyle.secondary)
    async def fim(self, interaction: discord.Interaction, button: Button):
        self.index = len(self.historico_estados) - 1
        await interaction.response.edit_message(embed=self.gerar_embed(), view=self)

# --- PAINEL DE DESAFIO ---

class ChallengeView(View):
    def __init__(self, channel_id, challenger, opponent):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.challenger = challenger
        self.opponent = opponent

    @discord.ui.button(label="Aceitar Desafio", style=discord.ButtonStyle.success, emoji="⚔️")
    async def aceitar(self, interaction: discord.Interaction, button_obj: Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("❌ Apenas o jogador desafiado pode aceitar este convite!", ephemeral=True)
            return

        if self.channel_id not in pending_challenges:
            await interaction.response.send_message("❌ Este convite já expirou ou foi cancelado.", ephemeral=True)
            return

        del pending_challenges[self.channel_id]

        active_games[self.channel_id] = {
            "board": chess.Board(),
            "white": self.challenger,
            "black": self.opponent,
            "draw_offered_by": None,
            "selected_square": None,
            "history_moves": []
        }

        board = active_games[self.channel_id]["board"]
        tabuleiro_txt = desenhar_tabuleiro(board)
        view = ChessBoardView(self.channel_id)

        u1_data = get_user_data(self.challenger.id)
        u2_data = get_user_data(self.opponent.id)

        embed = discord.Embed(
            title="⚔️ Partida de Xadrez Iniciada!",
            description=f"**Brancas:** {self.challenger.mention} ({u1_data['elo']} ELO)\n**Pretas:** {self.opponent.mention} ({u2_data['elo']} ELO)\n\n**Vez de:** {self.challenger.mention} (Brancas)\n{tabuleiro_txt}\nClique na peça que deseja mover:",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Cancelar / Recusar", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancelar(self, interaction: discord.Interaction, button_obj: Button):
        if interaction.user.id not in (self.challenger.id, self.opponent.id):
            await interaction.response.send_message("❌ Não podes intervir neste desafio!", ephemeral=True)
            return

        if self.channel_id in pending_challenges:
            del pending_challenges[self.channel_id]

        msg = f"❌ O desafio de xadrez foi cancelado por {interaction.user.mention}."
        await interaction.response.edit_message(content=msg, embed=None, view=None)

# --- SISTEMA DE BOTÕES DE PEÇAS E MOVIMENTOS ---

class ChessBoardView(View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.montar_botoes()

    def montar_botoes(self):
        self.clear_items()
        if self.channel_id not in active_games:
            return

        game = active_games[self.channel_id]
        board = game["board"]
        selected_sq = game.get("selected_square")

        pecas_unicode = {
            'R': '♖', 'N': '♘', 'B': '♗', 'Q': '♕', 'K': '♔', 'P': '♙',
            'r': '♜', 'n': '♞', 'b': '♝', 'q': '♛', 'k': '♚', 'p': '♟'
        }

        if selected_sq is None:
            movimentos_possiveis = list(board.legal_moves)
            casas_com_origem = set(m.from_square for m in movimentos_possiveis)

            for sq in sorted(casas_com_origem):
                piece = board.piece_at(sq)
                if piece:
                    nome_casa = chess.square_name(sq)
                    simbolo = pecas_unicode.get(piece.symbol(), "")
                    label = f"{simbolo} {nome_casa}"
                    
                    btn = Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"select_{nome_casa}")
                    btn.callback = self.criar_callback_selecao(sq)
                    self.add_item(btn)
        else:
            origem_nome = chess.square_name(selected_sq)
            destinos = [m.to_square for m in board.legal_moves if m.from_square == selected_sq]

            for dest_sq in destinos:
                dest_nome = chess.square_name(dest_sq)
                target_piece = board.piece_at(dest_sq)
                simbolo = f"⚔️ {dest_nome}" if target_piece else dest_nome

                btn = Button(label=simbolo, style=discord.ButtonStyle.success, custom_id=f"dest_{dest_nome}")
                btn.callback = self.criar_callback_movimento(selected_sq, dest_sq)
                self.add_item(btn)

            btn_cancel = Button(label="↩️ Voltar", style=discord.ButtonStyle.secondary, custom_id="cancel")
            btn_cancel.callback = self.callback_cancelar
            self.add_item(btn_cancel)

        btn_empate = Button(label="Empate", style=discord.ButtonStyle.secondary, emoji="🤝", row=4)
        btn_empate.callback = self.callback_empate
        self.add_item(btn_empate)

        btn_desistir = Button(label="Desistir", style=discord.ButtonStyle.danger, emoji="🏳️", row=4)
        btn_desistir.callback = self.callback_desistir
        self.add_item(btn_desistir)

    def criar_callback_selecao(self, square):
        async def callback(interaction: discord.Interaction):
            game = active_games.get(self.channel_id)
            if not game:
                await interaction.response.send_message("Partida não encontrada.", ephemeral=True)
                return

            vez_atual = game["white"] if game["board"].turn == chess.WHITE else game["black"]
            if interaction.user != vez_atual:
                await interaction.response.send_message("Não é a sua vez de jogar!", ephemeral=True)
                return

            game["selected_square"] = square
            self.montar_botoes()
            
            nome_casa = chess.square_name(square)
            proxima_vez = game["white"] if game["board"].turn == chess.WHITE else game["black"]
            tabuleiro_txt = desenhar_tabuleiro(game["board"])

            embed = discord.Embed(
                title="♟️ Partida em Andamento",
                description=f"**Peça selecionada:** `{nome_casa}`\nEscolha para onde deseja mover!\n\n**Vez de:** {proxima_vez.mention}\n{tabuleiro_txt}",
                color=discord.Color.gold()
            )
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    def criar_callback_movimento(self, from_sq, to_sq):
        async def callback(interaction: discord.Interaction):
            game = active_games.get(self.channel_id)
            if not game:
                await interaction.response.send_message("Partida não encontrada.", ephemeral=True)
                return

            vez_atual = game["white"] if game["board"].turn == chess.WHITE else game["black"]
            if interaction.user != vez_atual:
                await interaction.response.send_message("Não é a sua vez de jogar!", ephemeral=True)
                return

            board = game["board"]
            move = chess.Move(from_sq, to_sq)
            
            if board.piece_at(from_sq) and board.piece_at(from_sq).piece_type == chess.PAWN:
                if chess.square_rank(to_sq) in (0, 7):
                    move = chess.Move(from_sq, to_sq, promotion=chess.QUEEN)

            board.push(move)
            game["history_moves"].append(move.uci())
            game["selected_square"] = None
            game["draw_offered_by"] = None

            if board.is_game_over():
                result = board.result()
                white, black = game["white"], game["black"]
                w_data = get_user_data(white.id)
                b_data = get_user_data(black.id)
                w_elo, b_elo = w_data["elo"], b_data["elo"]
                k = 32

                if result == "1-0":
                    w_elo += int(k * (1 - 1/(1 + 10**((b_elo - w_elo)/400))))
                    b_elo += int(k * (0 - 1/(1 + 10**((w_elo - b_elo)/400))))
                    msg_fim = f"🏆 **Fim de Jogo!** As Brancas ({white.mention}) venceram por xeque-mate!"
                    registrar_historico_partida(white, black, white.id, False, "Xeque-Mate", game["history_moves"])
                elif result == "0-1":
                    w_elo += int(k * (0 - 1/(1 + 10**((b_elo - w_elo)/400))))
                    b_elo += int(k * (1 - 1/(1 + 10**((w_elo - b_elo)/400))))
                    msg_fim = f"🏆 **Fim de Jogo!** As Pretas ({black.mention}) venceram por xeque-mate!"
                    registrar_historico_partida(white, black, black.id, False, "Xeque-Mate", game["history_moves"])
                else:
                    msg_fim = "🤝 **Fim de Jogo!** A partida terminou em empate."
                    registrar_historico_partida(white, black, None, True, "Empate", game["history_moves"])

                w_data["elo"] = max(0, w_elo)
                b_data["elo"] = max(0, b_elo)
                w_data["peak_elo"] = max(w_data["peak_elo"], w_data["elo"])
                b_data["peak_elo"] = max(b_data["peak_elo"], b_data["elo"])
                save_elo()

                if interaction.guild:
                    await atualizar_cargo_elo(white, w_data["elo"], interaction.channel)
                    await atualizar_cargo_elo(black, b_data["elo"], interaction.channel)

                del active_games[self.channel_id]
                tabuleiro_txt = desenhar_tabuleiro(board)
                await interaction.response.edit_message(content=f"{msg_fim}\n{tabuleiro_txt}\nNovos ELOs:\n{white.name}: `{w_data['elo']}`\n{black.name}: `{b_data['elo']}`", embed=None, view=None)
                return

            self.montar_botoes()
            proxima_vez = game["white"] if board.turn == chess.WHITE else game["black"]
            tabuleiro_txt = desenhar_tabuleiro(game["board"])

            embed = discord.Embed(
                title="♟️ Partida em Andamento",
                description=f"Movimento efetuado: `{chess.square_name(from_sq)}{chess.square_name(to_sq)}`\n**Vez de:** {proxima_vez.mention}\n{tabuleiro_txt}",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    async def callback_cancelar(self, interaction: discord.Interaction):
        game = active_games.get(self.channel_id)
        if not game:
            return
        
        vez_atual = game["white"] if game["board"].turn == chess.WHITE else game["black"]
        if interaction.user != vez_atual:
            await interaction.response.send_message("Não é a sua vez!", ephemeral=True)
            return

        game["selected_square"] = None
        self.montar_botoes()
        tabuleiro_txt = desenhar_tabuleiro(game["board"])

        embed = discord.Embed(
            title="♟️ Partida em Andamento",
            description=f"Seleção cancelada.\n**Vez de:** {vez_atual.mention}\n{tabuleiro_txt}",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def callback_empate(self, interaction: discord.Interaction):
        game = active_games.get(self.channel_id)
        if not game or interaction.user not in (game["white"], game["black"]):
            await interaction.response.send_message("Não estás nesta partida!", ephemeral=True)
            return

        if game.get("draw_offered_by") is None:
            game["draw_offered_by"] = interaction.user
            await interaction.response.send_message(f"🤝 {interaction.user.mention} propôs um empate! O oponente pode clicar no botão para aceitar.", ephemeral=False)
        elif game["draw_offered_by"] == interaction.user:
            await interaction.response.send_message("Já propuseste um empate. Aguarda o oponente!", ephemeral=True)
        else:
            white, black = game["white"], game["black"]
            registrar_historico_partida(white, black, None, True, "Acordo de Empate", game["history_moves"])
            save_elo()
            del active_games[self.channel_id]
            await interaction.response.edit_message(content=f"🤝 **Empate Aceito!** A partida entre {white.mention} e {black.mention} terminou em empate.", embed=None, view=None)

    async def callback_desistir(self, interaction: discord.Interaction):
        game = active_games.get(self.channel_id)
        if not game or interaction.user not in (game["white"], game["black"]):
            await interaction.response.send_message("Não estás nesta partida!", ephemeral=True)
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
            w_elo += int(k * (0 - 1/(1 + 10**((w_elo - b_elo)/400))))
            b_elo += int(k * (1 - 1/(1 + 10**((w_elo - b_elo)/400))))

        w_data["elo"] = max(0, w_elo)
        b_data["elo"] = max(0, b_elo)
        w_data["peak_elo"] = max(w_data["peak_elo"], w_data["elo"])
        b_data["peak_elo"] = max(b_data["peak_elo"], b_data["elo"])

        registrar_historico_partida(white, black, vencedor.id, False, "Desistência", game["history_moves"])
        save_elo()

        if interaction.guild:
            await atualizar_cargo_elo(white, w_data["elo"], interaction.channel)
            await atualizar_cargo_elo(black, b_data["elo"], interaction.channel)

        del active_games[self.channel_id]
        await interaction.response.edit_message(content=f"🏳️ {perdedor.mention} desistiu da partida!\n🏆 **Vencedor:** {vencedor.mention}\n\nNovos ELOs:\n{white.name}: `{w_data['elo']}`\n{black.name}: `{b_data['elo']}`", embed=None, view=None)

# --- COMANDOS DO BOT ---

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
        await interaction.response.send_message("❌ Não podes jogar contra ti próprio!", ephemeral=True)
        return
    if oponente.bot:
        await interaction.response.send_message("❌ Não podes jogar contra bots do Discord!", ephemeral=True)
        return

    channel_id = interaction.channel.id

    if channel_id in active_games or channel_id in pending_challenges:
        await interaction.response.send_message("❌ Já existe uma partida ou desafio em andamento neste canal!", ephemeral=True)
        return

    em_jogo, motivo = jogador_em_jogo_ou_desafio(interaction.user.id)
    if em_jogo:
        await interaction.response.send_message(f"❌ Tu já estás {motivo}! Termina o desafio/partida antes de criar um novo.", ephemeral=True)
        return

    em_jogo_op, motivo_op = jogador_em_jogo_ou_desafio(oponente.id)
    if em_jogo_op:
        await interaction.response.send_message(f"❌ {oponente.mention} já está {motivo_op} e não pode aceitar novos desafios no momento.", ephemeral=True)
        return

    pending_challenges[channel_id] = {
        "challenger": interaction.user,
        "opponent": oponente
    }

    view = ChallengeView(channel_id, interaction.user, oponente)
    embed = discord.Embed(
        title="⚔️ Desafio de Xadrez!",
        description=f"{interaction.user.mention} desafiou {oponente.mention} para uma partida de xadrez!\n\n{oponente.mention}, clica em **Aceitar Desafio** para começar a jogar.",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="historico", description="Lista o histórico de partidas do jogador.")
async def historico(interaction: discord.Interaction, membro: discord.Member = None):
    target = membro or interaction.user
    data = get_user_data(target.id)
    partidas = data.get("match_history", [])

    if not partidas:
        await interaction.response.send_message(f"📜 **{target.name}** ainda não jogou nenhuma partida registrada.", ephemeral=True)
        return

    linhas = []
    for p in partidas[-10:]:
        emoji_res = "🟢" if p["resultado"] == "Vitória" else ("🔴" if p["resultado"] == "Derrota" else "🟡")
        linhas.append(f"{emoji_res} **Partida {p['id']}**: {p['resultado']} vs `{p['oponente']}` *(Motivo: {p['motivo']})*")

    embed = discord.Embed(
        title=f"📜 Histórico de Partidas — {target.name}",
        description="\n".join(linhas),
        color=discord.Color.blue()
    )
    embed.set_footer(text="Use /analisar <número> para ver a análise do Stockfish!")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="analisar", description="Navegue e veja a análise real do Stockfish lance por lance!")
async def analisar(interaction: discord.Interaction, id_partida: int, membro: discord.Member = None):
    target = membro or interaction.user
    data = get_user_data(target.id)
    partidas = data.get("match_history", [])

    match = next((p for p in partidas if p["id"] == id_partida), None)
    if not match:
        await interaction.response.send_message(f"❌ Partida ID `{id_partida}` não encontrada no histórico de **{target.name}**.", ephemeral=True)
        return

    await interaction.response.defer()

    view = MatchAnalysisView(match, target.name)
    embed = view.gerar_embed()

    await interaction.followup.send(embed=embed, view=view)

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
    guild = interaction.guild
    ranking_data = dict(user_elo)
    
    if not ranking_data and guild:
        for member in guild.members:
            if not member.bot:
                ranking_data[str(member.id)] = {"elo": 100}

    if not ranking_data:
        await interaction.response.send_message("📊 Ainda não há registos de ELO no ranking.", ephemeral=True)
        return

    sorted_users = sorted(
        ranking_data.items(),
        key=lambda item: item[1]["elo"] if isinstance(item[1], dict) else item[1],
        reverse=True
    )
    
    medals = ["🥇", "🥈", "🥉"]
    description = ""
    
    for index, (uid, udata) in enumerate(sorted_users[:10], start=1):
        elo_val = udata["elo"] if isinstance(udata, dict) else udata
        
        user_obj = guild.get_member(int(uid)) if guild else None
        mention = user_obj.mention if user_obj else f"<@{uid}>"
        
        prefix = medals[index - 1] if index <= 3 else f"**{index}.**"
        description += f"{prefix} {mention} — **{elo_val} ELO**\n"

    embed = discord.Embed(
        title="🏆 Ranking Global — Dischess Hub",
        description=description,
        color=discord.Color.gold()
    )
    embed.set_footer(text="Jogue partidas com /desafiar para subir no ranking!")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="resetar_tudo", description="[ADMIN] Reseta todos os ELOs, estatísticas e cargos de patentes.")
async def resetar_tudo(interaction: discord.Interaction):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("❌ Apenas o dono do bot tem permissão para usar este comando!", ephemeral=True)
        return

    global user_elo, pending_challenges, active_games
    user_elo = {}
    pending_challenges = {}
    active_games = {}
    save_elo()

    guild = interaction.guild
    if guild:
        nomes_todas_patentes = [p[2] for p in PATENTES]
        for member in guild.members:
            cargos_remover = [role for role in member.roles if role.name in nomes_todas_patentes]
            if cargos_remover:
                try:
                    await member.remove_roles(*cargos_remover)
                except Exception:
                    pass

    await interaction.response.send_message("🔄 **RESET COMPLETO!** Todos os ELOs, histórico e cargos foram zerados para 100 de ELO.")

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("ERRO: O token do Discord não foi encontrado nas variáveis de ambiente.")
