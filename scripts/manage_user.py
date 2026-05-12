"""대시보드 사용자 관리 CLI.

사용 예:
    # 첫 admin 비밀번호 설정
    python scripts/manage_user.py --set-password admin

    # 건축 담당자 신규 추가
    python scripts/manage_user.py --add building1 --name "김건축" --role viewer --categories 건축,건축·토목

    # 비밀번호만 변경
    python scripts/manage_user.py --set-password building1

    # 사용자 목록
    python scripts/manage_user.py --list

    # 사용자 삭제
    python scripts/manage_user.py --delete building1
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth import ALL_CATEGORIES, delete_user, list_users, upsert_user


def main() -> int:
    parser = argparse.ArgumentParser(description="대시보드 사용자 관리")
    parser.add_argument("--add", metavar="USERNAME", help="새 사용자 추가")
    parser.add_argument("--set-password", metavar="USERNAME", help="비밀번호 재설정")
    parser.add_argument("--delete", metavar="USERNAME", help="사용자 삭제")
    parser.add_argument("--list", action="store_true", help="사용자 목록")
    parser.add_argument("--name", default="", help="표시 이름")
    parser.add_argument("--email", default="", help="이메일")
    parser.add_argument("--role", choices=["admin", "viewer"], default="viewer")
    parser.add_argument(
        "--categories",
        default="",
        help=f"쉼표로 구분된 카테고리 (가능: {', '.join(ALL_CATEGORIES)})",
    )
    args = parser.parse_args()

    if args.list:
        users = list_users()
        if not users:
            print("(등록된 사용자 없음)")
            return 0
        print(f"{'USERNAME':<15} {'ROLE':<8} {'NAME':<15} CATEGORIES")
        for u in users:
            print(f"{u.username:<15} {u.role:<8} {u.name:<15} {u.categories}")
        return 0

    if args.delete:
        if delete_user(args.delete):
            print(f"OK — '{args.delete}' 삭제됨")
            return 0
        print(f"ERROR — '{args.delete}' 사용자가 없습니다")
        return 2

    if args.add:
        if not args.categories and args.role != "admin":
            print("ERROR — --categories 옵션이 필요합니다 (예: --categories 건축,건축·토목)")
            return 2
        cats = [c.strip() for c in args.categories.split(",") if c.strip()] if args.categories else ALL_CATEGORIES
        pw = getpass.getpass(f"'{args.add}' 비밀번호 입력: ")
        pw2 = getpass.getpass("비밀번호 다시 입력: ")
        if pw != pw2:
            print("ERROR — 비밀번호가 일치하지 않습니다")
            return 2
        if len(pw) < 6:
            print("ERROR — 비밀번호는 6자 이상이어야 합니다")
            return 2
        upsert_user(
            args.add,
            password=pw,
            name=args.name or args.add,
            email=args.email,
            role=args.role,
            categories=cats,
        )
        print(f"OK — '{args.add}' 추가됨 (role={args.role}, categories={cats})")
        return 0

    if args.set_password:
        pw = getpass.getpass(f"'{args.set_password}' 새 비밀번호: ")
        pw2 = getpass.getpass("새 비밀번호 다시 입력: ")
        if pw != pw2:
            print("ERROR — 비밀번호가 일치하지 않습니다")
            return 2
        if len(pw) < 6:
            print("ERROR — 비밀번호는 6자 이상이어야 합니다")
            return 2
        upsert_user(args.set_password, password=pw)
        print(f"OK — '{args.set_password}' 비밀번호 변경됨")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
