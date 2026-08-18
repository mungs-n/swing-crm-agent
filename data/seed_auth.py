"""companies/platform_users에 데모 계정 2개(ATHLEPA, 데이콘)를 시딩한다."""

import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.auth import hash_password

COMPANIES = [
    {"company_id": "athlepa", "company_name": "ATHLEPA", "dataset_source": "athlepa"},
    {"company_id": "dacon_retail", "company_name": "데이콘 리테일 (예시 2번째 기업)", "dataset_source": "dacon"},
]

USERS = [
    {"email": "athlepa@demo.com", "password": "demo1234", "company_id": "athlepa"},
    {"email": "dacon@demo.com", "password": "demo1234", "company_id": "dacon_retail"},
]


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    sb.table("companies").upsert(COMPANIES, on_conflict="company_id").execute()
    print(f"companies: {len(COMPANIES)}건 등록")

    rows = [
        {"email": u["email"], "password_hash": hash_password(u["password"]), "company_id": u["company_id"]}
        for u in USERS
    ]
    sb.table("platform_users").upsert(rows, on_conflict="email").execute()
    print(f"platform_users: {len(rows)}건 등록")


if __name__ == "__main__":
    main()
