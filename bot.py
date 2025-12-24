# ==============================
# Discord + Firebase Baseball Bot
# STEP 1: Slash only / Grouped Commands / Permission Split
# ==============================

import os
import json
import asyncio
import re
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from urllib.parse import quote_plus

import aiohttp
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

ADMIN_USER_ID = 742989026625060914  # 너의 디스코드 ID
VERIFY_MC = os.getenv("VERIFY_MC", "true").lower() not in ("0", "false", "no", "off")
DEFAULT_PITCH_POWER = int(os.getenv("DEFAULT_PITCH_POWER", "20"))
GUILD_ID = os.getenv("GUILD_ID")

bot = commands.Bot(command_prefix=None, intents=INTENTS)
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
# Embed
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
            await interaction.response.send_message("❌ 선수 없음")
            return
        await interaction.response.send_message(embed=make_player_embed(doc.to_dict()))

    @app_commands.command(name="추가", description="선수 추가")
    async def add(
        self,
        interaction: discord.Interaction,
        닉네임: str,
        팀: Optional[str] = None,
        포지션: Optional[str] = "N/A"
    ):
        data = {
            "nickname": 닉네임,
            "team": normalize_team(팀),
            "position": 포지션,
            "pitch_types": [],
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        player_ref(닉네임).set(data)
        await interaction.response.send_message(f"✅ `{닉네임}` 선수 등록 완료")

    @app_commands.command(name="삭제", description="선수 삭제 (관리자)")
    @app_commands.check(admin_only)
    async def delete(self, interaction: discord.Interaction, 닉네임: str):
        ref = player_ref(닉네임)
        if not ref.get().exists:
            await interaction.response.send_message("❌ 선수 없음")
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
            await interaction.response.send_message("❌ 팀 없음")
            return
        roster = doc.to_dict().get("roster", [])
        await interaction.response.send_message(
            f"**{팀명}** 로스터 ({len(roster)}):\n" + ", ".join(roster)
        )

    @app_commands.command(name="삭제", description="팀 삭제 (관리자)")
    @app_commands.check(admin_only)
    async def delete(self, interaction: discord.Interaction, 팀명: str):
        team_ref(팀명).delete()
        await interaction.response.send_message(f"🗑️ 팀 `{팀명}` 삭제 완료")

# ==============================
# 그룹: 관리
# ==============================
class AdminGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="관리", description="관리자 명령어")

    @app_commands.command(name="청소", description="메시지 삭제")
    @app_commands.check(admin_only)
    async def purge(self, interaction: discord.Interaction, 개수: int):
        deleted = await interaction.channel.purge(limit=min(max(개수,1),1000))
        await interaction.response.send_message(f"🧹 {len(deleted)}개 삭제", ephemeral=True)

    @app_commands.command(name="가져오기파일", description="파일 기반 선수 등록")
    @app_commands.check(admin_only)
    async def import_file(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "⚠️ 파일 업로드는 STEP 2에서 유지됩니다. 현재는 구조만 유지.",
            ephemeral=True
        )

# ==============================
# 등록
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

# ==============================
# 에러
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
    bot.run(token)
