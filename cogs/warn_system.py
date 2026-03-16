import discord
from discord.ext import commands
from discord.ui import View, Button
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import uuid

# Firebase 초기화
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# 사용 권한 설정
ALLOWED_ROLE_IDS = [
    1468993487654355046  # 경고 관리 역할
]

ALLOWED_USER_IDS = [
    742989026625060914  # 관리자 ID
]


def has_permission(member: discord.Member):

    if member.id in ALLOWED_USER_IDS:
        return True

    for role in member.roles:
        if role.id in ALLOWED_ROLE_IDS:
            return True

    return False


class WarnSystem(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.warn_channel = None
        self.log_channel = None

    @commands.command(name="경고")
    async def warn(self, ctx, member: discord.Member = None, *, reason=None):

        if member is None:
            await ctx.send("❌ 유저를 멘션해주세요.")
            return

        if reason is None:
            await ctx.send("❌ 경고 사유를 입력해주세요.")
            return

        if not has_permission(ctx.author):
            await ctx.send("❌ 권한 없음")
            return

        warn_id = str(uuid.uuid4())

        data = {
            "user_id": member.id,
            "user_name": str(member),
            "admin_id": ctx.author.id,
            "admin_name": str(ctx.author),
            "reason": reason,
            "time": datetime.utcnow(),
            "guild_id": ctx.guild.id
        }

        db.collection("warnings").document(warn_id).set(data)

        user_warns = db.collection("warnings").where("user_id", "==", member.id).stream()
        count = len(list(user_warns))

        embed = discord.Embed(
            title="⚠️ 경고 지급",
            color=0xff0000
        )

        embed.add_field(name="대상", value=member.mention)
        embed.add_field(name="관리자", value=ctx.author.mention)
        embed.add_field(name="경고 수", value=str(count))
        embed.add_field(name="사유", value=reason)

        await ctx.send(embed=embed)

        if self.warn_channel:
            ch = ctx.guild.get_channel(self.warn_channel)
            if ch:
                await ch.send(embed=embed)

        if self.log_channel:
            ch = ctx.guild.get_channel(self.log_channel)
            if ch:
                await ch.send(
                    f"[WARN LOG]\n관리자:{ctx.author}\n대상:{member}\n사유:{reason}\nID:{warn_id}"
                )

    @commands.command(name="경고확인")
    async def warn_check(self, ctx, member: discord.Member = None):

        if member is None:
            member = ctx.author
        else:
            if not has_permission(ctx.author):
                await ctx.send("❌ 다른 사람 경고 확인 권한 없음")
                return

        warns = db.collection("warnings").where("user_id", "==", member.id).stream()

        warns = list(warns)

        if len(warns) == 0:
            await ctx.send("경고가 없습니다.")
            return

        embed = discord.Embed(
            title=f"{member} 경고 목록",
            color=0xffcc00
        )

        for w in warns:
            d = w.to_dict()

            embed.add_field(
                name="경고",
                value=f"사유: {d['reason']}\n관리자: {d['admin_name']}",
                inline=False
            )

        embed.set_footer(text=f"총 경고 {len(warns)}개")

        await ctx.send(embed=embed)

    @commands.command(name="경고초기화")
    async def warn_reset(self, ctx, member: discord.Member):

        if not has_permission(ctx.author):
            await ctx.send("❌ 권한 없음")
            return

        warns = db.collection("warnings").where("user_id", "==", member.id).stream()

        for w in warns:
            w.reference.delete()

        embed = discord.Embed(
            title="⚠️ 경고 초기화",
            description=f"{member.mention} 의 경고가 모두 삭제되었습니다.",
            color=0x00ff00
        )

        await ctx.send(embed=embed)

    @commands.command(name="경고채널")
    async def set_warn_channel(self, ctx, channel: discord.TextChannel):

        if not has_permission(ctx.author):
            await ctx.send("❌ 권한 없음")
            return

        self.warn_channel = channel.id
        await ctx.send(f"경고 채널 설정됨: {channel.mention}")

    @commands.command(name="경고로그")
    async def set_log_channel(self, ctx, channel: discord.TextChannel):

        if not has_permission(ctx.author):
            await ctx.send("❌ 권한 없음")
            return

        self.log_channel = channel.id
        await ctx.send(f"로그 채널 설정됨: {channel.mention}")


async def setup(bot):
    await bot.add_cog(WarnSystem(bot))
