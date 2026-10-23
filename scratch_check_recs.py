import os, sys
base_dir = r"d:\Feeding_Trends\calling-saas\dograh"
if base_dir not in sys.path: sys.path.insert(0, base_dir)
from dotenv import load_dotenv
load_dotenv(os.path.join(base_dir, "api", ".env"))
import asyncio
from api.db import db_client
from sqlalchemy import text

async def main():
    async with db_client.get_async_session() as session:
        res = await session.execute(text("""
            SELECT id, campaign_id, recording_url, extra->'recordings' as recs, storage_backend
            FROM workflow_runs
            WHERE recording_url IS NOT NULL OR extra->'recordings' IS NOT NULL
            ORDER BY id DESC LIMIT 10
        """))
        rows = res.fetchall()
        print("Runs with recordings:", len(rows))
        for r in rows:
            print("Row:", r)

        res2 = await session.execute(text("""
            SELECT id, campaign_id, recording_url, extra, gathered_context
            FROM workflow_runs
            WHERE id >= 170
            ORDER BY id DESC LIMIT 5
        """))
        print("\nRuns 170+:")
        for r in res2.fetchall():
            print(f"ID={r[0]}, campaign_id={r[1]}, rec={r[2]}, extra={r[3]}")
            gathered = r[4] or {}
            print("  gathered keys:", list(gathered.keys()))
            if "extracted_variables" in gathered:
                print("  extracted_variables:", gathered["extracted_variables"])
            if "schedule" in gathered or "booking" in gathered or "appointment" in gathered:
                print("  schedule:", gathered.get("schedule") or gathered.get("booking"))

asyncio.run(main())
