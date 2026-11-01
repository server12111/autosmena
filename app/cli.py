import argparse
import getpass

from sqlalchemy import select

from app.config import settings
from app.db import Session
from app.models import Product, User
from app.security import password_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["password", "seed", "check-ledger"])
    args = parser.parse_args()
    if args.command == "password":
        password = getpass.getpass("New admin password (min 12 characters): ")
        if not 12 <= len(password) <= 256:
            raise SystemExit("Use between 12 and 256 characters")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords do not match")
        print("ADMIN_PASSWORD_HASH=" + password_hash(password))
    elif args.command == "seed":
        if not settings.demo_mode:
            raise SystemExit("Seed is available only in demo mode")
        with Session.begin() as db:
            if not db.scalar(select(Product.id).limit(1)):
                for country, code in [("США", "us")]:
                    for name, service in [("Telegram", "tg")]:
                        db.add(
                            Product(
                                name=name + " · DEMO",
                                country=country,
                                supplier_code=f"demo:{code}:{service}",
                                cost=500000,
                                markup_bps=3000,
                            )
                        )
        print("Demo catalog ready")
    else:
        from sqlalchemy import func

        from app.models import Ledger

        with Session() as db:
            bad = []
            for user in db.scalars(select(User)):
                total = db.scalar(
                    select(func.coalesce(func.sum(Ledger.delta), 0)).where(Ledger.user_id == user.id)
                )
                if total != user.balance:
                    bad.append(user.id)
            if bad:
                raise SystemExit(f"Ledger mismatch for users: {bad}")
        print("Ledger reconciled")


if __name__ == "__main__":
    main()
