"""Railway start wrapper: install missing OCR weights, then exec the existing bot.

Set Railway Start Command to: python start_automod.py
No token/key is read or printed here. No package installation or DB writes occur.
Model files are public EasyOCR weights; chat images are never uploaded.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REQUIRED_MODELS = ("craft_mlt_25k.pth", "korean_g2.pth")


def model_directory(root: Path) -> Path:
    configured = os.environ.get("AUTOMOD_OCR_MODEL_DIR", "").strip()
    directory = Path(configured).expanduser() if configured else root / "data" / "ocr_models"
    if not directory.is_absolute():
        directory = root / directory
    return directory.resolve()


def missing_models(directory: Path) -> list[str]:
    missing = []
    for name in REQUIRED_MODELS:
        try:
            if not (directory / name).is_file() or (directory / name).stat().st_size == 0:
                missing.append(name)
        except OSError:
            missing.append(name)
    return missing


def prepare_models(root: Path) -> bool:
    directory = model_directory(root)
    missing = missing_models(directory)
    if not missing:
        print("[AutoMod 시작] 모델 파일 존재. 실제 모델 로딩은 OCR 작업자가 확인합니다.", flush=True)
        return True
    print("[AutoMod 시작] 누락 OCR 모델: " + ", ".join(missing), flush=True)
    print("[AutoMod 시작] 공개 EasyOCR 모델을 설치합니다. 이미지/대화 내용을 전송하지 않습니다.", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, str(root / "cogs" / "automod.py"),
             "--install-ocr-models", "--model-dir", str(directory)],
            cwd=str(root), timeout=600, check=False,
        )
        ok = result.returncode == 0 and not missing_models(directory)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Do not dump environment variables or the exception's arbitrary text.
        print(f"[AutoMod 시작] 모델 설치 실패: {type(exc).__name__}", flush=True)
        ok = False
    if ok:
        print("[AutoMod 시작] 모델 설치 확인 완료.", flush=True)
    else:
        print("[AutoMod 시작] OCR 준비 실패. EasyOCR/CPU PyTorch 설치·네트워크·저장공간을 확인하세요.", flush=True)
        print("[AutoMod 시작] 기존 봇은 시작하지만 OCR 정상이나 검열 정상 운영을 의미하지 않습니다.", flush=True)
    return ok


def main() -> int:
    if not (ROOT / "bot.py").is_file() or not (ROOT / "cogs" / "automod.py").is_file():
        print("[AutoMod 시작] 프로젝트 루트의 bot.py와 cogs/automod.py가 필요합니다.", flush=True)
        return 2
    # Keep the same local .env behavior as bot.py; process variables take precedence.
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(ROOT / ".env", override=False)
    prepare_models(ROOT)
    os.chdir(ROOT)
    print("[AutoMod 시작] bot.py 실행", flush=True)
    # Replace the launcher, so Railway signals reach the bot process directly.
    os.execv(sys.executable, [sys.executable, str(ROOT / "bot.py")])
    return 0  # execv only returns if a test double replaces it


if __name__ == "__main__":
    raise SystemExit(main())
