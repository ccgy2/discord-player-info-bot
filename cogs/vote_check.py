import discord
from discord.ext import commands
from discord.ui import View, Button
import json
import os

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# 투표 데이터 저장
# =========================
votes = {}  # message_id 기준 저장


# =========================
# 투표 View
# =========================
class VoteView(View):
    def __init__(self, title, options, author, guild):
        super().__init__(timeout=None)
        self.title = title
        self.options = options
        self.author = author
        self.guild = guild

        for i, option in enumerate(options):
            self.add_item(VoteButton(i, option))

        self.add_item(CheckButton())
        self.add_item(NonVoterButton())
        self.add_item(CloseButton())


# =========================
# 투표 버튼
# =========================
class VoteButton(Button):
    def __init__(self, index, label):
        super().__init__(label=f"{label} (0명)", style=discord.ButtonStyle.primary)
        self.index = index
        self.option_label = label

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id
        user_id = interaction.user.id

        if msg_id not in votes:
            votes[msg_id] = {
                "options": {},
                "voters": {}
            }

        # 기존 투표 제거
        for opt in votes[msg_id]["options"]:
            if user_id in votes[msg_id]["options"][opt]:
                votes[msg_id]["options"][opt].remove(user_id)

        # 새 투표
        votes[msg_id]["options"].setdefault(self.option_label, []).append(user_id)
        votes[msg_id]["voters"][user_id] = self.option_label

        await update_message(interaction.message)
        await interaction.response.send_message("투표 완료!", ephemeral=True)


# =========================
# 투표 현황 확인 버튼
# =========================
class CheckButton(Button):
    def __init__(self):
        super().__init__(label="투표 확인", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id

        if msg_id not in votes:
            return await interaction.response.send_message("데이터 없음", ephemeral=True)

        text = "📊 투표 현황\n\n"

        for opt, users in votes[msg_id]["options"].items():
            mentions = []
            for uid in users:
                member = interaction.guild.get_member(uid)
                if member:
                    mentions.append(member.mention)

            text += f"**{opt} ({len(users)}명)**\n"
            text += ", ".join(mentions) if mentions else "없음"
            text += "\n\n"

        await interaction.response.send_message(text, ephemeral=True)


# =========================
# 미참여자 버튼
# =========================
class NonVoterButton(Button):
    def __init__(self):
        super().__init__(label="미참여자", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id

        if msg_id not in votes:
            return await interaction.response.send_message("데이터 없음", ephemeral=True)

        all_members = [
            m for m in interaction.guild.members
            if not m.bot
        ]

        voted = set(votes[msg_id]["voters"].keys())

        non_voters = [m.mention for m in all_members if m.id not in voted]

        await interaction.response.send_message(
            f"❌ 미참여자 ({len(non_voters)}명)\n" + ", ".join(non_voters),
            ephemeral=True
        )


# =========================
# 투표 마감
# =========================
class CloseButton(Button):
    def __init__(self):
        super().__init__(label="투표 마감", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.message.author.id:
            return await interaction.response.send_message("작성자만 가능", ephemeral=True)

        for item in self.view.children:
            item.disabled = True

        await interaction.message.edit(view=self.view)
        await interaction.response.send_message("투표 마감됨", ephemeral=True)


# =========================
# 메시지 업데이트 (핵심)
# =========================
async def update_message(message):
    msg_id = message.id

    if msg_id not in votes:
        return

    view = message.components

    new_view = View(timeout=None)

    for item in message.components[0].children:
        if isinstance(item, discord.ui.Button):
            label = item.label.split(" (")[0]

            count = len(votes[msg_id]["options"].get(label, []))

            if item.label.startswith("투표 확인"):
                new_view.add_item(CheckButton())
            elif item.label.startswith("미참여자"):
                new_view.add_item(NonVoterButton())
            elif item.label.startswith("투표 마감"):
                new_view.add_item(CloseButton())
            else:
                btn = VoteButton(0, label)
                btn.label = f"{label} ({count}명)"
                new_view.add_item(btn)

    await message.edit(view=new_view)


# =========================
# 투표 생성 명령어
# =========================
@bot.command()
async def 투표생성(ctx, 제목, *항목):
    if len(항목) < 2:
        return await ctx.send("항목 2개 이상 필요")

    view = VoteView(제목, 항목, ctx.author, ctx.guild)

    msg = await ctx.send(f"📊 **{제목}**", view=view)

    votes[msg.id] = {
        "options": {opt: [] for opt in 항목},
        "voters": {}
    }


# =========================
# 미참여자 명령어 (기존 기능 유지)
# =========================
@bot.command()
async def 미참여자(ctx, message_id: int):
    if message_id not in votes:
        return await ctx.send("해당 투표 없음")

    voted = set(votes[message_id]["voters"].keys())

    non_voters = [
        m.mention for m in ctx.guild.members
        if not m.bot and m.id not in voted
    ]

    await ctx.send(f"❌ 미참여자 ({len(non_voters)}명)\n" + ", ".join(non_voters))


# =========================
# 실행
# =========================
import discord
from discord.ext import commands
from discord.ui import View, Button
import json

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# 투표 데이터 저장
# =========================
votes = {}  # message_id 기준 저장


# =========================
# 투표 View
# =========================
class VoteView(View):
    def __init__(self, title, options, author, guild):
        super().__init__(timeout=None)
        self.title = title
        self.options = options
        self.author = author
        self.guild = guild

        for i, option in enumerate(options):
            self.add_item(VoteButton(i, option))

        self.add_item(CheckButton())
        self.add_item(NonVoterButton())
        self.add_item(CloseButton())


# =========================
# 투표 버튼
# =========================
class VoteButton(Button):
    def __init__(self, index, label):
        super().__init__(label=f"{label} (0명)", style=discord.ButtonStyle.primary)
        self.index = index
        self.option_label = label

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id
        user_id = interaction.user.id

        if msg_id not in votes:
            votes[msg_id] = {
                "options": {},
                "voters": {}
            }

        # 기존 투표 제거
        for opt in votes[msg_id]["options"]:
            if user_id in votes[msg_id]["options"][opt]:
                votes[msg_id]["options"][opt].remove(user_id)

        # 새 투표
        votes[msg_id]["options"].setdefault(self.option_label, []).append(user_id)
        votes[msg_id]["voters"][user_id] = self.option_label

        await update_message(interaction.message)
        await interaction.response.send_message("투표 완료!", ephemeral=True)


# =========================
# 투표 현황 확인 버튼
# =========================
class CheckButton(Button):
    def __init__(self):
        super().__init__(label="투표 확인", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id

        if msg_id not in votes:
            return await interaction.response.send_message("데이터 없음", ephemeral=True)

        text = "📊 투표 현황\n\n"

        for opt, users in votes[msg_id]["options"].items():
            mentions = []
            for uid in users:
                member = interaction.guild.get_member(uid)
                if member:
                    mentions.append(member.mention)

            text += f"**{opt} ({len(users)}명)**\n"
            text += ", ".join(mentions) if mentions else "없음"
            text += "\n\n"

        await interaction.response.send_message(text, ephemeral=True)


# =========================
# 미참여자 버튼
# =========================
class NonVoterButton(Button):
    def __init__(self):
        super().__init__(label="미참여자", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id

        if msg_id not in votes:
            return await interaction.response.send_message("데이터 없음", ephemeral=True)

        all_members = [
            m for m in interaction.guild.members
            if not m.bot
        ]

        voted = set(votes[msg_id]["voters"].keys())

        non_voters = [m.mention for m in all_members if m.id not in voted]

        await interaction.response.send_message(
            f"❌ 미참여자 ({len(non_voters)}명)\n" + ", ".join(non_voters),
            ephemeral=True
        )


# =========================
# 투표 마감
# =========================
class CloseButton(Button):
    def __init__(self):
        super().__init__(label="투표 마감", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.message.author.id:
            return await interaction.response.send_message("작성자만 가능", ephemeral=True)

        for item in self.view.children:
            item.disabled = True

        await interaction.message.edit(view=self.view)
        await interaction.response.send_message("투표 마감됨", ephemeral=True)


# =========================
# 메시지 업데이트 (핵심)
# =========================
async def update_message(message):
    msg_id = message.id

    if msg_id not in votes:
        return

    view = message.components

    new_view = View(timeout=None)

    for item in message.components[0].children:
        if isinstance(item, discord.ui.Button):
            label = item.label.split(" (")[0]

            count = len(votes[msg_id]["options"].get(label, []))

            if item.label.startswith("투표 확인"):
                new_view.add_item(CheckButton())
            elif item.label.startswith("미참여자"):
                new_view.add_item(NonVoterButton())
            elif item.label.startswith("투표 마감"):
                new_view.add_item(CloseButton())
            else:
                btn = VoteButton(0, label)
                btn.label = f"{label} ({count}명)"
                new_view.add_item(btn)

    await message.edit(view=new_view)


# =========================
# 투표 생성 명령어
# =========================
@bot.command()
async def 투표생성(ctx, 제목, *항목):
    if len(항목) < 2:
        return await ctx.send("항목 2개 이상 필요")

    view = VoteView(제목, 항목, ctx.author, ctx.guild)

    msg = await ctx.send(f"📊 **{제목}**", view=view)

    votes[msg.id] = {
        "options": {opt: [] for opt in 항목},
        "voters": {}
    }


# =========================
# 미참여자 명령어 (기존 기능 유지)
# =========================
@bot.command()
async def 미참여자(ctx, message_id: int):
    if message_id not in votes:
        return await ctx.send("해당 투표 없음")

    voted = set(votes[message_id]["voters"].keys())

    non_voters = [
        m.mention for m in ctx.guild.members
        if not m.bot and m.id not in voted
    ]

    await ctx.send(f"❌ 미참여자 ({len(non_voters)}명)\n" + ", ".join(non_voters))


# =========================
# 실행
# =========================
bot.run(os.getenv("DISCORD_TOKEN"))
