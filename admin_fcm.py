"""Local-only FCM approval utility backed by Firebase Admin credentials."""

import argparse
import hashlib
import sys

import firebase_admin
from firebase_admin import credentials, firestore


def get_db():
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(credentials.Certificate("firebase-key.json"))
    return firestore.client()


def fingerprint(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def clean_text(value):
    return str(value or "-").replace("\n", " ").replace("\r", " ")[:40]


def list_tokens(db):
    rows = []
    for snapshot in db.collection("fcm_tokens").stream():
        data = snapshot.to_dict() or {}
        rows.append((
            data.get("approved") is True,
            clean_text(data.get("nickname")),
            clean_text(data.get("registered_at") or data.get("createdAt")),
            fingerprint(snapshot.id),
        ))
    rows.sort(key=lambda row: (row[0], row[2], row[1]))
    if not rows:
        print("등록된 FCM 토큰이 없습니다.")
        return
    print("상태\tID\t\t닉네임\t등록 시각")
    for approved, nickname, registered_at, token_id in rows:
        status = "승인" if approved else "대기"
        print(f"{status}\t{token_id}\t{nickname}\t{registered_at}")


def resolve_token(db, token_id):
    matches = [
        snapshot
        for snapshot in db.collection("fcm_tokens").stream()
        if fingerprint(snapshot.id).startswith(token_id.lower())
    ]
    if not matches:
        raise ValueError(f"일치하는 토큰 ID가 없습니다: {token_id}")
    if len(matches) > 1:
        raise ValueError("토큰 ID가 여러 건과 일치합니다. 더 긴 ID를 입력하세요.")
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description="FCM 토큰 승인 관리")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="토큰 원문 없이 등록 목록 표시")

    approve = subparsers.add_parser("approve", help="지문 ID로 토큰 승인")
    approve.add_argument("token_id")

    delete = subparsers.add_parser("delete", help="지문 ID로 토큰 삭제")
    delete.add_argument("token_id")
    delete.add_argument("--yes", action="store_true", help="삭제 확인")

    args = parser.parse_args()
    db = get_db()

    if args.command == "list":
        list_tokens(db)
        return

    snapshot = resolve_token(db, args.token_id)
    token_id = fingerprint(snapshot.id)
    if args.command == "approve":
        snapshot.reference.update({"approved": True})
        print(f"승인 완료: {token_id}")
        return

    if not args.yes:
        print("삭제하려면 --yes를 함께 지정하세요.", file=sys.stderr)
        raise SystemExit(2)
    snapshot.reference.delete()
    print(f"삭제 완료: {token_id}")


if __name__ == "__main__":
    main()
