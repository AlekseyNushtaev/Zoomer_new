import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, select, update

from config_bd.models import AsyncSessionLocal, PartnerBotApplications
from utils.token_crypto import encrypt_token, token_hash


class PartnerAppSQL:
    def __init__(self):
        self.session_factory = AsyncSessionLocal

    async def create_application(
        self,
        *,
        partner_tg_id: int,
        partner_username: Optional[str],
        partner_first_name: Optional[str],
        bot_token_encrypted: str,
        bot_token_hash: str,
        bot_username: str,
        bot_display_name: str,
        source_bot_id: Optional[int] = None,
    ) -> PartnerBotApplications:
        async with self.session_factory() as session:
            app = PartnerBotApplications(
                partner_tg_id=partner_tg_id,
                partner_username=partner_username,
                partner_first_name=partner_first_name,
                bot_token_encrypted=bot_token_encrypted,
                bot_token_hash=bot_token_hash,
                bot_username=bot_username.lstrip("@"),
                bot_display_name=bot_display_name,
                source_bot_id=source_bot_id,
                status="pending",
            )
            session.add(app)
            await session.commit()
            await session.refresh(app)
            return app

    async def get_by_id(self, app_id: int) -> Optional[PartnerBotApplications]:
        async with self.session_factory() as session:
            return await session.get(PartnerBotApplications, app_id)

    async def get_by_token_hash(self, token_hash: str) -> Optional[PartnerBotApplications]:
        async with self.session_factory() as session:
            stmt = select(PartnerBotApplications).where(PartnerBotApplications.bot_token_hash == token_hash)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_by_bot_username(self, bot_username: str) -> Optional[PartnerBotApplications]:
        uname = (bot_username or "").lstrip("@")
        if not uname:
            return None
        async with self.session_factory() as session:
            stmt = select(PartnerBotApplications).where(PartnerBotApplications.bot_username == uname)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_by_partner(self, partner_tg_id: int) -> List[PartnerBotApplications]:
        async with self.session_factory() as session:
            stmt = (
                select(PartnerBotApplications)
                .where(PartnerBotApplications.partner_tg_id == partner_tg_id)
                .order_by(PartnerBotApplications.created_at.desc())
            )
            return list((await session.execute(stmt)).scalars().all())

    async def list_by_status(self, status: str) -> List[PartnerBotApplications]:
        async with self.session_factory() as session:
            stmt = (
                select(PartnerBotApplications)
                .where(PartnerBotApplications.status == status)
                .order_by(PartnerBotApplications.created_at.desc())
            )
            return list((await session.execute(stmt)).scalars().all())

    async def list_all(self) -> List[PartnerBotApplications]:
        async with self.session_factory() as session:
            stmt = select(PartnerBotApplications).order_by(PartnerBotApplications.id.asc())
            return list((await session.execute(stmt)).scalars().all())

    async def list_settings_export(
        self,
        bot_ids: Optional[Sequence[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Identity-поля для синка в partner_bot_settings (id заявки == bot_id на VPS)."""
        async with self.session_factory() as session:
            stmt = select(PartnerBotApplications).order_by(PartnerBotApplications.id.asc())
            if bot_ids:
                stmt = stmt.where(PartnerBotApplications.id.in_(list(bot_ids)))
            rows = list((await session.execute(stmt)).scalars().all())
        result: List[Dict[str, Any]] = []
        for app in rows:
            result.append(
                {
                    "bot_id": app.id,
                    "partner_username": app.partner_username,
                    "bot_username": (app.bot_username or "").lstrip("@") or None,
                    "bot_display_name": app.bot_display_name,
                    "source_bot_id": app.source_bot_id,
                    "status": app.status,
                }
            )
        return result

    async def delete_all(self) -> int:
        async with self.session_factory() as session:
            result = await session.execute(delete(PartnerBotApplications))
            await session.commit()
            return result.rowcount or 0

    async def get_active_draft(self, partner_tg_id: int) -> Optional[PartnerBotApplications]:
        async with self.session_factory() as session:
            stmt = (
                select(PartnerBotApplications)
                .where(
                    PartnerBotApplications.partner_tg_id == partner_tg_id,
                    PartnerBotApplications.status == "draft",
                )
                .order_by(PartnerBotApplications.created_at.desc())
                .limit(1)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def create_draft_application(
        self,
        *,
        partner_tg_id: int,
        partner_username: Optional[str],
        partner_first_name: Optional[str],
        source_bot_id: Optional[int] = None,
    ) -> PartnerBotApplications:
        draft_uid = uuid.uuid4().hex
        placeholder = f"draft:{partner_tg_id}:{draft_uid}"
        async with self.session_factory() as session:
            app = PartnerBotApplications(
                partner_tg_id=partner_tg_id,
                partner_username=partner_username,
                partner_first_name=partner_first_name,
                bot_token_encrypted=encrypt_token(placeholder),
                bot_token_hash=token_hash(placeholder),
                bot_username=f"draft_{draft_uid}_bot",
                bot_display_name="VPN_project",
                source_bot_id=source_bot_id,
                status="draft",
            )
            session.add(app)
            await session.flush()
            app.bot_username = f"white_lable_n{app.id}_bot"
            await session.commit()
            await session.refresh(app)
            return app

    async def complete_draft_application(
        self,
        app_id: int,
        *,
        bot_token_encrypted: str,
        bot_token_hash: str,
        bot_username: str,
        bot_display_name: str,
    ) -> Optional[PartnerBotApplications]:
        async with self.session_factory() as session:
            app = await session.get(PartnerBotApplications, app_id)
            if not app or app.status != "draft":
                return None
            app.bot_token_encrypted = bot_token_encrypted
            app.bot_token_hash = bot_token_hash
            app.bot_username = bot_username.lstrip("@")
            app.bot_display_name = bot_display_name
            app.status = "pending"
            await session.commit()
            await session.refresh(app)
            return app

    async def update_token(
        self,
        app_id: int,
        *,
        bot_token_encrypted: str,
        bot_token_hash: str,
        bot_username: Optional[str] = None,
        bot_display_name: Optional[str] = None,
    ) -> Optional[PartnerBotApplications]:
        async with self.session_factory() as session:
            app = await session.get(PartnerBotApplications, app_id)
            if not app:
                return None
            app.bot_token_encrypted = bot_token_encrypted
            app.bot_token_hash = bot_token_hash
            if bot_username is not None:
                app.bot_username = bot_username.lstrip("@")
            if bot_display_name is not None:
                app.bot_display_name = bot_display_name
            await session.commit()
            await session.refresh(app)
            return app

    async def update_status(
        self,
        app_id: int,
        status: str,
        *,
        reject_reason: Optional[str] = None,
        instance_id: Optional[str] = None,
        deployed_at: Optional[datetime] = None,
    ) -> None:
        values = {"status": status}
        if reject_reason is not None:
            values["reject_reason"] = reject_reason
        if instance_id is not None:
            values["instance_id"] = instance_id
        if deployed_at is not None:
            values["deployed_at"] = deployed_at
        async with self.session_factory() as session:
            await session.execute(
                update(PartnerBotApplications).where(PartnerBotApplications.id == app_id).values(**values)
            )
            await session.commit()
