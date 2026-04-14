import os
from pathlib import Path

from dotenv import load_dotenv


def _get_secret_dir() -> Path:
    secret_dir = os.getenv("STOCK_SAYO_SECRET_DIR")
    if secret_dir:
        return Path(secret_dir).expanduser()
    return Path.home() / ".stock-sayo-secrets"


def load_runtime_env() -> None:
    # 프로젝트 루트 .env 로드
    load_dotenv()

    # 외부 비밀 폴더의 .env 로드 (있으면 덮어쓰기)
    external_env = _get_secret_dir() / ".env"
    if external_env.exists():
        load_dotenv(dotenv_path=external_env, override=True)


def get_firebase_key_path() -> str:
    # 1) 직접 경로 지정이 최우선
    explicit = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_PATH")
    if explicit:
        return str(Path(explicit).expanduser())

    # 2) 외부 비밀 폴더 내부 파일 참조
    candidate = _get_secret_dir() / "firebase-key.json"
    if candidate.exists():
        return str(candidate)

    # 3) 프로젝트 루트 fallback
    return "firebase-key.json"

