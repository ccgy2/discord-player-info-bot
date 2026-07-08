import json
import os
import re
import discord
from discord.ext import commands
from firebase_admin import credentials, firestore, initialize_app
from google import genai  # Gemini 최신 라이브러리
import numpy as np
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

PDF_FOLDER = "pdf_data"

if not os.path.exists(PDF_FOLDER):
    os.makedirs(PDF_FOLDER)

# AI 임베딩 모델 (텍스트 벡터화용)
model = SentenceTransformer("all-MiniLM-L6-v2")


def split_text(text, size=500):
    chunks = []
    for i in range(0, len(text), size):
        chunks.append(text[i : i + size])
    return chunks


def extract_article(text):
    match = re.search(r"(제\s*\d+\s*조)", text)
    if match:
        return match.group(1)
    return None


class PDFAI(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

        # Railway 환경변수에서 Firebase JSON 설정을 가져옵니다.
        firebase_config_str = os.environ.get("FIREBASE_CONFIG")

        if firebase_config_str:
            try:
                # 문자열로 된 JSON을 파싱하여 인증 정보로 사용합니다.
                cred_dict = json.loads(firebase_config_str)
                cred = credentials.Certificate(cred_dict)
                initialize_app(cred)
            except ValueError:
                # 이미 초기화된 경우 예외 처리
                pass
            except Exception as e:
                print(f"Firebase 초기화 중 오류 발생: {e}")
        else:
            print(
                "경고: FIREBASE_CONFIG 환경변수가 설정되지 않았습니다. 로컬 인증을 시도합니다."
            )

        self.db = firestore.client()

    async def save_chunks(self, chunks, source):
        embeddings = model.encode(chunks)

        for i, chunk in enumerate(chunks):
            article = extract_article(chunk)

            data = {
                "text": chunk,
                "source": source,
                "article": article,
                "embedding": embeddings[i].tolist(),
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

    # -----------------------------
    # PDF 등록
    # -----------------------------
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

        await ctx.send("📄 PDF 분석 중입니다...")

        reader = PdfReader(path)
        text = ""

        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

        chunks = split_text(text)
        await self.save_chunks(chunks, file.filename)

        await ctx.send("✅ PDF 학습 완료.")

    # -----------------------------
    # 질문 (Gemini AI 답변 생성)
    # -----------------------------
    @commands.command(name="질문")
    async def ask(self, ctx, *, question):
        await ctx.send("🔍 관련 정보를 검색하고 Gemini AI가 답변을 생성 중입니다...")

        # 1. 내 문서에서 관련 내용 검색
        results = self.search(question)

        if not results:
            await ctx.send("관련 정보를 찾지 못했습니다.")
            return

        # 2. Gemini에게 줄 참고 문서 데이터 조립
        context = ""
        for r in results:
            article = r.get("article")
            title = (
                f"[{r['source']} - {article}]" if article else f"[{r['source']}]"
            )
            context += f"{title}\n{r['text']}\n\n"

        # 3. Railway 환경변수에서 Gemini 키 가져오기
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            await ctx.send(
                "❌ 서버에 GEMINI_API_KEY 환경변수가 설정되지 않았습니다."
            )
            return

        # Gemini 클라이언트 초기화
        client = genai.Client(api_key=api_key)

        try:
            # 4. 프롬프트 구성 및 AI 답변 요청
            prompt = (
                f"[참고 문서]\n{context}\n\n"
                f"[사용자 질문]\n{question}\n\n"
                f"지시사항: 제공된 참고 문서의 내용만을 바탕으로 질문에 정확하고 친절하게 답변하세요. "
                f"만약 문서 내용으로 답변을 알 수 없다면 억지로 지어내지 말고 관련 내용을 찾을 수 없다고 답변하세요."
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )

            ai_answer = response.text

            if not ai_answer:
                await ctx.send("AI가 답변을 생성하지 못했습니다.")
                return

            # 디코드 글자수 한계(2000자) 고려하여 안전하게 전송
            await ctx.send(ai_answer[:2000])

        except Exception as e:
            await ctx.send(f"❌ Gemini AI 답변 생성 중 오류가 발생했습니다: {e}")

    # -----------------------------
    # 조문 검색
    # -----------------------------
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

    # -----------------------------
    # PDF 목록
    # -----------------------------
    @commands.command(name="pdf목록")
    async def list_pdf(self, ctx):
        docs = self.db.collection("pdf_chunks").stream()
        files = set()

        for doc in docs:
            # 문서의 내부 필드가 아니라, 
            # 왼쪽 리스트에 있는 문서 고유의 ID(예: '규정.pdf')를 직접 가져옵니다.
            if doc.id:
                files.add(doc.id)

        if not files:
            await ctx.send("등록된 PDF가 없습니다.")
            return

        msg = "📚 등록된 PDF 목록\n\n"
        for f in files:
            msg += f + "\n"

        await ctx.send(msg)


async def setup(bot):
    await bot.add_cog(PDFAI(bot))
