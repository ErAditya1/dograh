import asyncio
from sqlalchemy import text
from api.db import db_client

async def main():
    async with db_client.async_session() as session:
        # Check master numbers
        res = await session.execute(text("""
            SELECT id, organization_id, telephony_configuration_id, address, pool_type, rental_status, is_active, is_platform_inventory, extra_metadata
            FROM telephony_phone_numbers
            ORDER BY id;
        """))
        rows = res.fetchall()
        print(f"Total phone numbers in DB: {len(rows)}")
        for r in rows:
            print(dict(r._mapping))

        print("\n--- Configurations ---")
        cfg_res = await session.execute(text("""
            SELECT id, organization_id, name, provider, is_default_outbound, is_platform_inventory, inactive
            FROM telephony_configurations
            ORDER BY id;
        """))
        for c in cfg_res.fetchall():
            print(dict(c._mapping))

if __name__ == "__main__":
    asyncio.run(main())
