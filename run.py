"""Unified application entrypoint for Toss Analytics & Tracking Admin Panel.

Usage:
    python run.py                   # Run web dashboard with embedded background scheduler
    python run.py --web             # Run Uvicorn web dashboard only
    python run.py --scheduler       # Run background scheduler daemon only
    python run.py --telegram        # Run Telethon Telegram listener daemon
    python run.py --backup          # Execute immediate database hot backup
    python run.py --sync            # Execute immediate match data synchronization
    python run.py --create-admin    # Interactive CLI command to create or reset admin user
    python run.py --all             # Concurrently launch web app, scheduler, and Telegram listener
"""

import sys
import os
import argparse
import asyncio
import getpass
import uvicorn
from loguru import logger

# Add root directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings
from app.logger import setup_logging
from app.db import init_db, AsyncSessionLocal
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.services.backup_service import perform_database_backup
from app.services.seeder import seed_leagues, seed_admin_user, seed_external_sources
from app.auth.security import hash_password
from app.models import AdminUser
from sqlalchemy import select


def run_web():
    """Launch FastAPI application via Uvicorn."""
    setup_logging()
    logger.info("Starting Toss Analytics Web Server on http://0.0.0.0:8000 ...")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config=None
    )


async def run_scheduler_daemon():
    """Run standalone APScheduler background daemon."""
    setup_logging()
    logger.info("Starting standalone APScheduler background worker...")
    await init_db()
    sched = start_scheduler()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        stop_scheduler()


async def run_telegram_daemon():
    """Run standalone Telegram channel listener daemon."""
    setup_logging()
    logger.info("Starting standalone Telegram listener daemon...")
    await init_db()
    from app.collectors.telegram_listener import TelegramListener
    listener = TelegramListener()
    await listener.run_listener_loop()


def run_backup():
    """Execute immediate database backup."""
    setup_logging()
    logger.info("Triggering manual database hot backup...")
    res = perform_database_backup()
    if res["status"] == "SUCCESS":
        logger.info(f"Backup SUCCESS: {res['filename']} ({res['size_bytes'] / 1024:.1f} KB). Retained {res['total_backups_retained']} backups.")
    else:
        logger.error(f"Backup FAILED: {res.get('error') or res.get('message')}")


async def run_sync():
    """Trigger immediate match and fixture synchronization."""
    setup_logging()
    logger.info("Triggering manual match and external source synchronization...")
    await init_db()
    from app.jobs.tasks import sync_live_matches_task, verify_toss_results_task, poll_external_sources_task
    await sync_live_matches_task()
    await verify_toss_results_task()
    await poll_external_sources_task()
    logger.info("Manual synchronization completed.")


async def create_admin_interactive():
    """Interactive CLI prompt to create or update administrator user."""
    setup_logging()
    await init_db()
    print("\n--- Toss Analytics Admin User Setup ---")
    username = input("Enter admin username [default: admin]: ").strip() or "admin"
    password = getpass.getpass("Enter secure password: ")
    confirm = getpass.getpass("Confirm secure password: ")

    if password != confirm:
        print("Error: Passwords do not match.")
        sys.exit(1)

    if len(password) < 8:
        print("Error: Password must be at least 8 characters.")
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        stmt = select(AdminUser).where(AdminUser.username == username)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        pw_hash = hash_password(password)
        if user:
            user.password_hash = pw_hash
            user.is_active = True
            print(f"Updated password for existing administrator '{username}'.")
        else:
            user = AdminUser(
                username=username,
                password_hash=pw_hash,
                email=f"{username}@tosstracker.local",
                is_active=True
            )
            session.add(user)
            print(f"Created new administrator user '{username}'.")

        await session.commit()
    print("Done! You may now log in to the admin dashboard.\n")


async def run_all_concurrently():
    """Run web server and background tasks concurrently."""
    setup_logging()
    logger.info("Starting unified all-in-one process (Web + Scheduler + Telegram)...")
    await init_db()
    
    # 1. Start scheduler
    start_scheduler()
    
    # 2. Start telegram listener task in background if enabled
    from app.collectors.telegram_listener import TelegramListener
    listener = TelegramListener()
    telegram_task = asyncio.create_task(listener.run_listener_loop())

    # 3. Start web server
    config = uvicorn.Config("app.main:app", host="0.0.0.0", port=8000, log_config=None)
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        telegram_task.cancel()
        stop_scheduler()


def run_streamlit(port: int = 8501):
    """Launch Streamlit interactive web dashboard."""
    import subprocess
    cmd = [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.port", str(port)]
    subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(description="Toss Analytics & Tracking Admin Panel Runner")
    parser.add_argument("--web", action="store_true", help="Launch Uvicorn web server only")
    parser.add_argument("--streamlit", action="store_true", help="Launch Streamlit dashboard")
    parser.add_argument("--scheduler", action="store_true", help="Launch standalone scheduler worker")
    parser.add_argument("--telegram", action="store_true", help="Launch standalone Telegram listener daemon")
    parser.add_argument("--backup", action="store_true", help="Execute database hot backup and exit")
    parser.add_argument("--sync", action="store_true", help="Execute immediate data sync and exit")
    parser.add_argument("--create-admin", action="store_true", help="Create or reset administrator credentials interactively")
    parser.add_argument("--all", action="store_true", help="Concurrently run web app, scheduler, and Telegram listener")

    args = parser.parse_args()

    if args.create_admin:
        asyncio.run(create_admin_interactive())
    elif args.backup:
        run_backup()
    elif args.sync:
        asyncio.run(run_sync())
    elif args.streamlit:
        run_streamlit()
    elif args.scheduler:
        asyncio.run(run_scheduler_daemon())
    elif args.telegram:
        asyncio.run(run_telegram_daemon())
    elif args.all:
        asyncio.run(run_all_concurrently())
    else:
        # Default behavior: run web app with embedded scheduler
        run_web()


if __name__ == "__main__":
    main()

