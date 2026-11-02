"""Command line administrative interface for Toss Analytics.

Provides first-run CLI commands such as creating or resetting the administrator user.
"""

import sys
import os
import asyncio
import getpass
import argparse
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import AsyncSessionLocal, init_db
from app.models import AdminUser
from app.auth.security import hash_password
from app.services.audit_service import log_audit_event


async def _save_admin_in_session(
    session: AsyncSession,
    username: str,
    password: str,
    email: Optional[str] = None
) -> AdminUser:
    stmt = select(AdminUser).where(AdminUser.username == username.strip())
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()

    pw_hash = hash_password(password)

    if user:
        user.password_hash = pw_hash
        user.is_active = True
        if email:
            user.email = email.strip()
        action_name = "UPDATE_ADMIN_USER"
        print(f"[*] Updating existing administrator '{username}'...")
    else:
        user = AdminUser(
            username=username.strip(),
            password_hash=pw_hash,
            email=email.strip() if email else None,
            is_active=True
        )
        session.add(user)
        action_name = "CREATE_ADMIN_USER"
        print(f"[*] Creating new administrator '{username}'...")

    await session.commit()
    await session.refresh(user)

    # Audit log the administrative creation
    await log_audit_event(
        session=session,
        action=action_name,
        entity="AdminUser",
        entity_id=str(user.id),
        user="CLI_TOOL",
        ip_address="127.0.0.1",
        details=f"Admin user '{username}' configured via CLI"
    )
    print(f"[+] Administrator user '{username}' saved successfully (bcrypt hashed).")
    return user


async def create_or_update_admin(
    username: str,
    password: str,
    email: Optional[str] = None,
    session: Optional[AsyncSession] = None
) -> AdminUser:
    """Create or update an administrator user with bcrypt password hash."""
    if session is not None:
        return await _save_admin_in_session(session, username, password, email)

    await init_db()
    async with AsyncSessionLocal() as db_session:
        return await _save_admin_in_session(db_session, username, password, email)


def interactive_create_admin(
    cli_username: Optional[str] = None,
    cli_password: Optional[str] = None,
    cli_email: Optional[str] = None
) -> None:
    """Prompt for admin credentials interactively if not provided via arguments."""
    username = cli_username
    if not username:
        username_input = input("Enter admin username [default: admin]: ").strip()
        username = username_input or "admin"

    password = cli_password
    if not password:
        while True:
            p1 = getpass.getpass("Enter admin password: ")
            if not p1:
                print("Error: Password cannot be empty. Try again.")
                continue
            p2 = getpass.getpass("Confirm admin password: ")
            if p1 != p2:
                print("Error: Passwords do not match. Try again.")
                continue
            password = p1
            break

    email = cli_email
    if email is None and not cli_username:
        email_input = input("Enter admin email (optional): ").strip()
        email = email_input or None

    asyncio.run(create_or_update_admin(username=username, password=password, email=email))


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Toss Analytics Administrative CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    admin_parser = subparsers.add_parser("create-admin", help="Create or reset administrator credentials")
    admin_parser.add_argument("--username", "-u", default=None, help="Admin username")
    admin_parser.add_argument("--password", "-p", default=None, help="Admin password (non-interactive)")
    admin_parser.add_argument("--email", "-e", default=None, help="Admin email (optional)")

    args = parser.parse_args()

    if args.command == "create-admin" or args.command is None:
        # Default to create-admin if no command provided
        cli_user = getattr(args, "username", None)
        cli_pass = getattr(args, "password", None)
        cli_email = getattr(args, "email", None)
        interactive_create_admin(cli_username=cli_user, cli_password=cli_pass, cli_email=cli_email)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
