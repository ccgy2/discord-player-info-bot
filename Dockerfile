FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# requirements 설치

COPY requirements.txt ./

RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && python -c "import easyocr, torch, torchvision; print('OCR packages OK:', easyocr.__version__)"

# 소스 복사
COPY . .

# 실행
CMD ["python", "bot.py"]
