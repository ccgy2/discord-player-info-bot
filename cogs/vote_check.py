import discord
from discord.ext import commands
from discord.ui import View, Button
import firebase_admin
from firebase_admin import credentials, db
import os
import json

# =========================
# 🔥 Firebase 초기화 (Railway용)
# =========================
if not firebase_admin._apps:
    firebase_json = json.loads(os.environ["FIREBASE_KEY"])
    cred = credentials.Certificate(firebase_json)

    firebase_admin.initialize_app(cred, {
        'databaseURL': os.environ["FIREBASE_DB_URL"]
    })


# =========================
# 🔹 Embed 생성
# =========================
def create_poll_embed(guild, data):
    embed = discord.Embed(title=f"📊 {data['title']}", color=0x2ecc71)

    for opt in data["options"]:
        users = data["votes"].get(opt, [])
        names = []

        for uid in users:
            member = guild.get_member(int(uid))
            if member:
                names.append(member.display_name)

        value = f"👥 {len(users)}명\n"
        value += "\n".join(names) if names else "없음"

        embed.add_field(name=opt, value=value, inline=False)

    return embed


# =========================
# 🔹 투표 버튼
# =========================
class VoteButton(Button):
    def __init__(self, option):
        super().__init__(label=option, style=discord.ButtonStyle.primary)
        self.option = option

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        poll_id = str(interaction.message.id)

        ref = db.reference(f"polls/{poll_id}")
        data = ref.get()

        votes = data.get("votes", {})

        # 기존 투표 제거
        for opt in votes:
            if user_id in votes[opt]:
                votes[opt].remove(user_id)

        # 새 투표
        votes.setdefault(self.option, []).append(user_id)

        ref.update({"votes": votes})

        # 🔥 실시간 업데이트
        new_data = ref.get()
        embed = create_poll_embed(interaction.guild, new_data)
        await interaction.message.edit(embed=embed)

        await interaction.response.send_message("투표 완료", ephemeral=True)


# =========================
# 🔹 미참여 버튼
# =========================
class NotVotedButton(Button):
    def __init__(self):
        super().__init__(label="❌ 미참여자", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        poll_id = str(interaction.message.id)
        ref = db.reference(f"polls/{poll_id}")
        data = ref.get()

        voters = set()
        for opt in data["votes"]:
            for uid in data["votes"][opt]:
                voters.add(uid)

        members = [m for m in interaction.guild.members if not m.bot]
        not_voted = [m for m in members if str(m.id) not in voters]

        text = "\n".join([m.mention for m in not_voted]) or "없음"
        await interaction.response.send_message(text, ephemeral=True)


# =========================
# 🔹 View
# =========================
class VoteView(View):
    def __init__(self, bot, poll_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.poll_id = poll_id

        ref = db.reference(f"polls/{poll_id}")
        data = ref.get()

        for opt in data["options"]:
            self.add_item(VoteButton(opt))

        self.add_item(NotVotedButton())


# =========================
# 🔹 Cog
# =========================
class VoteSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def 투표생성(self, ctx, title, *options):
        if len(options) < 2:
            return await ctx.send("❌ 선택지 2개 이상 필요")

        msg = await ctx.send("📊 생성중...")

        ref = db.reference(f"polls/{msg.id}")
        ref.set({
            "title": title,
            "options": list(options),
            "votes": {}
        })

        data = ref.get()
        embed = create_poll_embed(ctx.guild, data)

        await msg.edit(embed=embed, view=VoteView(self.bot, msg.id))


async def setup(bot):
    await bot.add_cog(VoteSystem(bot))
