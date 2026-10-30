"""PDF commands without resident PyTorch/Transformer/Gemini SDK imports."""
import json
import os
from pathlib import Path
import tempfile
import discord
from discord.ext import commands
from firebase_admin import credentials, firestore, initialize_app, get_app
from resource_runtime import WorkerError, get_manager
from resource_workers.pdf_job import split_text, extract_article, MAX_PDF_BYTES

PDF_FOLDER = "pdf_data"  # Historical path; existing files are never removed.


class PDFAI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            get_app()
        except ValueError:
            raw = os.getenv("FIREBASE_KEY") or os.getenv("FIREBASE_CONFIG")
            initialize_app(credentials.Certificate(json.loads(raw)) if raw else None)
        self.db = firestore.client()
        self.jobs = get_manager(bot)

    def _request(self, action, **fields):
        return {"action": action, "project": self.db.project, **fields}

    @commands.command(name="pdf등록")
    @commands.has_permissions(administrator=True)
    async def upload_pdf(self, ctx):
        if not ctx.message.attachments:
            await ctx.send("PDF 파일을 첨부해주세요.")
            return
        attachment = ctx.message.attachments[0]
        if not attachment.filename.lower().endswith(".pdf"):
            await ctx.send("PDF 파일만 업로드 가능합니다.")
            return
        if attachment.size > MAX_PDF_BYTES:
            await ctx.send("PDF는 최대 10 MiB입니다. 큰 문서는 나누어 올려주세요.")
            return
        try:
            async with self.jobs.slot("PDF 등록") as job:
                # No unbounded uploads folder; filenames never become paths.
                with tempfile.TemporaryDirectory(prefix="player-pdf-") as directory:
                    path = Path(directory) / "input.pdf"
                    await attachment.save(path)
                    if path.stat().st_size > MAX_PDF_BYTES:
                        raise WorkerError("PDF는 최대 10 MiB입니다.")
                    await ctx.send("📄 PDF 분석 중입니다. 분석이 끝나면 모델 메모리를 반환합니다.")
                    result = await job.run("pdf_job", self._request("upload", path=str(path), source=attachment.filename), timeout=600)
                await ctx.send(f"✅ PDF 학습 완료. ({result['chunks']}개 문단)")
        except WorkerError as exc:
            await ctx.send(f"❌ {exc}\n등록 도중 중단됐다면 일부 문단이 반영됐을 수 있습니다.")

    @commands.command(name="질문")
    async def ask(self, ctx, *, question):
        if not os.getenv("GEMINI_API_KEY"):
            await ctx.send("❌ 서버에 GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.")
            return
        if not question.strip() or len(question) > 4000:
            await ctx.send("질문은 1~4,000자로 입력해주세요.")
            return
        await self._perform(ctx, "ask", question=question)

    @commands.command(name="조문")
    async def article(self, ctx, number: str):
        await self._perform(ctx, "article", number=number)

    @commands.command(name="pdf목록")
    async def list_pdf(self, ctx):
        await self._perform(ctx, "list")

    async def _perform(self, ctx, action, **fields):
        try:
            async with self.jobs.slot("PDF " + action) as job:
                if action == "ask":
                    await ctx.send("🔍 관련 정보를 검색하고 Gemini AI가 답변을 생성 중입니다...")
                result = await job.run("pdf_job", self._request(action, **fields), timeout=300)
            await ctx.send(result["message"][:2000], allowed_mentions=discord.AllowedMentions.none())
        except WorkerError as exc:
            await ctx.send(f"❌ {exc}")


async def setup(bot):
    await bot.add_cog(PDFAI(bot))
