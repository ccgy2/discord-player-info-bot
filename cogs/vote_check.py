import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore


# Firebase 초기화
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()


class VoteView(discord.ui.View):

    def __init__(self, cog, ctx, role, channel, message_ids, excluded_roles):
        super().__init__(timeout=None)
        self.cog = cog
        self.ctx = ctx
        self.role = role
        self.channel = channel
        self.message_ids = message_ids
        self.excluded_roles = excluded_roles

    @discord.ui.button(label="🔄 재검사", style=discord.ButtonStyle.primary)
    async def recheck(self, interaction: discord.Interaction, button: discord.ui.Button):

        await interaction.response.defer()

        result = await self.cog.run_vote_check(
            interaction.guild,
            self.role,
            self.channel,
            self.message_ids,
            self.excluded_roles
        )

        await interaction.followup.send(result)

    @discord.ui.button(label="📩 미참여자 DM", style=discord.ButtonStyle.success)
    async def send_dm(self, interaction: discord.Interaction, button: discord.ui.Button):

        await interaction.response.defer()

        result = await self.cog.run_vote_check(
            interaction.guild,
            self.role,
            self.channel,
            self.message_ids,
            self.excluded_roles,
            force_dm=True
        )

        await interaction.followup.send("DM 전송 완료")


class VoteCheck(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.dm_enabled = True

    async def fetch_messages(self, channel, message_ids):

        messages = []

        for mid in message_ids:

            try:
                msg = await channel.fetch_message(mid)
                messages.append(msg)
            except:
                pass

        return messages

    async def get_voters(self, messages):

        voters = set()

        for msg in messages:

            for reaction in msg.reactions:

                async for user in reaction.users():

                    if not user.bot:
                        voters.add(user.id)

        return voters

    async def run_vote_check(self, guild, role, channel, message_ids, excluded_roles, force_dm=False):

        messages = await self.fetch_messages(channel, message_ids)

        reaction_map = {}
        voters = set()

        for msg in messages:

            for reaction in msg.reactions:

                emoji = str(reaction.emoji)

                if emoji not in reaction_map:
                    reaction_map[emoji] = []

                async for user in reaction.users():

                    if user.bot:
                        continue

                    reaction_map[emoji].append(user)
                    voters.add(user.id)

        members = []

        for m in role.members:

            if m.bot:
                continue

            skip = False

            for r in excluded_roles:
                if r in m.roles:
                    skip = True

            if skip:
                continue

            members.append(m)

        total = len(members)

        not_voted = []

        for m in members:

            if m.id not in voters:
                not_voted.append(m)

        voted_count = total - len(not_voted)

        rate = 0

        if total > 0:
            rate = (voted_count / total) * 100

        # Firebase 기록
        db.collection("vote_logs").add({
            "guild": guild.id,
            "channel": channel.id,
            "messages": message_ids,
            "participation_rate": rate
        })

        # 미참여 누적
        for m in not_voted:

            ref = db.collection("vote_users").document(str(m.id))
            doc = ref.get()

            if doc.exists:
                miss = doc.to_dict().get("miss_count", 0) + 1
            else:
                miss = 1

            ref.set({
                "miss_count": miss,
                "name": str(m)
            })

        # DM
        if self.dm_enabled or force_dm:

            for m in not_voted:

                try:
                    await m.send(
                        f"📢 {guild.name} 투표에 참여하지 않았습니다.\n"
                        f"채널: {channel.mention}"
                    )
                except:
                    pass

        msg = "📊 **투표 결과**\n\n"

        # 항목별 출력
        for emoji, users in reaction_map.items():

            msg += f"{emoji} ({len(users)}명)\n"

            for u in users:
                msg += f"{u.mention}\n"

            msg += "\n"

        # 미참여
        if not_voted:

            msg += f"❌ **미참여 ({len(not_voted)}명)**\n"

            for m in not_voted:
                msg += f"{m.mention}\n"

            msg += "\n"

        msg += f"참여율: **{rate:.2f}%**"

        return msg

    @commands.command(name="투표확인")
    @commands.has_permissions(administrator=True)
    async def vote_check(self, ctx, role: discord.Role, channel_id: int, *message_ids: int):

        guild = ctx.guild
        channel = guild.get_channel(channel_id)

        if not channel:

            await ctx.send("채널을 찾을 수 없습니다")
            return

        if len(message_ids) == 0:

            await ctx.send("메시지 ID 필요")
            return

        result = await self.run_vote_check(
            guild,
            role,
            channel,
            message_ids,
            []
        )

        view = VoteView(self, ctx, role, channel, message_ids, [])

        await ctx.send(result, view=view)

    @commands.command(name="투표DM켜기")
    async def dm_on(self, ctx):

        self.dm_enabled = True
        await ctx.send("투표 DM 켜짐")

    @commands.command(name="투표DM끄기")
    async def dm_off(self, ctx):

        self.dm_enabled = False
        await ctx.send("투표 DM 꺼짐")

    @commands.command(name="미참여순위")
    async def miss_rank(self, ctx):

        docs = db.collection("vote_users").order_by("miss_count", direction=firestore.Query.DESCENDING).limit(10).stream()

        msg = "📉 **투표 미참여 순위**\n\n"

        rank = 1

        for doc in docs:

            data = doc.to_dict()

            msg += f"{rank}. {data['name']} - {data['miss_count']}회\n"

            rank += 1

        await ctx.send(msg)


async def setup(bot):
    await bot.add_cog(VoteCheck(bot))
