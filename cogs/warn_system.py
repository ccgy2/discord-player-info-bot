import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import os
import base64

# =========================================
# FIREBASE INIT
# =========================================

if not firebase_admin._apps:

    FIREBASE_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT")

    decoded = base64.b64decode(FIREBASE_KEY)
    cred = credentials.Certificate(eval(decoded.decode("utf-8")))

    firebase_admin.initialize_app(cred)

db = firestore.client()

# =========================================
# 권한 설정
# =========================================

ALLOWED_ROLE_IDS = [
    123456789012345678
]

ALLOWED_USER_IDS = [
    111111111111111111
]


def has_permission(member):

    if member.id in ALLOWED_USER_IDS:
        return True

    for role in member.roles:
        if role.id in ALLOWED_ROLE_IDS:
            return True

    return False


# =========================================
# CONFIG
# =========================================

def get_config(guild_id):

    doc = db.collection("warn_config").document(str(guild_id)).get()

    if doc.exists:
        return doc.to_dict()

    return {}


def set_config(guild_id, data):

    db.collection("warn_config").document(str(guild_id)).set(data, merge=True)


# =========================================
# HISTORY
# =========================================

def add_history(guild, user, moderator, action, reason):

    db.collection("warning_history").add({
        "guild": guild,
        "user": user,
        "moderator": moderator,
        "action": action,
        "reason": reason,
        "timestamp": datetime.utcnow()
    })


# =========================================
# WARN DATA
# =========================================

def add_warning(guild, user, moderator, reason):

    db.collection("warnings").document(str(guild)).collection(str(user)).add({
        "reason": reason,
        "moderator": moderator,
        "timestamp": datetime.utcnow()
    })

    add_history(guild, user, moderator, "WARN_ADD", reason)


def get_warnings(guild, user):

    docs = db.collection("warnings").document(str(guild)).collection(str(user)).stream()

    result = []

    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        result.append(data)

    return result


def remove_warning(guild, user, warning_id, moderator):

    db.collection("warnings").document(str(guild)).collection(str(user)).document(warning_id).delete()

    add_history(guild, user, moderator, "WARN_REMOVE", warning_id)


def clear_warnings(guild, user, moderator):

    docs = db.collection("warnings").document(str(guild)).collection(str(user)).stream()

    for doc in docs:
        doc.reference.delete()

    add_history(guild, user, moderator, "WARN_RESET", "ALL")


# =========================================
# LOG CHANNEL
# =========================================

async def send_warn_channel(bot, guild_id, embed):

    config = get_config(guild_id)

    if "warn_channel" not in config:
        return

    channel = bot.get_channel(config["warn_channel"])

    if channel:
        await channel.send(embed=embed)


async def send_log(bot, guild_id, embed):

    config = get_config(guild_id)

    if "log_channel" not in config:
        return

    channel = bot.get_channel(config["log_channel"])

    if channel:
        await channel.send(embed=embed)


# =========================================
# 삭제 확인 VIEW
# =========================================

class ConfirmDelete(discord.ui.View):

    def __init__(self, bot, guild_id, user, warn_id, moderator):
        super().__init__(timeout=30)

        self.bot = bot
        self.guild_id = guild_id
        self.user = user
        self.warn_id = warn_id
        self.moderator = moderator

    @discord.ui.button(label="경고 삭제 확인", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):

        remove_warning(self.guild_id, self.user.id, self.warn_id, self.moderator.id)

        embed = discord.Embed(
            title="경고 삭제",
            color=discord.Color.yellow()
        )

        embed.add_field(name="관리자", value=self.moderator.mention)
        embed.add_field(name="대상", value=self.user.mention)

        await send_warn_channel(self.bot, self.guild_id, embed)

        await interaction.response.send_message("경고가 삭제되었습니다.", ephemeral=True)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):

        await interaction.response.send_message("취소되었습니다.", ephemeral=True)


# =========================================
# WARNING DELETE VIEW
# =========================================

class WarningRemoveView(discord.ui.View):

    def __init__(self, bot, guild_id, user, warnings, moderator):
        super().__init__(timeout=None)

        for warn in warnings:

            button = discord.ui.Button(
                label=f"{warn['reason']}",
                style=discord.ButtonStyle.red
            )

            async def callback(interaction: discord.Interaction, warn_id=warn["id"]):

                view = ConfirmDelete(bot, guild_id, user, warn_id, moderator)

                await interaction.response.send_message(
                    "경고를 삭제하시겠습니까?",
                    view=view,
                    ephemeral=True
                )

            button.callback = callback
            self.add_item(button)


# =========================================
# COG
# =========================================

class WarnSystem(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="경고")
    async def warn(self, ctx, action=None, member: discord.Member=None, *, reason=None):

        guild_id = ctx.guild.id

        # 경고 지급
        if action and member and reason and action not in ["확인", "차감", "초기화"]:

            if not has_permission(ctx.author):
                return await ctx.send("권한이 없습니다.")

            add_warning(guild_id, member.id, ctx.author.id, reason)

            warnings = get_warnings(guild_id, member.id)

            embed = discord.Embed(
                title="경고 지급",
                color=discord.Color.red()
            )

            embed.add_field(name="관리자", value=ctx.author.mention)
            embed.add_field(name="대상", value=member.mention)
            embed.add_field(name="사유", value=reason)
            embed.add_field(name="총 경고", value=len(warnings))

            await ctx.send(embed=embed)

            await send_warn_channel(self.bot, guild_id, embed)

            return

        # 경고 확인
        if action == "확인":

            target = ctx.author if member is None else member

            if member and not has_permission(ctx.author):
                return await ctx.send("다른 유저 조회는 관리자만 가능")

            warnings = get_warnings(guild_id, target.id)

            embed = discord.Embed(
                title=f"{target.display_name} 경고 목록",
                color=discord.Color.orange()
            )

            if len(warnings) == 0:
                embed.description = "경고 없음"

            else:

                for i, warn in enumerate(warnings, 1):

                    mod = ctx.guild.get_member(warn["moderator"])

                    embed.add_field(
                        name=f"경고 {i}",
                        value=f"사유: {warn['reason']}\n관리자: {mod.mention if mod else warn['moderator']}",
                        inline=False
                    )

            embed.set_footer(text=f"총 {len(warnings)}개")

            await ctx.send(embed=embed)

        # 경고 차감
        if action == "차감":

            if not has_permission(ctx.author):
                return await ctx.send("권한 없음")

            warnings = get_warnings(guild_id, member.id)

            embed = discord.Embed(
                title=f"{member.display_name} 경고 삭제",
                color=discord.Color.yellow()
            )

            view = WarningRemoveView(self.bot, guild_id, member, warnings, ctx.author)

            await ctx.send(embed=embed, view=view)

        # 경고 초기화
        if action == "초기화":

            if not has_permission(ctx.author):
                return await ctx.send("권한 없음")

            clear_warnings(guild_id, member.id, ctx.author.id)

            embed = discord.Embed(
                title="경고 초기화",
                description=f"{ctx.author.mention} → {member.mention}",
                color=discord.Color.green()
            )

            await ctx.send(embed=embed)

            await send_warn_channel(self.bot, guild_id, embed)

    # =============================
    # 채널 설정
    # =============================

    @commands.command()
    async def 경고채널(self, ctx, channel: discord.TextChannel):

        if not has_permission(ctx.author):
            return await ctx.send("권한 없음")

        set_config(ctx.guild.id, {"warn_channel": channel.id})

        await ctx.send(f"경고 채널 설정: {channel.mention}")

    @commands.command()
    async def 경고로그(self, ctx, channel: discord.TextChannel):

        if not has_permission(ctx.author):
            return await ctx.send("권한 없음")

        set_config(ctx.guild.id, {"log_channel": channel.id})

        await ctx.send(f"로그 채널 설정: {channel.mention}")


# =========================================
# EXTENSION SETUP
# =========================================

async def setup(bot):
    await bot.add_cog(WarnSystem(bot))
