"""
Перенос полей сайта из users в first_site и удаление колонок из users.

Поля: email, password_hash, activation_pass, field_bool_1.
Связь: first_site.tg_id = users.user_id.

Запуск из корня проекта (нужны переменные .env для Postgres):
  python -m config_bd.migrate_users_site_fields_to_first_site

  Либо напрямую:
  python config_bd/migrate_users_site_fields_to_first_site.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text

from config_bd.models import FirstSite, engine


async def migrate() -> None:
    async with engine.begin() as conn:
        def _create_first_site(sync_conn):
            FirstSite.__table__.create(sync_conn, checkfirst=True)

        await conn.run_sync(_create_first_site)

        has_email_col = await conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'users'
                  AND column_name = 'email'
                LIMIT 1
                """
            )
        )
        if has_email_col.scalar_one_or_none() is None:
            print("OK: users.email уже отсутствует — миграция ранее выполнена.")
            return

        dup = await conn.execute(
            text(
                """
                SELECT email, COUNT(*) AS cnt
                FROM users
                WHERE email IS NOT NULL AND TRIM(email) <> ''
                GROUP BY email
                HAVING COUNT(*) > 1
                """
            )
        )
        dup_rows = dup.fetchall()
        if dup_rows:
            print("Error: duplicate non-null emails in users. Fix and re-run.", file=sys.stderr)
            for r in dup_rows:
                print(f"  email={r[0]!r} rows={r[1]}", file=sys.stderr)
            raise SystemExit(1)

        await conn.execute(
            text(
                """
                INSERT INTO first_site (tg_id, email, password_hash, activation_pass, field_bool_1)
                SELECT
                    u.user_id,
                    NULLIF(TRIM(u.email), ''),
                    u.password_hash,
                    u.activation_pass,
                    COALESCE(u.field_bool_1, FALSE)
                FROM users u
                WHERE u.email IS NOT NULL
                   OR u.password_hash IS NOT NULL
                   OR u.activation_pass IS NOT NULL
                   OR COALESCE(u.field_bool_1, FALSE) = TRUE
                ON CONFLICT (tg_id) DO UPDATE SET
                    email = COALESCE(EXCLUDED.email, first_site.email),
                    password_hash = COALESCE(EXCLUDED.password_hash, first_site.password_hash),
                    activation_pass = COALESCE(EXCLUDED.activation_pass, first_site.activation_pass),
                    field_bool_1 = first_site.field_bool_1 OR EXCLUDED.field_bool_1
                """
            )
        )

        await conn.execute(text("DROP INDEX IF EXISTS uq_users_email"))

        await conn.execute(
            text(
                """
                ALTER TABLE users
                  DROP COLUMN IF EXISTS email,
                  DROP COLUMN IF EXISTS password_hash,
                  DROP COLUMN IF EXISTS activation_pass,
                  DROP COLUMN IF EXISTS field_bool_1
                """
            )
        )

        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_first_site_email ON first_site (email)"
            )
        )

    print(
        "OK: first_site создана/обновлена; данные перенесены; "
        "из users удалены email, password_hash, activation_pass, field_bool_1."
    )


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
