import os
import re
import json
import discord
import numpy as np
from discord.ext import commands
from discord import app_commands
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
from firebase_admin import firestore

PDF_FOLDER = "pdf_data"

if not os.path.exists(PDF_FOLDER):
    os.makedirs(PDF_FOLDER)

model = SentenceTransformer("all-MiniLM-L6-v2")

def split_text(text, size=500):
    chunks = []
    for i in range(0, len(text), size):
        chunks.append(text[i:i+size])
    return chunks

def extract_article(text):
    match = re.search(r"(제\s*\d+\s*조)", text)
    if match:
        return match.group(1)
    return None

class PDFAI(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.db = firestore.client()

    async def save_chunks(self, chunks, source):

        embeddings = model.encode(chunks)

        for i, chunk in enumerate(chunks):

            article = extract_article(chunk)

            data = {
                "text": chunk,
                "source": source,
                "article": article,
                "embedding": embeddings[i].tolist()
            }

            self.db.collection("pdf_chunks").add(data)

    def search(self, question, k=3):

        q_embed = model.encode([question])[0]

        docs = self.db.collection("pdf_chunks").stream()

        results = []

        for doc in docs:
            d = doc.to_dict()

            emb = np.array(d["embedding"])

            score = np.dot(q_embed, emb) / (
                np.linalg.norm(q_embed) * np.linalg.norm(emb)
            )

            results.append((score, d))

        results.sort(reverse=True, key=lambda x: x[0])

        return [r[1] for r in results[:k]]

    @commands.command(name="pdf등록")
    @commands.has_permissions(administrator=True)
    async def upload_pdf(self, ctx):

        if not ctx.message.attachments:
            await ctx.send("PDF 파일을 첨부해주세요.")
            return

        file = ctx.message.attachments[0]

        if not file.filename.endswith(".pdf"):
            await ctx.send("PDF 파일만 업로드 가능합니다.")
            return

        path = os.path.join(PDF_FOLDER, file.filename)

        await file.save(path)

        await ctx.send("PDF 분석 중입니다...")

        reader = PdfReader(path)

        text = ""

        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

        chunks = split_text(text)

        await self.save_chunks(chunks, file.filename)

        await ctx.send("PDF 학습 완료.")

    @commands.command(name="질문")
    async def ask(self, ctx, *, question):

        results = self.search(question)

        if not results:
            await ctx.send("관련 정보를 찾지 못했습니다.")
            return

        answer = ""

        for r in results:

            article = r.get("article")

            if article:
                answer += f"[{r['source']} - {article}]\n"
            else:
                answer += f"[{r['source']}]\n"

            answer += r["text"] + "\n\n"

        await ctx.send(answer[:2000])

    @commands.command(name="조문")
    async def article(self, ctx, number: str):

        pattern = f"제{number}조"

        docs = self.db.collection("pdf_chunks").stream()

        results = []

        for doc in docs:

            d = doc.to_dict()

            if d.get("article") == pattern:
                results.append(d)

        if not results:
            await ctx.send("조문을 찾지 못했습니다.")
            return

        msg = ""

        for r in results[:3]:

            msg += f"[{r['source']} - {r['article']}]\n"
            msg += r["text"] + "\n\n"

        await ctx.send(msg[:2000])

    @commands.command(name="pdf목록")
    async def list_pdf(self, ctx):

        docs = self.db.collection("pdf_chunks").stream()

        files = set()

        for doc in docs:
            d = doc.to_dict()
            files.add(d["source"])

        if not files:
            await ctx.send("등록된 PDF가 없습니다.")
            return

        msg = "등록된 PDF 목록\n\n"

        for f in files:
            msg += f + "\n"

        await ctx.send(msg)

    @app_commands.command(name="질문", description="PDF 규정 질문")
    async def slash_question(self, interaction: discord.Interaction, question: str):

        results = self.search(question)

        if not results:
            await interaction.response.send_message("관련 정보를 찾지 못했습니다.")
            return

        answer = ""

        for r in results:

            article = r.get("article")

            if article:
                answer += f"[{r['source']} - {article}]\n"
            else:
                answer += f"[{r['source']}]\n"

            answer += r["text"] + "\n\n"

        await interaction.response.send_message(answer[:2000])

    @app_commands.command(name="조문", description="조문 검색")
    async def slash_article(self, interaction: discord.Interaction, number: str):

        pattern = f"제{number}조"

        docs = self.db.collection("pdf_chunks").stream()

        results = []

        for doc in docs:

            d = doc.to_dict()

            if d.get("article") == pattern:
                results.append(d)

        if not results:
            await interaction.response.send_message("조문을 찾지 못했습니다.")
            return

        msg = ""

        for r in results[:3]:

            msg += f"[{r['source']} - {r['article']}]\n"
            msg += r["text"] + "\n\n"

        await interaction.response.send_message(msg[:2000])

async def setup(bot):

    cog = PDFAI(bot)

    await bot.add_cog(cog)

    bot.tree.add_command(cog.slash_question)
    bot.tree.add_command(cog.slash_article)
