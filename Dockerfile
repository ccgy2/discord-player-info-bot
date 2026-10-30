FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 TOKENIZERS_PARALLELISM=false HF_HUB_DISABLE_TELEMETRY=1 \
    AUTOMOD_OCR_IDLE_SECONDS=0 AUTOMOD_OCR_EAGER=false BOT_MESSAGE_CACHE=0

COPY requirements.txt ./
# CPU-only builds avoid installing/loading CUDA libraries on a CPU Railway service.
# Official compatible pair: https://pytorch.org/get-started/previous-versions/
RUN python -m pip install --no-cache-dir torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && python -c "import easyocr, torch, torchvision; assert torch.version.cuda is None; print('CPU OCR dependencies OK')"

COPY . .
# Existing Railway override 'python start_automod.py' remains supported.
CMD ["python", "bot.py"]
