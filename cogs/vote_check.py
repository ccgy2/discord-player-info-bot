import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore

# Firebase 초기화
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()


EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]


class VoteButton(discord.ui.Button):
    def __init__(self, label, emoji, index, vote_id):
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary)
        self.index = index
        self.vote_id = vote_id

    async def callback(self, interaction: discord.Interaction):

        user_id = str(interaction.user.id)

        vote_ref = db.collection("votes").document(self.vote_id)
        vote_data = vote_ref.get().to_dict()

        votes = vote_data.get("votes", {})

        # 기존 투표 제거 (중복 방지)
        for key in votes:
            if user_id in votes[key]:
                votes[key].remove(user_id)

        # 새 투표 추가
        if str(self.index) not in votes:
            votes[str(self.index)] = []

        votes[str(self.index)].append(user_id)

        vote_ref.update({"votes": votes})

        await interaction.response.send_message("투표 완료!", ephemeral=True)


class VoteView(discord.ui.View):
    def __init__(self, options, vote_id):
        super().__init__(timeout=None)

        for i, option in enumerate(options):
            self.add_item(VoteButton(option, EMOJIS[i], i, vote_id))


class VoteSystem(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.dm_enabled = True

    @commands.command(name="투표생성")
    @commands.has_permissions(administrator=True)
    async def create_vote(self, ctx, title, *options):

        if len(options) < 2:
            await ctx.send("항목은 최소 2개 필요")
            return

        vote_ref = db.collection("votes").document()
        vote_id = vote_ref.id

        vote_ref.set({
            "title": title,
            "options": options,
            "votes": {},
            "message_id": None,
            "channel_id": ctx.channel.id,
            "guild_id": ctx.guild.id
        })

        embed = discord.Embed(title=f"📊 {title}", color=0x00ff00)

        for i, opt in enumerate(options):
            embed.add_field(name=f"{EMOJIS[i]} {opt}", value="0명", inline=False)

        view = VoteView(options, vote_id)

        msg = await ctx.send(embed=embed, view=view)

        vote_ref.update({"message_id": msg.id})

    @commands.command(name="투표분석")
    @commands.has_permissions(administrator=True)
    async def analyze_vote(self, ctx, message_id: int, role: discord.Role, excluded_role: discord.Role = None):

        votes_ref = db.collection("votes").where("message_id", "==", message_id).stream()

        vote_data = None

        for doc in votes_ref:
            vote_data = doc.to_dict()

        if not vote_data:
            await ctx.send("투표 찾을 수 없음")
            return

        votes = vote_data.get("votes", {})
        options = vote_data.get("options", [])

        guild = ctx.guild

        members = []

        for m in role.members:

            if m.bot:
                continue

            if excluded_role and excluded_role in m.roles:
                continue

            members.append(m)

        voters = set()

        result = "📊 **투표 결과**\n\n"

        for idx, opt in enumerate(options):

            user_ids = votes.get(str(idx), [])

            result += f"{EMOJIS[idx]} {opt} ({len(user_ids)}명)\n"

            for uid in user_ids:
                member = guild.get_member(int(uid))
                if member:
                    result += f"{member.mention}\n"
                    voters.add(int(uid))

            result += "\n"

        not_voted = []

        for m in members:
            if m.id not in voters:
                not_voted.append(m)

        total = len(members)
        voted_count = total - len(not_voted)

        rate = 0
        if total > 0:
            rate = (voted_count / total) * 100

        if not_voted:
            result += f"❌ 미참여 ({len(not_voted)}명)\n"
            for m in not_voted:
                result += f"{m.mention}\n"

        result += f"\n참여율: {rate:.2f}%"

        await ctx.send(result)

        # 미참여 누적
        for m in not_voted:

            ref = db.collection("vote_users").document(str(m.id))
            doc = ref.get()

            if doc.exists:
                count = doc.to_dict().get("miss_count", 0) + 1
            else:
                count = 1

            ref.set({
                "miss_count": count,
                "name": str(m)
            })

        # DM
        if self.dm_enabled:
            for m in not_voted:
                try:
                    await m.send(f"{guild.name} 투표 미참여")
                except:
                    pass

    @commands.command(name="투표DM켜기")
    async def dm_on(self, ctx):
        self.dm_enabled = True
        await ctx.send("DM ON")

    @commands.command(name="투표DM끄기")
    async def dm_off(self, ctx):
        self.dm_enabled = False
        await ctx.send("DM OFF")


async def setup(bot):
    await bot.add_cog(VoteSystem(bot))
