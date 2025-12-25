# ==============================
# Discord + Firebase Baseball Bot
# Slash Only / Grouped Commands / Help Pagination
# ==============================

import os
import json
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands

import firebase_admin
from firebase_admin import credentials, firestore

# ==============================
# 기본 설정
# ==============================
INTENTS = discord.Intents.default()
INTENTS.members = True

ADMIN_USER_ID = 742989026625060914
GUILD_ID = os.getenv("GUILD_ID")

bot = commands.Bot(command_prefix="__disabled__", intents=INTENTS)
SYNCED = False

# ==============================
# Firebase 초기화
# ==============================
def init_firebase():
    if firebase_admin._apps:
        return firestore.client()
    key = os.getenv("FIREBASE_KEY")
    if key:
        cred = credentials.Certificate(json.loads(key))
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()
    return firestore.client()

db = init_firebase()

# ==============================
# 공통 유틸
# ==============================
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def normalize_nick(nick: str) -> str:
    return nick.strip().lower()

def normalize_team(team: str) -> str:
    return " ".join(team.strip().split()) if team else "Free"

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id == ADMIN_USER_ID

# ==============================
# Firestore refs
# ==============================
def player_ref(nick: str):
    return db.collection("players").document(normalize_nick(nick))

def team_ref(team: str):
    return db.collection("teams").document(normalize_team(team))

# ==============================
# Embed
# ==============================
def make_player_embed(d: dict) -> discord.Embed:
    embed = discord.Embed(
        title=d.get("nickname", "-"),
        description=f"[{d.get('team','Free')}]",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="포지션", value=d.get("position","-"), inline=True)
    embed.add_field(
        name="구종",
        value="\n".join(d.get("pitch_types", [])) or "-",
        inline=False
    )
    embed.set_footer(text=f"등록: {d.get('created_at','-')}")
    return embed

# ==============================
# 그룹: 선수 (누구나 사용 가능)
# ==============================
class PlayerGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="선수", description="선수 관련 명령어")

    @app_commands.command(name="정보", description="선수 기본 정보 조회")
    async def info(self, interaction: discord.Interaction, 닉네임: str):
        await interaction.response.defer(ephemeral=False)

        doc = player_ref(닉네임).get()
        if not doc.exists:
            await interaction.followup.send("❌ 선수 없음")
            return

        await interaction.followup.send(embed=make_player_embed(doc.to_dict()))

    @app_commands.command(name="추가", description="선수 추가 (누구나 가능)")
    async def add(self, interaction: discord.Interaction, 닉네임: str):
        await interaction.response.defer(ephemeral=True)

        player_ref(닉네임).set({
            "nickname": 닉네임,
            "team": "Free",
            "position": "N/A",
            "pitch_types": [],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }, merge=True)

        await interaction.followup.send(
            f"✅ `{닉네임}` 선수 등록/갱신 완료",
            ephemeral=True
        )

    @app_commands.command(name="수정", description="선수 정보 수정 (누구나 가능)")
    async def edit(
        self,
        interaction: discord.Interaction,
        닉네임: str,
        포지션: str = None,
        팀명: str = None
    ):
        await interaction.response.defer(ephemeral=True)

        ref = player_ref(닉네임)
        doc = ref.get()
        if not doc.exists:
            await interaction.followup.send("❌ 선수 없음", ephemeral=True)
            return

        updates = {"updated_at": now_iso()}
        if 포지션:
            updates["position"] = 포지션
        if 팀명:
            updates["team"] = normalize_team(팀명)

        ref.update(updates)

        await interaction.followup.send(
            f"✏️ `{닉네임}` 선수 정보 수정 완료",
            ephemeral=True
        )

    @app_commands.command(name="삭제", description="선수 삭제 (관리자)")
    async def delete(self, interaction: discord.Interaction, 닉네임: str):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "⛔ 관리자 전용 명령어입니다.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        ref = player_ref(닉네임)
        if not ref.get().exists:
            await interaction.followup.send("❌ 선수 없음", ephemeral=True)
            return

        ref.delete()
        await interaction.followup.send(
            f"🗑️ `{닉네임}` 삭제 완료",
            ephemeral=True
        )

# ==============================
# 그룹: 팀
# ==============================
class TeamGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="팀", description="팀 관리")

    @app_commands.command(name="생성", description="팀 생성 (누구나 가능)")
    async def create(self, interaction: discord.Interaction, 팀명: str):
        await interaction.response.defer(ephemeral=True)

        team_ref(팀명).set({
            "name": 팀명,
            "created_at": now_iso(),
            "roster": []
        }, merge=True)

        await interaction.followup.send(
            f"✅ 팀 `{팀명}` 생성 완료",
            ephemeral=True
        )

    @app_commands.command(name="조회", description="팀 로스터 조회")
    async def view(self, interaction: discord.Interaction, 팀명: str):
        await interaction.response.defer(ephemeral=False)

        doc = team_ref(팀명).get()
        if not doc.exists:
            await interaction.followup.send("❌ 팀 없음")
            return

        roster = doc.to_dict().get("roster", [])
        await interaction.followup.send(
            f"**{팀명}** 로스터 ({len(roster)}):\n" + ", ".join(roster)
        )

    @app_commands.command(name="삭제", description="팀 삭제 (관리자)")
    async def delete(self, interaction: discord.Interaction, 팀명: str):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "⛔ 관리자 전용 명령어입니다.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        ref = team_ref(팀명)
        if not ref.get().exists:
            await interaction.followup.send(
                f"❌ 팀 `{팀명}` 없음",
                ephemeral=True
            )
            return

        ref.delete()
        await interaction.followup.send(
            f"🗑️ 팀 `{팀명}` 삭제 완료",
            ephemeral=True
        )

# ==============================
# 그룹: 관리
# ==============================
class AdminGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="관리", description="관리자 명령어")

    @app_commands.command(name="청소", description="메시지 삭제 (관리자)")
    async def purge(self, interaction: discord.Interaction, 개수: int):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "⛔ 관리자 전용 명령어입니다.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        limit = max(1, min(1000, 개수))
        deleted = await interaction.channel.purge(limit=limit)

        await interaction.followup.send(
            f"🧹 삭제 완료: {len(deleted)}개",
            ephemeral=True
        )

# ==============================
# /도움 페이지
# ==============================
HELP_PAGES = [
    ("📘 선수 명령어", "`/선수 정보`\n`/선수 추가`\n`/선수 수정`\n`/선수 삭제(관리자)`"),
    ("📕 팀 명령어", "`/팀 생성`\n`/팀 조회`\n`/팀 삭제(관리자)`"),
    ("🛠 관리 명령어", "`/관리 청소`"),
]

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.page = 0

    def embed(self):
        title, desc = HELP_PAGES[self.page]
        e = discord.Embed(title=title, description=desc, color=discord.Color.green())
        e.set_footer(text=f"{self.page+1}/{len(HELP_PAGES)}")
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = (self.page - 1) % len(HELP_PAGES)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = (self.page + 1) % len(HELP_PAGES)
        await interaction.response.edit_message(embed=self.embed(), view=self)

@bot.tree.command(name="도움", description="명령어 도움말")
async def help_cmd(interaction: discord.Interaction):
    view = HelpView()
    await interaction.response.send_message(
        embed=view.embed(),
        view=view,
        ephemeral=True
    )

# ==============================
# 그룹 등록
# ==============================
bot.tree.add_command(PlayerGroup())
bot.tree.add_command(TeamGroup())
bot.tree.add_command(AdminGroup())

# ==============================
# on_ready
# ==============================
@bot.event
async def on_ready():
    global SYNCED
    if SYNCED:
        return

    if GUILD_ID:
        await bot.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
    else:
        await bot.tree.sync()

    SYNCED = True
    print("✅ Slash 명령어 동기화 완료")
    print("등록된 명령어:", [c.name for c in bot.tree.get_commands()])

# ==============================
# 실행
# ==============================
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN 환경변수가 없습니다.")
    bot.run(token)
