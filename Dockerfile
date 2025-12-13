FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# requirements 설치
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 소스 복사
COPY . .

# 실행
CMD ["python", "bot.py"]
