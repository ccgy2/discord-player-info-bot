# ==============================
# Discord + Firebase Baseball Bot
# STEP 2: Slash only / Grouped Commands / Help Pagination
# ==============================

import os
import json
import asyncio
from datetime import datetime, timezone
from typing import Optional, List

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
    if interaction.user.id == ADMIN_USER_ID:
        return True
    if interaction.guild and interaction.user.guild_permissions.administrator:
        return True
    return False

async def admin_only(interaction: discord.Interaction):
    if not is_admin(interaction):
        raise app_commands.CheckFailure("관리자 전용 명령어입니다.")

# ==============================
# Firestore refs
# ==============================
def player_ref(nick: str):
    return db.collection("players").document(normalize_nick(nick))

def team_ref(team: str):
    return db.collection("teams").document(normalize_team(team))

# ==============================
# Embed (선수)
# ==============================
def make_player_embed(d: dict) -> discord.Embed:
    embed = discord.Embed(
        title=d.get("nickname", "-"),
        description=f"[{d.get('team','Free')}] {d.get('form','')}",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="포지션", value=d.get("position","-"), inline=True)
    embed.add_field(name="구종", value="\n".join(d.get("pitch_types", [])) or "-", inline=False)
    embed.set_footer(text=f"등록: {d.get('created_at','-')}")
    return embed

# ==============================
# 그룹: 선수
# ==============================
class PlayerGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="선수", description="선수 관련 명령어")

    @app_commands.command(name="정보", description="선수 기본 정보 조회")
    async def info(self, interaction: discord.Interaction, 닉네임: str):
        doc = player_ref(닉네임).get()
        if not doc.exists:
            await interaction.response.send_message("❌ 선수 없음", ephemeral=True)
            return
        await interaction.response.send_message(embed=make_player_embed(doc.to_dict()))

    @app_commands.command(name="추가", description="선수 추가")
    async def add(self, interaction: discord.Interaction, 닉네임: str):
        player_ref(닉네임).set({
            "nickname": 닉네임,
            "team": "Free",
            "position": "N/A",
            "pitch_types": [],
            "created_at": now_iso(),
            "updated_at": now_iso()
        })
        await interaction.response.send_message(f"✅ `{닉네임}` 선수 등록 완료")

    @app_commands.command(name="삭제", description="선수 삭제 (관리자)")
    @app_commands.check(admin_only)
    async def delete(self, interaction: discord.Interaction, 닉네임: str):
        ref = player_ref(닉네임)
        if not ref.get().exists:
            await interaction.response.send_message("❌ 선수 없음", ephemeral=True)
            return
        ref.delete()
        await interaction.response.send_message(f"🗑️ `{닉네임}` 삭제 완료")

# ==============================
# 그룹: 팀
# ==============================
class TeamGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="팀", description="팀 관리")

    @app_commands.command(name="생성", description="팀 생성")
    async def create(self, interaction: discord.Interaction, 팀명: str):
        team_ref(팀명).set({"name": 팀명, "created_at": now_iso(), "roster": []})
        await interaction.response.send_message(f"✅ 팀 `{팀명}` 생성")

    @app_commands.command(name="조회", description="팀 로스터 조회")
    async def view(self, interaction: discord.Interaction, 팀명: str):
        doc = team_ref(팀명).get()
        if not doc.exists:
            await interaction.response.send_message("❌ 팀 없음", ephemeral=True)
            return
        roster = doc.to_dict().get("roster", [])
        await interaction.response.send_message(
            f"**{팀명}** 로스터 ({len(roster)}):\n" + ", ".join(roster)
        )

    @app_commands.command(name="삭제", description="팀 삭제 (관리자)")
    async def delete(self, interaction: discord.Interaction, 팀명: str):
    # 🔐 권한 체크
    if not is_admin(interaction):
        await interaction.response.send_message(
            "⛔ 이 명령어는 관리자만 사용할 수 있습니다.",
            ephemeral=True
        )
        return

    # ⏳ 반드시 먼저 defer
    await interaction.response.defer(ephemeral=True)

    ref = team_ref(팀명)
    if not ref.get().exists:
        await interaction.followup.send(
            f"❌ 팀 `{팀명}` 이(가) 존재하지 않습니다.",
            ephemeral=True
        )
        return

    try:
        ref.delete()
        await interaction.followup.send(
            f"🗑️ 팀 `{팀명}` 삭제 완료",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ 팀 삭제 실패: {e}",
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
    # 🔐 권한 체크
    if not is_admin(interaction):
        await interaction.response.send_message(
            "⛔ 이 명령어는 관리자만 사용할 수 있습니다.",
            ephemeral=True
        )
        return

    # ⏳ 먼저 defer (이거 없으면 무조건 타임아웃)
    await interaction.response.defer(ephemeral=True)

    try:
        limit = max(1, min(1000, 개수))
        deleted = await interaction.channel.purge(limit=limit)
        await interaction.followup.send(
            f"🧹 삭제 완료: {len(deleted)}개 메시지",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ 청소 실패: {e}",
            ephemeral=True
        )

# ==============================
# /도움 페이지 View
# ==============================
HELP_PAGES = [
    ("📘 선수 명령어", 
     "`/선수 정보`\n`/선수 추가`\n`/선수 삭제`"),
    ("📕 팀 명령어", 
     "`/팀 생성`\n`/팀 조회`\n`/팀 삭제`"),
    ("📗 기록 명령어", 
     "`/기록 추가타자`\n`/기록 추가투수`\n`/기록 보기`\n`/기록 리셋`"),
    ("📙 이적 명령어", 
     "`/이적 이적`\n`/이적 영입`\n`/이적 트레이드`\n`/이적 방출`\n`/이적 웨이버`"),
    ("🛠 관리 명령어", 
     "`/관리 청소`\n`/관리 가져오기파일`")
]

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.page = 0

    def make_embed(self):
        title, desc = HELP_PAGES[self.page]
        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.green()
        )
        embed.set_footer(text=f"페이지 {self.page + 1}/{len(HELP_PAGES)}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = (self.page - 1) % len(HELP_PAGES)
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = (self.page + 1) % len(HELP_PAGES)
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

# ==============================
# /도움 명령어
# ==============================
@bot.tree.command(name="도움", description="명령어 도움말 보기")
async def slash_help(interaction: discord.Interaction):
    view = HelpView()
    await interaction.response.send_message(
        embed=view.make_embed(),
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
# 에러 처리
# ==============================
@bot.event
async def on_app_command_error(interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("⛔ 권한이 없습니다.", ephemeral=True)
    else:
        await interaction.response.send_message(f"오류: {error}", ephemeral=True)

# ==============================
# 실행
# ==============================
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN 환경변수가 없습니다.")
    bot.run(token)


