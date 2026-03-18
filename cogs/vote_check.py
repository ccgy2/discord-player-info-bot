import discord
from discord.ext import commands
from discord.ui import View, Button

votes = {}

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


class VoteButton(Button):
    def __init__(self, index, label):
        super().__init__(label=f"{label} (0명)", style=discord.ButtonStyle.primary)
        self.index = index
        self.option_label = label

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id
        user_id = interaction.user.id

        if msg_id not in votes:
            votes[msg_id] = {"options": {}, "voters": {}}

        for opt in votes[msg_id]["options"]:
            if user_id in votes[msg_id]["options"][opt]:
                votes[msg_id]["options"][opt].remove(user_id)

        votes[msg_id]["options"].setdefault(self.option_label, []).append(user_id)
        votes[msg_id]["voters"][user_id] = self.option_label

        await update_message(interaction.message)
        await interaction.response.send_message("투표 완료!", ephemeral=True)


class CheckButton(Button):
    def __init__(self):
        super().__init__(label="투표 확인", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id

        if msg_id not in votes:
            return await interaction.response.send_message("데이터 없음", ephemeral=True)

        text = "📊 투표 현황\n\n"

        for opt, users in votes[msg_id]["options"].items():
            mentions = [
                interaction.guild.get_member(uid).mention
                for uid in users
                if interaction.guild.get_member(uid)
            ]

            text += f"**{opt} ({len(users)}명)**\n"
            text += ", ".join(mentions) if mentions else "없음"
            text += "\n\n"

        await interaction.response.send_message(text, ephemeral=True)


class NonVoterButton(Button):
    def __init__(self):
        super().__init__(label="미참여자", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id

        if msg_id not in votes:
            return await interaction.response.send_message("데이터 없음", ephemeral=True)

        all_members = [m for m in interaction.guild.members if not m.bot]
        voted = set(votes[msg_id]["voters"].keys())

        non_voters = [m.mention for m in all_members if m.id not in voted]

        await interaction.response.send_message(
            f"❌ 미참여자 ({len(non_voters)}명)\n" + ", ".join(non_voters),
            ephemeral=True
        )


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


async def update_message(message):
    msg_id = message.id

    if msg_id not in votes:
        return

    new_view = View(timeout=None)

    for item in message.components[0].children:
        label = item.label.split(" (")[0]
        count = len(votes[msg_id]["options"].get(label, []))

        if "투표 확인" in item.label:
            new_view.add_item(CheckButton())
        elif "미참여자" in item.label:
            new_view.add_item(NonVoterButton())
        elif "투표 마감" in item.label:
            new_view.add_item(CloseButton())
        else:
            btn = VoteButton(0, label)
            btn.label = f"{label} ({count}명)"
            new_view.add_item(btn)

    await message.edit(view=new_view)


class VoteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def 투표생성(self, ctx, 제목, *항목):
        if len(항목) < 2:
            return await ctx.send("항목 2개 이상 필요")

        view = VoteView(제목, 항목, ctx.author, ctx.guild)
        msg = await ctx.send(f"📊 **{제목}**", view=view)

        votes[msg.id] = {
            "options": {opt: [] for opt in 항목},
            "voters": {}
        }

    @commands.command()
    async def 미참여자(self, ctx, message_id: int):
        if message_id not in votes:
            return await ctx.send("해당 투표 없음")

        voted = set(votes[message_id]["voters"].keys())

        non_voters = [
            m.mention for m in ctx.guild.members
            if not m.bot and m.id not in voted
        ]

        await ctx.send(f"❌ 미참여자 ({len(non_voters)}명)\n" + ", ".join(non_voters))


async def setup(bot):
    await bot.add_cog(VoteCog(bot))
