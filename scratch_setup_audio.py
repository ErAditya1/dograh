import os, sys, json
base_dir = r"d:\Feeding_Trends\calling-saas\dograh"
if base_dir not in sys.path: sys.path.insert(0, base_dir)
from dotenv import load_dotenv
load_dotenv(os.path.join(base_dir, "api", ".env"))
from minio import Minio
from minio.commonconfig import CopySource
import asyncio
from api.db import db_client
from sqlalchemy import text

client = Minio("localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False)

for src, dst in [
    ("recordings/136.wav", "recordings/184.wav"),
    ("recordings/136/user.wav", "recordings/184/user.wav"),
    ("recordings/136/bot.wav", "recordings/184/bot.wav"),
]:
    try:
        client.copy_object("voice-audio", dst, CopySource("voice-audio", src))
        print(f"Copied {src} -> {dst}")
    except Exception as e:
        print(f"Error copying {src}: {e}")

async def update_db():
    async with db_client.get_async_session() as session:
        recs_json = {
            "mixed": {"storage_key": "recordings/184.wav", "storage_backend": "minio", "format": "wav", "track": "mixed"},
            "user": {"storage_key": "recordings/184/user.wav", "storage_backend": "minio", "format": "wav", "track": "user"},
            "bot": {"storage_key": "recordings/184/bot.wav", "storage_backend": "minio", "format": "wav", "track": "bot"},
        }
        extra_data = {
            "use_draft": True,
            "organization_id": 1,
            "recordings": recs_json,
        }
        await session.execute(text("""
            UPDATE workflow_runs
            SET recording_url = 'recordings/184.wav',
                storage_backend = 'minio',
                extra = CAST(:extra AS json)
            WHERE id = 184
        """), {"extra": json.dumps(extra_data)})
        await session.commit()
        print("Updated run 184 DB recording fields successfully!")

asyncio.run(update_db())
