import discord
from discord.ext import commands
from discord.ui import View, Button
import firebase_admin
from firebase_admin import credentials, db

# Firebase 초기화
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://YOUR_DB_URL.firebaseio.com/'
    })


# =========================
# 🔹 Embed 생성 (실시간 표시)
# =========================
def create_poll_embed(guild, data):
    embed = discord.Embed(
        title=f"📊 {data['title']}",
        color=0x2ecc71
    )

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

    if data.get("closed"):
        embed.set_footer(text="🔒 마감된 투표")

    return embed


# =========================
# 🔹 버튼
# =========================
class VoteButton(Button):
    def __init__(self, option):
        super().__init__(label=option, style=discord.ButtonStyle.primary)
        self.option = option

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        poll_id = interaction.message.id

        ref = db.reference(f"polls/{poll_id}")
        data = ref.get()

        if data["closed"]:
            return await interaction.response.send_message("❌ 마감됨", ephemeral=True)

        votes = data.get("votes", {})

        # 기존 투표 제거
        for opt in votes:
            if user_id in votes[opt]:
                votes[opt].remove(user_id)

        # 새 투표
        votes.setdefault(self.option, []).append(user_id)

        ref.update({"votes": votes})

        # 🔥 실시간 반영
        new_data = ref.get()
        embed = create_poll_embed(interaction.guild, new_data)
        await interaction.message.edit(embed=embed)

        await interaction.response.send_message(f"✅ {self.option} 선택됨", ephemeral=True)


# =========================
# 🔹 View
# =========================
class VoteView(View):
    def __init__(self, bot, poll_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.poll_id = poll_id
        self.load_buttons()

    def load_buttons(self):
        ref = db.reference(f"polls/{self.poll_id}")
        data = ref.get()

        if not data:
            return

        for opt in data["options"]:
            self.add_item(VoteButton(opt))


# =========================
# 🔹 Cog
# =========================
class VoteSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.auto_dm = True

    # =========================
    # 투표 생성
    # =========================
    @commands.command()
    async def 투표생성(self, ctx, title, *options):
        if len(options) < 2:
            return await ctx.send("❌ 선택지 2개 이상 필요")

        msg = await ctx.send("📊 투표 생성 중...")

        ref = db.reference(f"polls/{msg.id}")
        ref.set({
            "title": title,
            "options": list(options),
            "votes": {},
            "closed": False
        })

        data = ref.get()
        embed = create_poll_embed(ctx.guild, data)

        await msg.edit(embed=embed, view=VoteView(self.bot, msg.id))

    # =========================
    # 투표 마감
    # =========================
    @commands.command()
    async def 투표마감(self, ctx, message_id: int):
        ref = db.reference(f"polls/{message_id}")
        data = ref.get()

        if not data:
            return await ctx.send("❌ 없음")

        ref.update({"closed": True})

        msg = await ctx.channel.fetch_message(message_id)
        new_data = ref.get()
        embed = create_poll_embed(ctx.guild, new_data)

        await msg.edit(embed=embed)
        await ctx.send("🔒 마감 완료")

    # =========================
    # 🔥 투표 확인 (미참여자)
    # =========================
    @commands.command()
    async def 투표확인(self, ctx, role: discord.Role, channel: discord.TextChannel, *message_ids):

        members = [m for m in role.members if not m.bot]

        # 제외 역할
        exclude_roles = db.reference("settings/exclude_roles").get() or []

        filtered = []
        for m in members:
            if any(r.id in exclude_roles for r in m.roles):
                continue
            filtered.append(m)

        all_voters = set()

        for msg_id in message_ids:
            ref = db.reference(f"polls/{msg_id}")
            data = ref.get()

            if not data:
                continue

            for opt in data["votes"]:
                for uid in data["votes"][opt]:
                    all_voters.add(uid)

        not_voted = [m for m in filtered if str(m.id) not in all_voters]

        rate = (len(all_voters) / len(filtered)) * 100 if filtered else 0

        embed = discord.Embed(title="📊 투표 분석", color=0xe74c3c)
        embed.add_field(name="참여율", value=f"{rate:.1f}%")
        embed.add_field(name="미참여", value=f"{len(not_voted)}명")

        await ctx.send(embed=embed)

        if not_voted:
            text = "\n".join([m.mention for m in not_voted])
            await ctx.send(f"❌ 미참여자:\n{text}")

        # 🔥 누적 + DM
        for m in not_voted:
            ref = db.reference(f"users/{m.id}")
            data = ref.get() or {}
            count = data.get("missed", 0) + 1
            ref.update({"missed": count})

            if self.auto_dm:
                try:
                    await m.send(f"⚠️ 투표 미참여 {count}회")
                except:
                    pass

    # =========================
    # 자동 DM
    # =========================
    @commands.command()
    async def 투표DM(self, ctx, mode: str):
        self.auto_dm = (mode == "on")
        await ctx.send(f"자동 DM: {'ON' if self.auto_dm else 'OFF'}")

    # =========================
    # 제외 역할
    # =========================
    @commands.command()
    async def 제외역할추가(self, ctx, role: discord.Role):
        ref = db.reference("settings/exclude_roles")
        data = ref.get() or []

        if role.id not in data:
            data.append(role.id)
            ref.set(data)

        await ctx.send(f"✅ 제외 역할 추가: {role.name}")

    # =========================
    # 상세 보기
    # =========================
    @commands.command()
    async def 투표상세(self, ctx, message_id: int):
        ref = db.reference(f"polls/{message_id}")
        data = ref.get()

        if not data:
            return await ctx.send("❌ 없음")

        text = ""

        for opt in data["options"]:
            users = data["votes"].get(opt, [])
            mentions = []

            for uid in users:
                m = ctx.guild.get_member(int(uid))
                if m:
                    mentions.append(m.mention)

            text += f"\n**{opt} ({len(users)}명)**\n"
            text += "\n".join(mentions) if mentions else "없음"
            text += "\n"

        await ctx.send(text)


async def setup(bot):
    await bot.add_cog(VoteSystem(bot))
