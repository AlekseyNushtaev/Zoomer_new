import time
import uuid

from sqlalchemy import select, update, delete, func, and_, or_, cast, Date, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import datetime, date, timedelta, timezone
from typing import Any, Optional, List, Tuple, Dict

from config_bd.models import (
    AsyncSessionLocal,
    Users,
    Payments,
    Gifts,
    PaymentsCryptobot,
    PaymentsStars,
    Online,
    WhiteCounter,
    PaymentsCards,
    PaymentsPlategaCrypto,
    PaymentsWataSBP,
    PaymentsWataCard,
    PaymentsFkSBP,
    LinkingCodes,
    PasswordResetCodes,
    WlTrafficMeta,
)
from lexicon import dct_price
from logging_config import logger

# Размер батча для IN (...) в PostgreSQL (очень длинные списки режутся).
_STAT_IN_CHUNK = 8000

_BILLING_OK_STATUSES = ("confirmed", "paid")

# Старые тарифы до полноценного payload (сумма → дни, только обычная подписка).
_LEGACY_BILLING_AMOUNT_TO_DAYS: Dict[int, int] = {
    99: 30,
    269: 120,
    499: 180,
    3490: 5000,
    2790: 5000,
}


def _billing_days_for_tariff_key(key: str) -> Optional[int]:
    """Дни обычной подписки по ключу тарифа из dct_price (без white)."""
    if "white" in key:
        return None
    if key == "7":
        return 7
    if key in ("30", "30old"):
        return 30
    if key == "90":
        return 90
    if key == "120":
        return 120
    if key == "180":
        return 180
    if key == "365":
        return 365
    if key == "730":
        return 730
    if key == "5000":
        return 5000
    if key == "5000sale":
        return 5000
    if key == "30secret":
        return 30
    return None


def _payload_duration_to_panel_days(raw: Optional[str]) -> Optional[int]:
    """Значение duration из payload платежа → число дней для панели (30secret → 30)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "30secret":
        return 30
    if s == "5000sale":
        return 5000
    try:
        v = int(s)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_traffic_duration(raw: Optional[str]) -> Optional[int]:
    """duration:traffic10 -> 10 GB; иначе None."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s.startswith("traffic"):
        return None
    try:
        return int(s.replace("traffic", ""))
    except ValueError:
        return None


def _payment_method_label(raw: Optional[str]) -> str:
    if not raw:
        return "—"
    key = raw.strip().lower()
    labels = {
        "sbp": "Platega СБП",
        "card": "Platega карта",
        "crypto": "Platega крипто",
        "wata_sbp": "WATA СБП",
        "wata_card": "WATA карта",
        "fk_sbp": "FreeKassa СБП",
        "fk_card": "FreeKassa карта",
        "stars": "Stars",
        "cryptobot": "CryptoBot",
    }
    return labels.get(key, raw.strip())


def _white_days_from_amount_fallback(amount: Any) -> Optional[int]:
    """Дни вайт-подписки по сумме (dct_price, ключи с white)."""
    try:
        target = int(round(float(amount)))
    except (TypeError, ValueError):
        return None
    for key, price in dct_price.items():
        if "white" not in key:
            continue
        if int(price) != target:
            continue
        if key == "white_30":
            return 30
    return None


def _billing_duration_from_amount_fallback(amount: Any) -> Optional[int]:
    """
    Если в payload нет duration: актуальные суммы из dct_price (несколько тарифов на цену — max дней)
    плюс устаревшие суммы из _LEGACY_BILLING_AMOUNT_TO_DAYS (99→30, 269→120, 499→180).
    """
    try:
        target = int(round(float(amount)))
    except (TypeError, ValueError):
        return None
    if target == 1:
        return None
    candidates: list[int] = []
    for key, price in dct_price.items():
        days = _billing_days_for_tariff_key(key)
        if days is None:
            continue
        if int(price) != target:
            continue
        candidates.append(days)
    from_tariffs = max(candidates) if candidates else None
    legacy_days = _LEGACY_BILLING_AMOUNT_TO_DAYS.get(target)
    if from_tariffs is not None and legacy_days is not None:
        return max(from_tariffs, legacy_days)
    if from_tariffs is not None:
        return from_tariffs
    return legacy_days


def _naive_utc(dt: datetime) -> datetime:
    """asyncpg + TIMESTAMP WITHOUT TIME ZONE: только naive datetime; время в UTC."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _user_tuple(user: Users) -> Tuple:
    return (
        user.id,
        user.user_id,
        user.ref,
        user.is_delete,
        user.in_panel,
        user.is_connect,
        user.create_user,
        user.in_chanel,
        user.reserve_field,
        user.subscription_end_date,
        user.white_subscription_end_date,
        user.last_notification_date,
        user.last_broadcast_status,
        user.last_broadcast_date,
        user.stamp,
        user.ttclid,
        user.subscribtion,
        user.white_subscription,
        user.email,
        user.password,
        user.activation_pass,
        user.field_str_1,
        user.field_str_2,
        user.field_str_3,
        user.field_bool_1,
        user.field_bool_2,
        user.field_bool_3,
        user.password_hash,
        user.linked_telegram_id,
        user.partner,
        user.partner_balance,
        user.partner_pay,
        user.partner_flag,
        user.trafic_wl,
        user.limit_wl,
    )


def _users_column_value_for_api(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return str(v)
    return v


def user_row_to_api_dict(user: Users) -> Dict[str, Any]:
    """Все колонки таблицы users в JSON-совместимый словарь (имена полей как в БД)."""
    out: Dict[str, Any] = {}
    for col in Users.__table__.columns:
        out[col.key] = _users_column_value_for_api(getattr(user, col.key))
    return out


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _sum_subscription_end_dates(
    a: Optional[datetime], b: Optional[datetime], now: datetime
) -> Optional[datetime]:
    """Суммирует оставшееся время двух подписок (вместо выбора более поздней даты)."""
    if a is None and b is None:
        return None
    now_n = _naive_utc(now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc))

    def rem(dt: Optional[datetime]) -> timedelta:
        if dt is None:
            return timedelta(0)
        n = _naive_utc(dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt)
        return max(timedelta(0), n - now_n)

    total = rem(a) + rem(b)
    if total == timedelta(0):
        if a is None:
            return _naive_utc(b) if b is not None else None
        if b is None:
            return _naive_utc(a) if a is not None else None
        return max(_naive_utc(a), _naive_utc(b))
    return now_n + total


def _max_subscription_end_dates(
    a: Optional[datetime], b: Optional[datetime], now: datetime
) -> Optional[datetime]:
    """Более поздняя дата окончания (для merge без двух оплат по одному треку)."""
    if a is None and b is None:
        return None

    def norm(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return _naive_utc(dt)
        return _naive_utc(dt.astimezone(timezone.utc).replace(tzinfo=None))

    vals: List[datetime] = []
    if a is not None:
        vals.append(norm(a))
    if b is not None:
        vals.append(norm(b))
    return max(vals)


def _payload_white_flag(payload: Optional[str]) -> bool:
    """True — mobile/white трек; без ключа white в payload считаем обычной подпиской."""
    if not payload or not str(payload).strip():
        return False
    try:
        parts = dict(item.split(":", 1) for item in str(payload).split(","))
    except ValueError:
        return False
    return parts.get("white", "False") == "True"


_MERGE_PAYMENT_MODELS = (
    Payments,
    PaymentsCards,
    PaymentsPlategaCrypto,
    PaymentsWataSBP,
    PaymentsWataCard,
    PaymentsFkSBP,
    PaymentsStars,
    PaymentsCryptobot,
)


async def _merge_user_paid_subscription_flags(session, user_id: int) -> Tuple[bool, bool]:
    """
    (оплаченный pro-трек, оплаченный white-трек): успешный не-подарочный платёж
    с white=False / white=True в payload (аналогично для обеих сторон merge).
    """
    has_pro = False
    has_white = False
    for model in _MERGE_PAYMENT_MODELS:
        if has_pro and has_white:
            break
        stmt = select(model.payload).where(
            model.user_id == user_id,
            model.is_gift.is_(False),
            model.status.in_(_BILLING_OK_STATUSES),
        )
        result = await session.execute(stmt)
        for (payload,) in result.all():
            if _payload_white_flag(payload):
                has_white = True
            else:
                has_pro = True
            if has_pro and has_white:
                break
    return has_pro, has_white


class AsyncSQL:
    def __init__(self):
        self.session_factory = AsyncSessionLocal

    async def get_user(self, user_id: int) -> Optional[Tuple]:
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user:
                return _user_tuple(user)
            return None

    async def get_user_by_internal_id(self, internal_id: int) -> Optional[Tuple]:
        async with self.session_factory() as session:
            user = await session.get(Users, internal_id)
            if user:
                return _user_tuple(user)
            return None

    async def get_user_by_email(self, email: str) -> Optional[Tuple]:
        em = _norm_email(email)
        async with self.session_factory() as session:
            stmt = select(Users).where(func.lower(Users.email) == em)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user:
                return _user_tuple(user)
            return None

    async def get_user_object_by_user_id(self, user_id: int) -> Optional[Users]:
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_user_object_by_internal_id(self, internal_id: int) -> Optional[Users]:
        async with self.session_factory() as session:
            return await session.get(Users, internal_id)

    async def next_negative_user_id(self) -> int:
        """
        Следующий отрицательный user_id для сайта. С панелью X-UI username ≥ 3 символов,
        поэтому первый id — -10, далее не выдаём -1…-9 (строка «-N» короче 3 символов).
        """
        async with self.session_factory() as session:
            stmt = select(func.min(Users.user_id)).where(Users.user_id < 0)
            result = await session.execute(stmt)
            m = result.scalar_one_or_none()
            if m is None:
                return -10
            nxt = int(m) - 1
            while nxt < 0 and len(str(nxt)) < 3:
                nxt -= 1
            return nxt

    async def register_email_user(
        self, email: str, password_hash: str, stamp: str = "email"
    ) -> int:
        em = _norm_email(email)
        uid = await self.next_negative_user_id()
        async with self.session_factory() as session:
            u = Users(
                user_id=uid,
                email=em,
                password_hash=password_hash,
                stamp=stamp,
                create_user=_naive_utc(datetime.now(timezone.utc)),
            )
            session.add(u)
            await session.commit()
            await session.refresh(u)
            return int(u.id)

    async def set_user_stamp_by_internal_id(self, internal_id: int, stamp: str) -> bool:
        """Обновляет stamp только если текущее значение пустое или 'email'."""
        async with self.session_factory() as session:
            result = await session.execute(select(Users).where(Users.id == internal_id))
            user = result.scalar_one_or_none()
            if user is None:
                return False
            current = (user.stamp or "").strip()
            if current and current != "email":
                return False
            user.stamp = stamp
            await session.commit()
            return True

    async def set_password_hash_by_internal_id(self, internal_id: int, password_hash: str) -> bool:
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(Users.id == internal_id)
                .values(password_hash=password_hash)
            )
            r = await session.execute(stmt)
            await session.commit()
            return (r.rowcount or 0) > 0

    async def set_activation_pass_by_email(self, email: str, value) -> bool:
        em = _norm_email(email)
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(func.lower(Users.email) == em)
                .values(activation_pass=value)
            )
            r = await session.execute(stmt)
            await session.commit()
            return (r.rowcount or 0) > 0

    async def set_email_verified(self, internal_id: int, verified: bool) -> bool:
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(Users.id == internal_id)
                .values(field_bool_1=verified)
            )
            r = await session.execute(stmt)
            await session.commit()
            return (r.rowcount or 0) > 0

    async def replace_password_reset_codes(self, email: str, code: str, expires_at: datetime) -> None:
        em = _norm_email(email)
        exp = _naive_utc(expires_at)
        async with self.session_factory() as session:
            await session.execute(delete(PasswordResetCodes).where(PasswordResetCodes.email == em))
            session.add(PasswordResetCodes(email=em, code=code, expires_at=exp))
            await session.commit()

    async def verify_password_reset_code(self, email: str, code: str) -> bool:
        em = _norm_email(email)
        now = _naive_utc(datetime.now(timezone.utc))
        async with self.session_factory() as session:
            stmt = select(PasswordResetCodes).where(
                PasswordResetCodes.email == em,
                PasswordResetCodes.code == code,
                PasswordResetCodes.expires_at > now,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return row is not None

    async def delete_password_reset_codes_for_email(self, email: str) -> None:
        em = _norm_email(email)
        async with self.session_factory() as session:
            await session.execute(delete(PasswordResetCodes).where(PasswordResetCodes.email == em))
            await session.commit()

    async def replace_linking_code(self, creator_internal_id: int, code: str, expires_at: datetime) -> None:
        exp = _naive_utc(expires_at)
        now = _naive_utc(datetime.now(timezone.utc))
        async with self.session_factory() as session:
            await session.execute(delete(LinkingCodes).where(LinkingCodes.user_id == creator_internal_id))
            session.add(
                LinkingCodes(
                    code=code,
                    user_id=creator_internal_id,
                    created_at=now,
                    expires_at=exp,
                )
            )
            await session.commit()

    async def get_valid_linking_code(self, code: str) -> Optional[Tuple[int, int]]:
        """Возвращает (code_id, creator_internal_id) или None."""
        now = _naive_utc(datetime.now(timezone.utc))
        async with self.session_factory() as session:
            stmt = select(LinkingCodes).where(
                LinkingCodes.code == code,
                LinkingCodes.expires_at > now,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return (int(row.code_id), int(row.user_id))

    async def delete_linking_code_by_id(self, code_id: int) -> None:
        async with self.session_factory() as session:
            await session.execute(delete(LinkingCodes).where(LinkingCodes.code_id == code_id))
            await session.commit()

    async def delete_linking_codes_for_user_internal(self, internal_id: int) -> None:
        async with self.session_factory() as session:
            await session.execute(delete(LinkingCodes).where(LinkingCodes.user_id == internal_id))
            await session.commit()

    async def merge_email_placeholder_into_telegram(
        self,
        email_row_internal_id: int,
        telegram_user_id: int,
    ) -> bool:
        """
        Сливает строку только-email (user_id < 0) в строку Telegram (user_id > 0).
        Email и password_hash переносятся на telegram-строку; placeholder удаляется.
        """
        async with self.session_factory() as session:
            e = await session.get(Users, email_row_internal_id)
            if e is None or e.user_id >= 0:
                return False
            stmt = select(Users).where(Users.user_id == telegram_user_id)
            t = (await session.execute(stmt)).scalar_one_or_none()
            if t is None or t.user_id <= 0:
                return False

            merge_now = datetime.now(timezone.utc)

            # uq_users_email: нельзя присвоить t.email пока та же строка есть у e — сначала снимаем с placeholder
            merged_email = e.email
            merged_password_hash = e.password_hash
            e.email = None
            e.password_hash = None
            await session.flush()

            t_paid_pro, t_paid_white = await _merge_user_paid_subscription_flags(session, t.user_id)
            e_paid_pro, e_paid_white = await _merge_user_paid_subscription_flags(session, e.user_id)

            if t_paid_pro and e_paid_pro:
                t.subscription_end_date = _sum_subscription_end_dates(
                    t.subscription_end_date, e.subscription_end_date, merge_now
                )
            else:
                t.subscription_end_date = _max_subscription_end_dates(
                    t.subscription_end_date, e.subscription_end_date, merge_now
                )

            if t_paid_white and e_paid_white:
                t.white_subscription_end_date = _sum_subscription_end_dates(
                    t.white_subscription_end_date, e.white_subscription_end_date, merge_now
                )
            else:
                t.white_subscription_end_date = _max_subscription_end_dates(
                    t.white_subscription_end_date, e.white_subscription_end_date, merge_now
                )
            t.in_panel = bool(t.in_panel or e.in_panel)
            t.in_chanel = bool(t.in_chanel or e.in_chanel)
            t.is_connect = bool(t.is_connect or e.is_connect)
            t.is_delete = bool(t.is_delete or e.is_delete)
            t.reserve_field = bool(t.reserve_field or e.reserve_field)
            if not (t.ref or "") and (e.ref or ""):
                t.ref = e.ref
            if merged_email:
                t.email = merged_email
            if merged_password_hash:
                t.password_hash = merged_password_hash
            if (e.stamp or "") and (e.stamp or "") != "email":
                if not (t.stamp or "") or (t.stamp or "") == "email":
                    t.stamp = e.stamp
            if not (t.ttclid or "") and (e.ttclid or ""):
                t.ttclid = e.ttclid
            if not (t.subscribtion or "") and (e.subscribtion or ""):
                t.subscribtion = e.subscribtion
            if not (t.white_subscription or "") and (e.white_subscription or ""):
                t.white_subscription = e.white_subscription
            t.field_bool_1 = bool(t.field_bool_1 or e.field_bool_1)
            t.field_bool_2 = bool(t.field_bool_2 or e.field_bool_2)
            t.field_bool_3 = bool(t.field_bool_3 or e.field_bool_3)

            old_uid = e.user_id
            await session.delete(e)
            await session.flush()

            await session.execute(
                update(Users).where(Users.ref == str(old_uid)).values(ref=str(telegram_user_id))
            )
            await session.execute(
                update(Gifts).where(Gifts.giver_id == old_uid).values(giver_id=telegram_user_id)
            )
            await session.execute(
                update(Gifts).where(Gifts.recepient_id == old_uid).values(recepient_id=telegram_user_id)
            )
            for model in (
                Payments,
                PaymentsCards,
                PaymentsPlategaCrypto,
                PaymentsWataSBP,
                PaymentsWataCard,
                PaymentsStars,
                PaymentsCryptobot,
                WhiteCounter,
            ):
                await session.execute(
                    update(model).where(model.user_id == old_uid).values(user_id=telegram_user_id)
                )

            await session.execute(delete(LinkingCodes).where(LinkingCodes.user_id == email_row_internal_id))
            await session.commit()

        merged_email_kept = _norm_email(merged_email) if merged_email else None
        try:
            from bot import x3
            from X3 import (
                panel_username_for_site_email,
                panel_username_for_site_user,
            )

            if merged_email_kept:
                await x3.delete_panel_user_by_username(
                    panel_username_for_site_email(merged_email_kept, False)
                )
                await x3.delete_panel_user_by_username(
                    panel_username_for_site_email(merged_email_kept, True)
                )
            await x3.delete_panel_user_by_username(
                panel_username_for_site_user(old_uid, False)
            )
            await x3.delete_panel_user_by_username(
                panel_username_for_site_user(old_uid, True)
            )

            row = await self.get_user(telegram_user_id)
            if row:
                def _aware_utc(dt: datetime) -> datetime:
                    if dt.tzinfo is None:
                        return dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)

                sub_end = row[9]
                w_end = row[10]
                tid = int(row[1])
                if sub_end:
                    await x3.set_expiration_date(str(tid), _aware_utc(sub_end), tid)
                if w_end:
                    await x3.set_expiration_date(str(tid) + "_white", _aware_utc(w_end), tid)
        except Exception as ex:
            logger.warning("post-merge panel cleanup/sync: {}", ex)

        return True

    async def add_user(
        self,
        user_id: int,
        in_panel: bool,
        is_connect: bool = False,
        ref: str = '',
        is_delete: bool = False,
        in_chanel: bool = False,
        stamp: str = '',
        partner: str = '',
    ) -> bool:
        """True, если строка вставлена; False при конфликте user_id (гонки /start)."""
        async with self.session_factory() as session:
            stmt = (
                pg_insert(Users)
                .values(
                    user_id=user_id,
                    ref=ref or None,
                    partner=partner or None,
                    is_delete=is_delete,
                    in_panel=in_panel,
                    is_connect=is_connect,
                    in_chanel=in_chanel,
                    stamp=stamp,
                    create_user=_naive_utc(datetime.now(timezone.utc)),
                )
                .on_conflict_do_nothing(index_elements=[Users.user_id])
            )
            try:
                result = await session.execute(stmt)
                await session.commit()
                return (result.rowcount or 0) > 0
            except Exception as e:
                await session.rollback()
                logger.error(f"Error inserting user {user_id}: {e}")
                return False

    async def upsert_user_from_export_users_xlsx(
        self,
        user_id: int,
        create_user: datetime,
        *,
        in_panel: bool,
        is_connect: bool,
        subscription_end_date: Optional[datetime],
        subscribtion: Optional[str],
    ) -> None:
        """
        Вставка или обновление пользователя по строке импорта /export_users (.xlsx).
        Поля вне сценария импорта у существующей строки не трогаются.
        """
        cu = _naive_utc(create_user)
        sub_end = _naive_utc(subscription_end_date) if subscription_end_date else None

        async with self.session_factory() as session:
            result = await session.execute(select(Users).where(Users.user_id == user_id))
            row = result.scalar_one_or_none()
            if row:
                row.create_user = cu
                row.in_panel = in_panel
                row.is_connect = is_connect
                row.subscription_end_date = sub_end
                row.subscribtion = subscribtion
            else:
                session.add(
                    Users(
                        user_id=user_id,
                        create_user=cu,
                        in_panel=in_panel,
                        is_connect=is_connect,
                        subscription_end_date=sub_end,
                        subscribtion=subscribtion,
                        stamp="",
                        ref=None,
                        is_delete=False,
                        in_chanel=False,
                        reserve_field=False,
                        white_subscription_end_date=None,
                        white_subscription=None,
                        last_notification_date=None,
                        last_broadcast_status=None,
                        last_broadcast_date=None,
                        ttclid=None,
                        email=None,
                        password=None,
                        password_hash=None,
                        linked_telegram_id=None,
                        activation_pass=None,
                        field_str_1=None,
                        field_str_2=None,
                        field_str_3=None,
                        field_bool_1=False,
                        field_bool_2=False,
                        field_bool_3=False,
                    )
                )
            await session.commit()

    async def update_in_panel(self, user_id: int):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(in_panel=True)
            await session.execute(stmt)
            await session.commit()

    async def update_in_chanel(self, user_id: int, booly: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(in_chanel=booly)
            await session.execute(stmt)
            await session.commit()

    async def update_is_connect(self, user_id: int, booly: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(is_connect=booly)
            await session.execute(stmt)
            await session.commit()

    async def update_ttclid(self, user_id: int, ttclid: str):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(ttclid=ttclid)
            await session.execute(stmt)
            await session.commit()

    async def update_reserve_field(self, user_id: int):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(reserve_field=True)
            await session.execute(stmt)
            await session.commit()

    async def update_delete(self, user_id: int, booly: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(is_delete=booly)
            await session.execute(stmt)
            await session.commit()

    async def update_delete_all(self, booly: bool):
        async with self.session_factory() as session:
            stmt = update(Users).values(is_delete=booly)
            await session.execute(stmt)
            await session.commit()

    async def select_ref_if_in_panel(self, user_id: int) -> Optional[Tuple]:
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id, Users.in_panel == True)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user:
                return (user.id, user.user_id, user.ref, user.is_delete,
                        user.in_panel, user.is_connect, user.create_user,
                        user.in_chanel, user.reserve_field, user.subscription_end_date,
                        user.white_subscription_end_date, user.last_notification_date,
                        user.last_broadcast_status, user.last_broadcast_date,
                        user.stamp, user.ttclid)
            return None

    async def select_ref_count(self, user_id: int) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count(Users.user_id)).where(Users.ref == str(user_id))
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def select_ref_paid_count(self, user_id: int) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count(Users.user_id)).where(
                Users.ref == str(user_id),
                Users.reserve_field == True,
            )
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def select_partner_count(self, partner_id: int) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count(Users.user_id)).where(Users.partner == str(partner_id))
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def select_partner_referrals_payments_sum(self, partner_id: int) -> int:
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(Users.partner == str(partner_id))
            result = await session.execute(stmt)
            user_ids = [row[0] for row in result.all()]
            if not user_ids:
                return 0

            total = 0
            for i in range(0, len(user_ids), _STAT_IN_CHUNK):
                chunk = user_ids[i : i + _STAT_IN_CHUNK]
                for model in _MERGE_PAYMENT_MODELS:
                    stmt_sum = select(func.coalesce(func.sum(model.amount), 0)).where(
                        model.user_id.in_(chunk),
                        model.status.in_(_BILLING_OK_STATUSES),
                    )
                    val = (await session.execute(stmt_sum)).scalar() or 0
                    total += int(val)
            return total

    async def update_partner_flag(self, user_id: int, flag: bool = True) -> None:
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(partner_flag=flag)
            await session.execute(stmt)
            await session.commit()

    async def add_partner_balance(self, partner_user_id: int, amount: int) -> bool:
        if amount <= 0:
            return False
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(Users.user_id == partner_user_id)
                .values(partner_balance=func.coalesce(Users.partner_balance, 0) + amount)
            )
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def partner_record_payout(self, partner_user_id: int, amount: int) -> Tuple[bool, str]:
        """
        Списание с partner_balance и зачисление в partner_pay (вывод средств).
        Возвращает (успех, сообщение об ошибке).
        """
        if amount <= 0:
            return False, "Сумма должна быть больше 0"
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == partner_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user is None:
                return False, "Пользователь не найден"
            balance = user.partner_balance or 0
            if balance < amount:
                return False, f"Недостаточно на балансе: {balance} ₽, запрошено {amount} ₽"
            user.partner_balance = balance - amount
            user.partner_pay = (user.partner_pay or 0) + amount
            await session.commit()
            return True, ""

    async def update_subscription_end_date(self, user_id: int, end_date: datetime):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(
                subscription_end_date=_naive_utc(end_date)
            )
            await session.execute(stmt)
            await session.commit()

    async def update_white_subscription_end_date(self, user_id: int, end_date: datetime):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(
                white_subscription_end_date=_naive_utc(end_date)
            )
            await session.execute(stmt)
            await session.commit()

    async def update_subscribtion(self, user_id: int, subscribtion: Optional[str]):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(subscribtion=subscribtion)
            await session.execute(stmt)
            await session.commit()

    async def update_white_subscription(self, user_id: int, white_subscription: Optional[str]):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(white_subscription=white_subscription)
            await session.execute(stmt)
            await session.commit()

    async def get_subscription_end_date(self, user_id: int) -> Optional[datetime]:
        async with self.session_factory() as session:
            stmt = select(Users.subscription_end_date).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def notification_sent_today(self, user_id: int) -> bool:
        async with self.session_factory() as session:
            stmt = select(Users.last_notification_date).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            last = result.scalar_one_or_none()
            today = date.today()
            if last:
                if isinstance(last, datetime):
                    last = last.date()
                return last == today
            return False

    async def mark_notification_as_sent(self, user_id: int):
        async with self.session_factory() as session:
            utc_today = datetime.now(timezone.utc).date()
            stmt = update(Users).where(Users.user_id == user_id).values(last_notification_date=utc_today)
            await session.execute(stmt)
            await session.commit()

    async def update_field_str_1(self, user_id: int, value: Optional[str]):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(field_str_1=value)
            await session.execute(stmt)
            await session.commit()

    async def update_field_bool_1(self, user_id: int, value: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(field_bool_1=value)
            await session.execute(stmt)
            await session.commit()

    async def update_field_bool_2(self, user_id: int, value: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(field_bool_2=value)
            await session.execute(stmt)
            await session.commit()

    async def reset_field_bool_2_all(self) -> int:
        """Всем строкам users: field_bool_2 = False. Возвращает число обновлённых записей."""
        async with self.session_factory() as session:
            result = await session.execute(update(Users).values(field_bool_2=False))
            await session.commit()
            return int(result.rowcount or 0)

    async def init_wl_trial_limits(self, user_id: int) -> None:
        """При первом триале (limit_wl=0): trafic_wl=0, limit_wl=2 GB. Иначе не трогаем."""
        from wl_traffic.constants import WL_TRIAL_LIMIT_GB

        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(
                    Users.user_id == user_id,
                    or_(Users.limit_wl.is_(None), Users.limit_wl <= 0),
                )
                .values(trafic_wl=0.0, limit_wl=WL_TRIAL_LIMIT_GB)
            )
            await session.execute(stmt)
            await session.commit()

    async def add_wl_limit(self, user_id: int, gb: float) -> None:
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user is None:
                return
            current = float(user.limit_wl or 0.0)
            user.limit_wl = round(current + gb, 2)
            if gb > 0:
                user.field_bool_2 = False
            await session.commit()

    async def update_trafic_wl(self, user_id: int, gb: float) -> None:
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(Users.user_id == user_id)
                .values(trafic_wl=round(gb, 2))
            )
            await session.execute(stmt)
            await session.commit()

    async def add_trafic_wl(self, user_id: int, gb: float) -> None:
        """Прибавляет GB к накопленному trafic_wl (ежедневное накопление с панели)."""
        if gb <= 0:
            return
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user is None:
                return
            current = float(user.trafic_wl or 0.0)
            user.trafic_wl = round(current + gb, 2)
            await session.commit()

    async def get_wl_traffic_last_closed_date(self) -> Optional[date]:
        """Последний WL-день, закрытый accumulate (глобально для бота)."""
        async with self.session_factory() as session:
            stmt = select(WlTrafficMeta.last_closed_date).where(WlTrafficMeta.id == 1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def set_wl_traffic_last_closed_date(self, day: date) -> None:
        """Помечает WL-день как закрытый (идемпотентность accumulate)."""
        async with self.session_factory() as session:
            stmt = select(WlTrafficMeta).where(WlTrafficMeta.id == 1)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                row = WlTrafficMeta(id=1, last_closed_date=day)
                session.add(row)
            else:
                row.last_closed_date = day
            await session.commit()

    async def get_wl_limits(self, user_id: int) -> tuple[float, float]:
        """Возвращает (trafic_wl, limit_wl) в GB."""
        async with self.session_factory() as session:
            stmt = select(Users.trafic_wl, Users.limit_wl).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            row = result.one_or_none()
            if row is None:
                return 0.0, 0.0
            return float(row[0] or 0.0), float(row[1] or 0.0)

    async def select_users_active_subscription(
        self,
    ) -> List[Tuple[int, float, float, bool, Optional[datetime]]]:
        """Активная PRO-подписка: (user_id, trafic_wl, limit_wl, field_bool_2, subscription_end_date)."""
        async with self.session_factory() as session:
            now = datetime.now()
            stmt = select(
                Users.user_id,
                Users.trafic_wl,
                Users.limit_wl,
                Users.field_bool_2,
                Users.subscription_end_date,
            ).where(
                Users.is_delete == False,
                Users.in_panel == True,
                Users.subscription_end_date.isnot(None),
                Users.subscription_end_date > now,
            )
            result = await session.execute(stmt)
            return [
                (
                    int(r[0]),
                    float(r[1] or 0.0),
                    float(r[2] or 0.0),
                    bool(r[3]),
                    r[4],
                )
                for r in result.all()
            ]

    async def select_forever_active_users(self) -> List[int]:
        """Активные пользователи тарифа Навсегда (end_date >= 2030-01-01 и ещё не истекла)."""
        from wl_traffic.constants import FOREVER_END_CUTOFF

        async with self.session_factory() as session:
            now = datetime.now()
            stmt = select(Users.user_id).where(
                Users.is_delete == False,
                Users.in_panel == True,
                Users.subscription_end_date.isnot(None),
                Users.subscription_end_date > now,
                Users.subscription_end_date >= FOREVER_END_CUTOFF,
            )
            result = await session.execute(stmt)
            return [int(r[0]) for r in result.all()]

    async def get_user_id_by_field_str_2(self, value: str) -> Optional[int]:
        """user_id по field_str_2 (например gift_N)."""
        if not value:
            return None
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(Users.field_str_2 == value).limit(1)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return int(row) if row is not None else None

    async def add_wl_limit_subscribers_from_today(self, gb: float) -> list[int]:
        """
        +gb к limit_wl всем, у кого подписка до конца сегодня или позже (МСК).
        field_bool_2 сбрасывается через add_wl_limit.
        Возвращает список user_id.
        """
        from wl_traffic.constants import WL_TIMEZONE

        if gb <= 0:
            return []
        today_start = (
            datetime.now(WL_TIMEZONE)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .replace(tzinfo=None)
        )
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.subscription_end_date.isnot(None),
                Users.subscription_end_date >= today_start,
            )
            result = await session.execute(stmt)
            user_ids = [int(r[0]) for r in result.all()]

        for user_id in user_ids:
            await self.add_wl_limit(user_id, gb)
        return user_ids

    async def update_field_bool_3(self, user_id: int, value: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(field_bool_3=value)
            await session.execute(stmt)
            await session.commit()

    async def reset_field_bool_3_all(self) -> int:
        """Всем строкам users: field_bool_3 = False. Возвращает число обновлённых записей."""
        async with self.session_factory() as session:
            result = await session.execute(update(Users).values(field_bool_3=False))
            await session.commit()
            return int(result.rowcount or 0)

    async def bulk_add_2_days_to_subscription_dates(self) -> Tuple[int, int]:
        """
        Добавляет 2 дня ко всем непустым subscription_end_date и white_subscription_end_date.
        Возвращает (число строк с обновлённой обычной датой, число строк с обновлённой white-датой).
        """
        async with self.session_factory() as session:
            r1 = await session.execute(
                text(
                    "UPDATE users SET subscription_end_date = subscription_end_date + interval '2 days' "
                    "WHERE subscription_end_date IS NOT NULL"
                )
            )
            r2 = await session.execute(
                text(
                    "UPDATE users SET white_subscription_end_date = "
                    "white_subscription_end_date + interval '2 days' "
                    "WHERE white_subscription_end_date IS NOT NULL"
                )
            )
            await session.commit()
            return (int(r1.rowcount or 0), int(r2.rowcount or 0))

    async def get_last_notification_date(self, user_id: int) -> Optional[date]:
        async with self.session_factory() as session:
            stmt = select(Users.last_notification_date).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            val = result.scalar_one_or_none()
            if isinstance(val, datetime):
                return val.date()
            return val

    async def SELECT_ALL_USERS(self) -> List[int]:
        async with self.session_factory() as session:
            today = date.today()
            stmt = select(Users.user_id).where(
                Users.is_delete == False
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_USER_IDS_ACTIVE_WHITE_SUBSCRIPTION(self) -> List[int]:
        """user_id с неистёкшей white-подпиской: дата окончания (календарный день UTC) ≥ сегодня UTC."""
        today_utc = datetime.now(timezone.utc).date()
        async with self.session_factory() as session:
            stmt = (
                select(Users.user_id)
                .where(
                    Users.is_delete == False,
                    Users.white_subscription_end_date.isnot(None),
                    cast(Users.white_subscription_end_date, Date) >= today_utc,
                )
                .order_by(Users.user_id)
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_USER_IDS_ACTIVE_SUBSCRIPTION(self) -> List[int]:
        """user_id с неистёкшей обычной подпиской: дата окончания (календарный день UTC) ≥ сегодня UTC."""
        today_utc = datetime.now(timezone.utc).date()
        async with self.session_factory() as session:
            stmt = (
                select(Users.user_id)
                .where(
                    Users.is_delete == False,
                    Users.subscription_end_date.isnot(None),
                    cast(Users.subscription_end_date, Date) >= today_utc,
                )
                .order_by(Users.user_id)
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_user_ids_subscription_end_on_may_2_5_2026(self) -> List[int]:
        """
        Обычная подписка: календарный день subscription_end_date (UTC, как в остальных выборках)
        совпадает с одним из 2026-05-02 … 2026-05-05.
        """
        targets = (
            date(2026, 5, 2),
            date(2026, 5, 3),
            date(2026, 5, 4),
            date(2026, 5, 5),
        )
        async with self.session_factory() as session:
            stmt = (
                select(Users.user_id)
                .where(
                    Users.is_delete == False,
                    Users.subscription_end_date.isnot(None),
                    cast(Users.subscription_end_date, Date).in_(targets),
                )
                .order_by(Users.user_id)
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_USER_IDS_NO_ACTIVE_PRO_SUBSCRIPTION(self) -> List[int]:
        """
        Не удалены; нет PRO-подписки (subscription_end_date пусто)
        или календарный день окончания (UTC) не позже чем 2 дня назад от сегодня UTC.
        """
        today_utc = datetime.now(timezone.utc).date()
        cutoff_utc = today_utc - timedelta(days=2)
        eligible = or_(
            Users.subscription_end_date.is_(None),
            cast(Users.subscription_end_date, Date) <= cutoff_utc,
        )
        async with self.session_factory() as session:
            stmt = (
                select(Users.user_id)
                .where(
                    Users.is_delete == False,
                    eligible,
                )
                .order_by(Users.user_id)
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_rows_for_subscription_expiry_push(
        self, now_utc_naive: datetime, window: timedelta
    ) -> List[Tuple[int, datetime, bool, Optional[str], Optional[str]]]:
        """
        Строки для sheduler.time_mes без N× get_user: user_id, subscription_end_date,
        in_panel (оплачивал / клава тарифа), ttclid, field_str_1 (JSON состояния push).

        Фильтр по времени (как _in_send_window в Python, moment <= now < moment + window):
        — Подписка активна: end попадает в одно из окон «за 7 / 3 / 1 день» или «за 1 час».
        — Подписка истекла: end попадает в окно second_chance (+7 дн) или post-expiry p1..p200 (+3n дн).
        """
        w = window
        now = now_utc_naive

        active_7 = and_(
            Users.subscription_end_date > now,
            Users.subscription_end_date > now + timedelta(days=7) - w,
            Users.subscription_end_date <= now + timedelta(days=7),
        )
        active_3 = and_(
            Users.subscription_end_date > now,
            Users.subscription_end_date > now + timedelta(days=3) - w,
            Users.subscription_end_date <= now + timedelta(days=3),
        )
        active_1 = and_(
            Users.subscription_end_date > now,
            Users.subscription_end_date > now + timedelta(days=1) - w,
            Users.subscription_end_date <= now + timedelta(days=1),
        )
        active_h = and_(
            Users.subscription_end_date > now,
            Users.subscription_end_date > now + timedelta(hours=1) - w,
            Users.subscription_end_date <= now + timedelta(hours=1),
        )
        active_cond = or_(active_7, active_3, active_1, active_h)

        post_second = and_(
            Users.subscription_end_date <= now,
            Users.subscription_end_date > now - timedelta(days=7) - w,
            Users.subscription_end_date <= now - timedelta(days=7),
        )
        post_pn = []
        for n in range(1, 201):
            d = timedelta(days=3 * n)
            post_pn.append(
                and_(
                    Users.subscription_end_date <= now,
                    Users.subscription_end_date > now - d - w,
                    Users.subscription_end_date <= now - d,
                )
            )
        expired_cond = or_(post_second, *post_pn)

        async with self.session_factory() as session:
            stmt = (
                select(
                    Users.user_id,
                    Users.subscription_end_date,
                    Users.in_panel,
                    Users.ttclid,
                    Users.field_str_1,
                )
                .where(
                    Users.is_delete == False,
                    Users.subscription_end_date.isnot(None),
                    or_(active_cond, expired_cond),
                )
                .order_by(Users.user_id)
            )
            result = await session.execute(stmt)
            rows = result.all()
            return [
                (r[0], r[1], bool(r[2]), r[3], r[4])
                for r in rows
            ]

    async def SELECT_NOT_CONNECTED_SUBSCRIBE_YES(self) -> List[int]:
        async with self.session_factory() as session:
            current_time = datetime.now()
            today = date.today()
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_connect == False,
                Users.is_delete == False,
                Users.subscription_end_date > current_time
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_NOT_CONNECTED_SUBSCRIBE_OFF(self):
        async with self.session_factory() as session:
            current_time = datetime.now()
            today = datetime.now().date()
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_connect == False,
                Users.is_delete == False,
                (Users.subscription_end_date < current_time) |
                (Users.subscription_end_date.is_(None))
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_CONNECTED_SUBSCRIBE_OFF(self):
        async with self.session_factory() as session:
            current_time = datetime.now()
            today = datetime.now().date()
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_connect == True,
                Users.is_delete == False,
                (Users.subscription_end_date < current_time) |
                (Users.subscription_end_date.is_(None))
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_CONNECTED_SUBSCRIBE_YES(self):
        async with self.session_factory() as session:
            current_time = datetime.now()
            today = datetime.now().date()
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_connect == True,
                Users.is_delete == False,
                Users.subscription_end_date > current_time,
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_NOT_SUBSCRIBED(self):
        async with self.session_factory() as session:
            today = datetime.now().date()
            stmt = select(Users.user_id).where(
                Users.in_panel == False,
                Users.is_connect == False,
                Users.is_delete == False,
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_CONNECTED_NEVER_PAID(self) -> List[int]:
        """
        Возвращает список user_id, у которых is_connect=True, is_delete=False,
        и нет ни одной успешной оплаты (статус 'confirmed' в Payments или PaymentsStars,
        или статус 'paid' в PaymentsCryptobot).
        """
        async with self.session_factory() as session:
            # Подзапрос: все пользователи с успешными платежами
            today = datetime.now().date()
            paid_subq = (
                select(Payments.user_id)
                .where(Payments.status == 'confirmed')
                .union(
                    select(PaymentsStars.user_id).where(PaymentsStars.status == 'confirmed'),
                    select(PaymentsCryptobot.user_id).where(PaymentsCryptobot.status == 'paid'),
                    select(PaymentsCards.user_id).where(PaymentsCards.status == 'confirmed'),
                    select(PaymentsPlategaCrypto.user_id).where(PaymentsPlategaCrypto.status == 'confirmed'),
                    select(PaymentsWataSBP.user_id).where(PaymentsWataSBP.status == 'confirmed'),
                    select(PaymentsWataCard.user_id).where(PaymentsWataCard.status == 'confirmed'),
                    select(PaymentsFkSBP.user_id).where(PaymentsFkSBP.status == 'confirmed'),
                )
                .subquery()
            )
            stmt = select(Users.user_id).where(
                Users.is_connect == True,
                Users.is_delete == False,
                Users.user_id.notin_(paid_subq)
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_SUBSCRIBED_NOT_IN_PANEL(self) -> List[int]:
        """
        Возвращает список user_id с подпиской в панели без subscription_end_date (синхронизация с X3).
        """
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.subscription_end_date == None,
                Users.is_delete == False
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_SUBSCRIBED(self) -> List[int]:
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.subscription_end_date != None,
                Users.is_delete == False
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    _FOREVER_PAYMENT_AMOUNTS = (3490, 4990, 2790)

    @staticmethod
    def _forever_payment_cond(table):
        """Платёж за тариф «Навсегда» (duration:5000 / 5000sale или типичные суммы)."""
        return or_(
            table.payload.like("%duration:5000%"),
            table.amount.in_(AsyncSQL._FOREVER_PAYMENT_AMOUNTS),
        )

    def _forever_payers_subquery(self):
        """user_id, хотя бы раз оплативших тариф «Навсегда»."""
        fc = self._forever_payment_cond
        return (
            select(Payments.user_id)
            .where(Payments.status == "confirmed", fc(Payments))
            .union(
                select(PaymentsStars.user_id).where(
                    PaymentsStars.status == "confirmed", fc(PaymentsStars)
                ),
                select(PaymentsCryptobot.user_id).where(
                    PaymentsCryptobot.status == "paid", fc(PaymentsCryptobot)
                ),
                select(PaymentsCards.user_id).where(
                    PaymentsCards.status == "confirmed", fc(PaymentsCards)
                ),
                select(PaymentsPlategaCrypto.user_id).where(
                    PaymentsPlategaCrypto.status == "confirmed", fc(PaymentsPlategaCrypto)
                ),
                select(PaymentsWataSBP.user_id).where(
                    PaymentsWataSBP.status == "confirmed", fc(PaymentsWataSBP)
                ),
                select(PaymentsWataCard.user_id).where(
                    PaymentsWataCard.status == "confirmed", fc(PaymentsWataCard)
                ),
                select(PaymentsFkSBP.user_id).where(
                    PaymentsFkSBP.status == "confirmed", fc(PaymentsFkSBP)
                ),
            )
            .subquery()
        )

    def _build_broadcast_where(self, category: str, exclude_today: bool):
        """
        Условие выборки пользователей для рассылки.
        exclude_today: только те, у кого last_broadcast_date пусто или дата (UTC) не сегодня.
        """
        current_time = datetime.now()
        today_d = datetime.now(timezone.utc).date()
        skip_today_cond = or_(
            Users.last_broadcast_date.is_(None),
            func.date(Users.last_broadcast_date) != today_d,
        )

        def wrap(base):
            return and_(base, skip_today_cond) if exclude_today else base

        if category == "all_users":
            return wrap(Users.is_delete == False)
        if category == "not_connected_subscribe_yes":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.is_connect == False,
                    Users.is_delete == False,
                    Users.subscription_end_date > current_time,
                )
            )
        if category == "not_connected_subscribe_off":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.is_connect == False,
                    Users.is_delete == False,
                    or_(
                        Users.subscription_end_date < current_time,
                        Users.subscription_end_date.is_(None),
                    ),
                )
            )
        if category == "connected_subscribe_off":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.is_connect == True,
                    Users.is_delete == False,
                    or_(
                        Users.subscription_end_date < current_time,
                        Users.subscription_end_date.is_(None),
                    ),
                )
            )
        if category == "connected_subscribe_yes":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.is_connect == True,
                    Users.is_delete == False,
                    Users.subscription_end_date > current_time,
                )
            )
        if category == "not_subscribed":
            return wrap(
                and_(
                    Users.in_panel == False,
                    Users.is_connect == False,
                    Users.is_delete == False,
                )
            )
        if category == "connected_never_paid":
            paid_subq = (
                select(Payments.user_id)
                .where(Payments.status == "confirmed")
                .union(
                    select(PaymentsStars.user_id).where(PaymentsStars.status == "confirmed"),
                    select(PaymentsCryptobot.user_id).where(PaymentsCryptobot.status == "paid"),
                    select(PaymentsCards.user_id).where(PaymentsCards.status == "confirmed"),
                    select(PaymentsPlategaCrypto.user_id).where(PaymentsPlategaCrypto.status == "confirmed"),
                    select(PaymentsWataSBP.user_id).where(PaymentsWataSBP.status == "confirmed"),
                    select(PaymentsWataCard.user_id).where(PaymentsWataCard.status == "confirmed"),
                    select(PaymentsFkSBP.user_id).where(PaymentsFkSBP.status == "confirmed"),
                )
                .subquery()
            )
            return wrap(
                and_(
                    Users.is_connect == True,
                    Users.is_delete == False,
                    Users.user_id.notin_(paid_subq),
                )
            )
        if category == "subscribed_all":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.subscription_end_date != None,
                    Users.is_delete == False,
                )
            )
        if category == "never_bought_forever":
            from wl_traffic.constants import FOREVER_END_CUTOFF

            forever_paid = self._forever_payers_subquery()
            return wrap(
                and_(
                    Users.is_delete == False,
                    Users.user_id.notin_(forever_paid),
                    or_(
                        Users.subscription_end_date.is_(None),
                        Users.subscription_end_date < FOREVER_END_CUTOFF,
                    ),
                )
            )
        return None

    async def count_users_for_broadcast(self, category: str, exclude_today: bool) -> int:
        where_clause = self._build_broadcast_where(category, exclude_today)
        if where_clause is None:
            return 0
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(Users).where(where_clause)
            return int((await session.execute(stmt)).scalar_one())

    async def select_user_ids_for_broadcast(self, category: str, exclude_today: bool) -> List[int]:
        where_clause = self._build_broadcast_where(category, exclude_today)
        if where_clause is None:
            return []
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(where_clause)
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def SELECT_USERS_BY_PARAMETER(self, parameter: str, value: str) -> List[int]:
        """
        Возвращает список user_id, у которых значение указанного параметра равно value.
        Допустимые параметры: 'Ref', 'in_panel', 'Is_pay_null' (синоним in_panel), 'stamp'.
        """
        param_map = {
            'Ref': Users.ref,
            'in_panel': Users.in_panel,
            'Is_pay_null': Users.in_panel,
            'stamp': Users.stamp,
        }
        if parameter not in param_map:
            logger.info(f"Invalid parameter: {parameter}")
            return []

        attr = param_map[parameter]

        if parameter in ('in_panel', 'Is_pay_null'):
            try:
                val = bool(int(value))
            except ValueError:
                logger.error(f"Invalid value type for parameter {parameter}: {value}")
                return []
        else:
            val = value

        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(attr == val)
            result = await session.execute(stmt)
            rows = result.all()
            logger.info(f"Query result for parameter '{parameter}' with value '{value}': {len(rows)}")
            return [row[0] for row in rows]

    async def get_stat_by_ref_or_stamp(self, arg: str) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int], Optional[int], Optional[str]]:
        """
        Возвращает статистику по пользователям, у которых Ref == arg,
        если таких нет – по пользователям с stamp == arg.
        total_payments — сумма подтверждённых платежей: Payments + WATA СБП + WATA карта + FreeKassa.
        Возвращает (total, with_sub, with_tarif, with_tarif_not_blocked, total_payments, source)
        или (None, None, None, None, None, None) если нет совпадений.
        """
        # 1. Ищем по Ref
        users = await self.SELECT_USERS_BY_PARAMETER('Ref', arg)
        source = 'ref'
        if not users:
            # 2. Ищем по stamp
            users = await self.SELECT_USERS_BY_PARAMETER('stamp', arg)
            source = 'stamp'

        if not users:
            return None, None, None, None, None, None

        total = len(users)
        with_sub = 0
        with_tarif = 0
        with_tarif_not_blocked = 0
        total_payments = 0

        async with self.session_factory() as session:
            for i in range(0, len(users), _STAT_IN_CHUNK):
                chunk = users[i : i + _STAT_IN_CHUNK]
                stmt_users = select(
                    Users.subscription_end_date,
                    Users.is_connect,
                    Users.is_delete,
                ).where(Users.user_id.in_(chunk))
                result = await session.execute(stmt_users)
                for sub_end, is_connect, is_delete in result.all():
                    if sub_end is not None:
                        with_sub += 1
                    if is_connect:
                        with_tarif += 1
                    if is_connect and not is_delete:
                        with_tarif_not_blocked += 1

            with_tarif //= 2
            with_tarif_not_blocked //= 2

            for i in range(0, len(users), _STAT_IN_CHUNK):
                chunk = users[i : i + _STAT_IN_CHUNK]
                stmt_pay = select(func.coalesce(func.sum(Payments.amount), 0)).where(
                    Payments.user_id.in_(chunk),
                    Payments.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_pay)).scalar() or 0
                stmt_wata_sbp = select(func.coalesce(func.sum(PaymentsWataSBP.amount), 0)).where(
                    PaymentsWataSBP.user_id.in_(chunk),
                    PaymentsWataSBP.status == 'confirmed',
                )
                wata_sbp = total_payments
                total_payments += (await session.execute(stmt_wata_sbp)).scalar() or 0
                stmt_wata_card = select(func.coalesce(func.sum(PaymentsWataCard.amount), 0)).where(
                    PaymentsWataCard.user_id.in_(chunk),
                    PaymentsWataCard.status == 'confirmed',
                )
                wata_card = total_payments - wata_sbp
                total_payments += (await session.execute(stmt_wata_card)).scalar() or 0
                stmt_fk_sbp = select(func.coalesce(func.sum(PaymentsFkSBP.amount), 0)).where(
                    PaymentsFkSBP.user_id.in_(chunk),
                    PaymentsFkSBP.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_fk_sbp)).scalar() or 0

                fk_sbp = total_payments - wata_sbp - wata_card

        return total, with_sub, with_tarif, with_tarif_not_blocked, total_payments, source, wata_sbp, wata_card, fk_sbp

    def GET_AVAILABLE_PARAMETERS(self) -> List[str]:
        """Возвращает список доступных параметров для фильтрации пользователей."""
        return [
            'not_connected_subscribe_yes',
            'not_connected_subscribe_off',
            'connected_subscribe_off',
            'connected_subscribe_yes',
            'not_subscribed',
            'connected_never_paid',
            'subscribed_all',
            'all_users'
        ]

    async def delete_from_db(self, user_id: int) -> bool:
        """Полностью удаляет пользователя из БД по user_id."""
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                logger.warning(f"User {user_id} not found for deletion")
                return False
            await session.delete(user)
            await session.commit()
            logger.info(f"✅ Удалено пользователей: 1 (User_id: {user_id})")
            return True

    async def list_users_by_stamps(self, stamps: List[str]) -> List[Dict[str, Any]]:
        """Снимок пользователей с stamp из списка (для превью и отчёта)."""
        if not stamps:
            return []
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.stamp.in_(stamps)).order_by(Users.stamp, Users.id)
            result = await session.execute(stmt)
            return [self._user_delete_snapshot(u) for u in result.scalars().all()]

    async def delete_users_by_stamps(self, stamps: List[str]) -> List[Dict[str, Any]]:
        """
        Удаляет пользователей с stamp из списка.
        Возвращает снимок удалённых строк. Связанные linking/reset-коды тоже чистятся.
        """
        if not stamps:
            return []
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.stamp.in_(stamps)).order_by(Users.stamp, Users.id)
            result = await session.execute(stmt)
            users = list(result.scalars().all())
            if not users:
                return []

            snapshots = [self._user_delete_snapshot(u) for u in users]
            internal_ids = [u.id for u in users]
            emails = [u.email for u in users if u.email]

            for i in range(0, len(internal_ids), _STAT_IN_CHUNK):
                chunk = internal_ids[i : i + _STAT_IN_CHUNK]
                await session.execute(delete(LinkingCodes).where(LinkingCodes.user_id.in_(chunk)))
                await session.execute(delete(Users).where(Users.id.in_(chunk)))
            for i in range(0, len(emails), _STAT_IN_CHUNK):
                await session.execute(
                    delete(PasswordResetCodes).where(
                        PasswordResetCodes.email.in_(emails[i : i + _STAT_IN_CHUNK])
                    )
                )
            await session.commit()

            logger.info(f"✅ Удалено пользователей: {len(snapshots)} (stamps={stamps})")
            return snapshots

    @staticmethod
    def _user_delete_snapshot(user: Users) -> Dict[str, Any]:
        return {
            "id": user.id,
            "user_id": user.user_id,
            "stamp": user.stamp,
            "email": user.email,
            "ref": user.ref,
            "partner": user.partner,
            "create_user": user.create_user,
            "subscription_end_date": user.subscription_end_date,
            "white_subscription_end_date": user.white_subscription_end_date,
            "in_panel": user.in_panel,
            "is_connect": user.is_connect,
            "is_delete": user.is_delete,
        }

    async def reset_all_delete_flag(self) -> int:
        """Устанавливает Is_delete = False для всех записей в таблице users."""
        async with self.session_factory() as session:
            stmt = update(Users).values(is_delete=False)
            result = await session.execute(stmt)
            await session.commit()
            updated = result.rowcount
            logger.info(f"✅ Сброшен флаг Is_delete для {updated} пользователей")
            return updated

    async def get_users_with_confirmed_payments(self, user_ids: Optional[List[int]] = None) -> List[int]:
        """
        Возвращает список user_id, у которых есть хотя бы один платёж со статусом 'confirmed'.
        Если передан список user_ids, возвращаются только те, кто есть в этом списке.
        """
        async with self.session_factory() as session:
            stmt = select(Payments.user_id).where(Payments.status == 'confirmed').distinct()
            if user_ids:
                stmt = stmt.where(Payments.user_id.in_(user_ids))
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def get_payment_stats_by_period(self, start_date: datetime, end_date: datetime) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        Возвращает статистику платежей за период по группам ref и stamp.
        Для каждого платежа с суммой != 1, статус 'confirmed', дата между start_date и end_date включительно,
        находим пользователя и добавляем сумму в группы ref и stamp (если они заданы).
        Возвращает два словаря: ref_totals, stamp_totals.
        """
        # Приводим даты к началу и концу суток для включительности
        start = datetime.combine(start_date.date(), datetime.min.time())
        end = datetime.combine(end_date.date(), datetime.max.time())

        async with self.session_factory() as session:
            # Получаем платежи за период, исключая сумму 1
            stmt_payments = select(
                Payments.user_id,
                Payments.amount
            ).where(
                Payments.status == 'confirmed',
                Payments.amount != 1,
                Payments.time_created.between(start, end)
            )
            payments_result = await session.execute(stmt_payments)
            payments_data = payments_result.all()

            if not payments_data:
                return {}, {}

            # Собираем уникальные user_id из платежей
            user_ids = list(set(p[0] for p in payments_data))

            # Получаем данные всех этих пользователей одним запросом
            stmt_users = select(
                Users.user_id,
                Users.ref,
                Users.stamp
            ).where(Users.user_id.in_(user_ids))
            users_result = await session.execute(stmt_users)
            users_data = users_result.all()

        # Словарь для быстрого поиска ref и stamp по user_id
        user_map = {u[0]: (u[1], u[2]) for u in users_data}

        ref_totals = {}
        stamp_totals = {}

        for user_id, amount in payments_data:
            ref, stamp = user_map.get(user_id, (None, None))
            if ref:
                ref_totals[ref] = ref_totals.get(ref, 0) + amount
            if stamp:
                stamp_totals[stamp] = stamp_totals.get(stamp, 0) + amount

        return ref_totals, stamp_totals

    async def update_broadcast_status(self, user_id: int, status: str) -> None:
        """
        Обновляет статус последней рассылки и дату для указанного пользователя.
        """
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(
                last_broadcast_status=status,
                last_broadcast_date=_naive_utc(datetime.now(timezone.utc)),
            )
            try:
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating broadcast status for user {user_id}: {e}")

    async def get_gift(self, gift_id: str) -> Optional[Gifts]:
        """Возвращает запись подарка или None."""
        async with self.session_factory() as session:
            result = await session.execute(select(Gifts).where(Gifts.gift_id == gift_id))
            return result.scalar_one_or_none()

    async def next_gift_number(self) -> int:
        """Следующий порядковый номер для panel username gift_N."""
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(Users).where(Users.stamp == "gift")
            result = await session.execute(stmt)
            return int(result.scalar_one() or 0) + 1

    async def create_gift_web_user(self, gift_id: str, gift_num: int) -> int:
        """Создаёт пользователя для веб-активации подарка. Возвращает user_id (отрицательный)."""
        panel_username = f"gift_{gift_num}"
        uid = await self.next_negative_user_id()
        async with self.session_factory() as session:
            u = Users(
                user_id=uid,
                stamp="gift",
                field_str_1=gift_id,
                field_str_2=panel_username,
                in_panel=False,
                create_user=_naive_utc(datetime.now(timezone.utc)),
            )
            session.add(u)
            await session.commit()
        return uid

    async def activate_gift(self, gift_id: str, recipient_id: int) -> Tuple[bool, Optional[int], Optional[bool]]:
        """
        Активирует подарок по gift_id для указанного получателя.
        Возвращает (успех, duration, white_flag) или (False, None, None) если подарок не найден или уже активирован.
        """
        async with self.session_factory() as session:
            # Проверяем существование и статус подарка
            stmt = select(Gifts).where(
                Gifts.gift_id == gift_id,
                Gifts.flag == False,
                Gifts.recepient_id == None
            )
            result = await session.execute(stmt)
            gift = result.scalar_one_or_none()

            if not gift:
                logger.warning(f"Gift {gift_id} not found or already activated")
                return False, None, None

            # Активируем подарок
            gift.flag = True
            gift.recepient_id = recipient_id
            try:
                await session.commit()
                logger.info(f"Gift {gift_id} activated for user {recipient_id}")
                return True, gift.duration, gift.white_flag
            except Exception as e:
                await session.rollback()
                logger.error(f"Error activating gift {gift_id} for user {recipient_id}: {e}")
                return False, None, None

    async def get_pending_platega_payments(self) -> List[Payments]:
        """Возвращает все платежи из таблицы payments со статусом 'pending'."""
        async with self.session_factory() as session:
            stmt = select(Payments).where(Payments.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_pending_platega_card_payments(self) -> List[PaymentsCards]:
        """Возвращает все платежи из таблицы payments со статусом 'pending'."""
        async with self.session_factory() as session:
            stmt = select(PaymentsCards).where(PaymentsCards.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_pending_platega_crypto_payments(self) -> List[PaymentsPlategaCrypto]:
        """Возвращает все платежи из таблицы payments со статусом 'pending'."""
        async with self.session_factory() as session:
            stmt = select(PaymentsPlategaCrypto).where(PaymentsPlategaCrypto.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def update_payment_status(self, transaction_id: str, new_status: str) -> None:
        """Обновляет статус платежа по transaction_id."""
        async with self.session_factory() as session:
            stmt = update(Payments).where(Payments.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def update_payment_card_status(self, transaction_id: str, new_status: str) -> None:
        """Обновляет статус платежа по transaction_id."""
        async with self.session_factory() as session:
            stmt = update(PaymentsCards).where(PaymentsCards.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def update_platega_connect_panel(
        self,
        transaction_id: str,
        value: bool,
        *,
        is_card: bool,
    ) -> None:
        """connect_panel у платежа Platega SBP (payments) или карты (payments_cards)."""
        model = PaymentsCards if is_card else Payments
        async with self.session_factory() as session:
            stmt = (
                update(model)
                .where(model.transaction_id == transaction_id)
                .values(connect_panel=value)
            )
            await session.execute(stmt)
            await session.commit()

    async def update_payment_platega_crypto_status(self, transaction_id: str, new_status: str) -> None:
        """Обновляет статус платежа по transaction_id."""
        async with self.session_factory() as session:
            stmt = update(PaymentsPlategaCrypto).where(PaymentsPlategaCrypto.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def alloc_fk_api_nonce(self) -> int:
        """Уникальный растущий nonce для FreeKassa API без отдельной таблицы."""
        return time.time_ns() // 1000

    async def get_pending_fk_sbp_payments(self) -> List[PaymentsFkSBP]:
        async with self.session_factory() as session:
            stmt = select(PaymentsFkSBP).where(PaymentsFkSBP.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_fk_sbp_payments_for_date(self, day: date) -> List[PaymentsFkSBP]:
        day_start = datetime.combine(day, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        async with self.session_factory() as session:
            stmt = (
                select(PaymentsFkSBP)
                .where(
                    PaymentsFkSBP.time_created >= day_start,
                    PaymentsFkSBP.time_created < day_end,
                )
                .order_by(PaymentsFkSBP.time_created)
            )
            result = await session.execute(stmt)
            return result.scalars().all()

    async def update_fk_sbp_payment_status(self, transaction_id: str, new_status: str) -> None:
        async with self.session_factory() as session:
            stmt = update(PaymentsFkSBP).where(
                PaymentsFkSBP.transaction_id == transaction_id
            ).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def add_fk_sbp_payment(
            self,
            user_id: int,
            amount: int,
            status: str,
            transaction_id: str,
            fk_order_id: Optional[int],
            payload: str,
            nonce: int,
            signature: str,
            is_gift: bool = False,
            method: str = 'fk_qr_card',
    ) -> None:
        async with self.session_factory() as session:
            payment = PaymentsFkSBP(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                fk_order_id=fk_order_id,
                payload=payload,
                nonce=nonce,
                signature=signature,
                method=method,
                is_gift=is_gift,
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(
                    f"💰 Платёж FreeKassa записан: user_id={user_id}, amount={amount}, is_gift={is_gift}, method={method}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа FreeKassa: {e}")
                raise

    async def get_pending_wata_sbp_payments(self) -> List[PaymentsWataSBP]:
        async with self.session_factory() as session:
            stmt = select(PaymentsWataSBP).where(PaymentsWataSBP.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def count_pending_wata_sbp(self) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(PaymentsWataSBP).where(PaymentsWataSBP.status == "pending")
            return int((await session.execute(stmt)).scalar_one())

    async def count_pending_wata_card(self) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(PaymentsWataCard).where(PaymentsWataCard.status == "pending")
            return int((await session.execute(stmt)).scalar_one())

    async def count_open_payment_slots_for_user(self, user_id: int) -> int:
        """
        Незавершённые попытки оплаты у пользователя (все каналы вместе):
        WATA pending, FreeKassa pending, Platega pending, Cryptobot неоплаченный счёт (active / устаревший pending в БД).
        """
        uid = int(user_id)
        async with self.session_factory() as session:
            total = 0
            pairs = (
                (
                    PaymentsWataSBP,
                    and_(PaymentsWataSBP.user_id == uid, PaymentsWataSBP.status == "pending"),
                ),
                (
                    PaymentsWataCard,
                    and_(PaymentsWataCard.user_id == uid, PaymentsWataCard.status == "pending"),
                ),
                (
                    PaymentsCryptobot,
                    and_(
                        PaymentsCryptobot.user_id == uid,
                        or_(
                            PaymentsCryptobot.status == "active",
                            PaymentsCryptobot.status == "pending",
                        ),
                    ),
                ),
                (Payments, and_(Payments.user_id == uid, Payments.status == "pending")),
                (PaymentsCards, and_(PaymentsCards.user_id == uid, PaymentsCards.status == "pending")),
                (
                    PaymentsPlategaCrypto,
                    and_(PaymentsPlategaCrypto.user_id == uid, PaymentsPlategaCrypto.status == "pending"),
                ),
                (
                    PaymentsFkSBP,
                    and_(PaymentsFkSBP.user_id == uid, PaymentsFkSBP.status == "pending"),
                ),
            )
            for model, cond in pairs:
                q = select(func.count()).select_from(model).where(cond)
                total += int((await session.execute(q)).scalar_one())
        return total

    async def get_pending_wata_card_payments(self) -> List[PaymentsWataCard]:
        async with self.session_factory() as session:
            stmt = select(PaymentsWataCard).where(PaymentsWataCard.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_pending_wata_sbp_payments_polled(
        self,
        recent_hours: int = 72,
        recent_limit: int = 100,
        stale_limit: int = 50,
    ) -> List[PaymentsWataSBP]:
        """
        Очередь для cron: свежие (недавние) — чтобы оплаты подтверждались быстро;
        плюс небольшая порция самых старых — чтобы не копился хвост.
        Без этого один проход по всем pending занимает минуты и APScheduler пропускает тики.
        """
        cutoff = datetime.now() - timedelta(hours=recent_hours)
        async with self.session_factory() as session:
            q_recent = (
                select(PaymentsWataSBP)
                .where(PaymentsWataSBP.status == "pending", PaymentsWataSBP.time_created >= cutoff)
                .order_by(PaymentsWataSBP.time_created.desc())
                .limit(recent_limit)
            )
            q_stale = (
                select(PaymentsWataSBP)
                .where(PaymentsWataSBP.status == "pending", PaymentsWataSBP.time_created < cutoff)
                .order_by(PaymentsWataSBP.time_created.asc())
                .limit(stale_limit)
            )
            r1 = (await session.execute(q_recent)).scalars().all()
            r2 = (await session.execute(q_stale)).scalars().all()
        seen: set[int] = set()
        out: List[PaymentsWataSBP] = []
        for p in (*r1, *r2):
            if p.id in seen:
                continue
            seen.add(p.id)
            out.append(p)
        return out

    async def get_pending_wata_card_payments_polled(
        self,
        recent_hours: int = 72,
        recent_limit: int = 100,
        stale_limit: int = 50,
    ) -> List[PaymentsWataCard]:
        cutoff = datetime.now() - timedelta(hours=recent_hours)
        async with self.session_factory() as session:
            q_recent = (
                select(PaymentsWataCard)
                .where(PaymentsWataCard.status == "pending", PaymentsWataCard.time_created >= cutoff)
                .order_by(PaymentsWataCard.time_created.desc())
                .limit(recent_limit)
            )
            q_stale = (
                select(PaymentsWataCard)
                .where(PaymentsWataCard.status == "pending", PaymentsWataCard.time_created < cutoff)
                .order_by(PaymentsWataCard.time_created.asc())
                .limit(stale_limit)
            )
            r1 = (await session.execute(q_recent)).scalars().all()
            r2 = (await session.execute(q_stale)).scalars().all()
        seen: set[int] = set()
        out: List[PaymentsWataCard] = []
        for p in (*r1, *r2):
            if p.id in seen:
                continue
            seen.add(p.id)
            out.append(p)
        return out

    async def update_wata_sbp_status(self, transaction_id: str, new_status: str) -> None:
        async with self.session_factory() as session:
            stmt = update(PaymentsWataSBP).where(PaymentsWataSBP.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def update_wata_card_status(self, transaction_id: str, new_status: str) -> None:
        async with self.session_factory() as session:
            stmt = update(PaymentsWataCard).where(PaymentsWataCard.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def add_wata_sbp_payment(
        self, user_id: int, amount: int, status: str, transaction_id: str, payload: str, is_gift: bool = False
    ) -> None:
        async with self.session_factory() as session:
            payment = PaymentsWataSBP(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift,
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж WATA СБП записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа WATA СБП: {e}")
                raise

    async def add_wata_card_payment(
        self, user_id: int, amount: int, status: str, transaction_id: str, payload: str, is_gift: bool = False
    ) -> None:
        async with self.session_factory() as session:
            payment = PaymentsWataCard(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift,
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж WATA Карта записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа WATA Карта: {e}")
                raise

    async def get_payment_by_transaction_id(self, transaction_id: str, user_id: int) -> Optional[str]:
        """Статус Platega-платежа (SBP / card / crypto), только если transaction принадлежит user_id."""
        async with self.session_factory() as session:
            for model in (Payments, PaymentsCards, PaymentsPlategaCrypto, PaymentsWataSBP, PaymentsWataCard, PaymentsFkSBP):
                stmt = select(model).where(
                    model.transaction_id == transaction_id,
                    model.user_id == user_id,
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is not None:
                    return row.status
        return None

    async def get_active_cryptobot_payments(self) -> List[PaymentsCryptobot]:
        """
        Возвращает все платежи Cryptobot со статусом 'active'.
        """
        async with self.session_factory() as session:
            stmt = select(PaymentsCryptobot).where(PaymentsCryptobot.status == 'active')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def update_cryptobot_payment_status(self, payment_id: int, status: str) -> None:
        """
        Обновляет статус платежа Cryptobot.
        """
        async with self.session_factory() as session:
            stmt = update(PaymentsCryptobot).where(PaymentsCryptobot.id == payment_id).values(status=status)
            await session.execute(stmt)
            await session.commit()

    async def add_payment_stars(self, user_id: int, amount: int, payload: str, is_gift: bool) -> None:
        """Добавляет запись в таблицу payments_stars."""
        async with self.session_factory() as session:
            payment = PaymentsStars(
                user_id=user_id,
                amount=amount,
                payload=payload,
                is_gift=is_gift,
                status='confirmed'
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(
                    f"💰 Платёж Telegram Stars записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа Telegram Stars: {e}")

    async def create_gift(self, giver_id: int, duration: int, white_flag: bool) -> str:
        """Создаёт запись о подарке и возвращает gift_id."""
        gift_id = str(uuid.uuid4())
        async with self.session_factory() as session:
            gift = Gifts(
                gift_id=gift_id,
                giver_id=giver_id,
                duration=duration,
                recepient_id=None,
                white_flag=white_flag,
                flag=False
            )
            session.add(gift)
            try:
                await session.commit()
                logger.info(f"✅ Запись о подарке создана: gift_id={gift_id}")
                return gift_id
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка создания подарка: {e}")
                raise

    async def add_online_stats(self, users_panel: int, users_active: int, users_pay: int, users_trial: int) -> None:
        """
        Сохраняет ежедневную статистику онлайн-активности.
        """
        async with self.session_factory() as session:
            online_record = Online(
                users_panel=users_panel,
                users_active=users_active,
                users_pay=users_pay,
                users_trial=users_trial
            )
            session.add(online_record)
            await session.commit()

    async def add_platega_payment(self, user_id: int, amount: int, status: str, transaction_id: str, payload: str,
                                  is_gift: bool = False) -> None:
        """
        Записывает платёж Platega в таблицу payments.
        """
        async with self.session_factory() as session:
            payment = Payments(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift,
                connect_panel=False,
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж Platega SBP записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа Platega: {e}")
                raise

    async def add_platega_card_payment(self, user_id: int, amount: int, status: str, transaction_id: str, payload: str,
                                       is_gift: bool = False) -> None:
        """
        Записывает платёж PlategaCard в таблицу payments.
        """
        async with self.session_factory() as session:
            payment = PaymentsCards(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift,
                connect_panel=False,
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж Platega Card записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа Platega: {e}")
                raise

    async def add_platega_crypto_payment(self, user_id: int, amount: int, status: str, transaction_id: str, payload: str,
                                       is_gift: bool = False) -> None:
        """
        Записывает платёж PlategaCard в таблицу payments.
        """
        async with self.session_factory() as session:
            payment = PaymentsPlategaCrypto(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж Platega Crypto записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа Platega: {e}")
                raise

    async def add_cryptobot_payment(self, user_id: int, amount: float, currency: str, is_gift: bool, invoice_id: str,
                                    payload: str) -> None:
        """
        Запись платежа Cryptobot в таблицу payments_cryptobot.
        """
        async with self.session_factory() as session:
            payment = PaymentsCryptobot(
                user_id=user_id,
                amount=amount,
                currency=currency,
                is_gift=is_gift,
                status='active',
                invoice_id=invoice_id,
                payload=payload
            )
            session.add(payment)
            await session.commit()
            logger.info(f"Cryptobot invoice created: {invoice_id} for user {user_id}")

    async def get_all_users(self) -> List[Users]:
        """Возвращает список всех пользователей."""
        async with self.session_factory() as session:
            result = await session.execute(select(Users))
            return result.scalars().all()

    async def get_all_payments(self) -> List[Payments]:
        """Возвращает список всех платежей Platega."""
        async with self.session_factory() as session:
            result = await session.execute(select(Payments))
            return result.scalars().all()

    async def get_all_payments_cards(self) -> List[PaymentsCards]:
        """Возвращает список всех платежей по картам (PaymentsCards)."""
        async with self.session_factory() as session:
            result = await session.execute(select(PaymentsCards))
            return result.scalars().all()

    async def get_all_payments_platega_crypto(self) -> List[PaymentsPlategaCrypto]:
        async with self.session_factory() as session:
            result = await session.execute(select(PaymentsPlategaCrypto))
            return result.scalars().all()

    async def get_all_payments_stars(self) -> List[PaymentsStars]:
        """Возвращает список всех платежей Telegram Stars."""
        async with self.session_factory() as session:
            result = await session.execute(select(PaymentsStars))
            return result.scalars().all()

    async def get_all_payments_cryptobot(self) -> List[PaymentsCryptobot]:
        """Возвращает список всех крипто-платежей."""
        async with self.session_factory() as session:
            result = await session.execute(select(PaymentsCryptobot))
            return result.scalars().all()

    async def get_regular_subscription_payment_events(self) -> List[Tuple[int, datetime, int]]:
        """
        Успешные оплаты обычной подписки (не «Включи мобильный интернет» / white), не подарок.
        Статус: confirmed или paid. Длительность из payload; если нет (старые записи) — по сумме из dct_price.
        Исключаются тестовые суммы 1 (админы). Возвращает (user_id, time_created UTC naive, duration_days).
        """
        rows: List[Tuple[int, datetime, int]] = []

        def _parse_payload_map(payload: Optional[str]) -> dict[str, str]:
            if not payload:
                return {}
            out: dict[str, str] = {}
            for part in payload.split(','):
                if ':' not in part:
                    continue
                k, _, v = part.partition(':')
                out[k.strip()] = v.strip()
            return out

        def _include(payload: Optional[str], is_gift: bool, amount: Any) -> Optional[int]:
            if is_gift:
                return None
            try:
                amt_f = float(amount)
                if amt_f == 1.0:
                    return None
            except (TypeError, ValueError):
                return None
            m = _parse_payload_map(payload)
            if m.get("white", "False").lower() == "true":
                return None
            if m.get("gift", "False").lower() == "true":
                return None
            d = _payload_duration_to_panel_days(m.get("duration"))
            if d is not None:
                return d
            return _billing_duration_from_amount_fallback(amt_f)

        async with self.session_factory() as session:
            q1 = select(
                Payments.user_id, Payments.time_created, Payments.amount, Payments.payload, Payments.is_gift
            ).where(Payments.status.in_(_BILLING_OK_STATUSES), Payments.is_gift == False)
            for uid, tc, amt, pl, ig in (await session.execute(q1)).all():
                d = _include(pl, ig, amt)
                if d is not None:
                    rows.append((uid, tc, d))

            q2 = select(
                PaymentsCards.user_id,
                PaymentsCards.time_created,
                PaymentsCards.amount,
                PaymentsCards.payload,
                PaymentsCards.is_gift,
            ).where(PaymentsCards.status.in_(_BILLING_OK_STATUSES), PaymentsCards.is_gift == False)
            for uid, tc, amt, pl, ig in (await session.execute(q2)).all():
                d = _include(pl, ig, amt)
                if d is not None:
                    rows.append((uid, tc, d))

            q3 = select(
                PaymentsPlategaCrypto.user_id,
                PaymentsPlategaCrypto.time_created,
                PaymentsPlategaCrypto.amount,
                PaymentsPlategaCrypto.payload,
                PaymentsPlategaCrypto.is_gift,
            ).where(
                PaymentsPlategaCrypto.status.in_(_BILLING_OK_STATUSES),
                PaymentsPlategaCrypto.is_gift == False,
            )
            for uid, tc, amt, pl, ig in (await session.execute(q3)).all():
                d = _include(pl, ig, amt)
                if d is not None:
                    rows.append((uid, tc, d))

            q4 = select(
                PaymentsStars.user_id,
                PaymentsStars.time_created,
                PaymentsStars.amount,
                PaymentsStars.payload,
                PaymentsStars.is_gift,
            ).where(PaymentsStars.status.in_(_BILLING_OK_STATUSES), PaymentsStars.is_gift == False)
            for uid, tc, amt, pl, ig in (await session.execute(q4)).all():
                d = _include(pl, ig, amt)
                if d is not None:
                    rows.append((uid, tc, d))

            q5 = select(
                PaymentsCryptobot.user_id,
                PaymentsCryptobot.time_created,
                PaymentsCryptobot.amount,
                PaymentsCryptobot.payload,
                PaymentsCryptobot.is_gift,
            ).where(PaymentsCryptobot.status.in_(_BILLING_OK_STATUSES), PaymentsCryptobot.is_gift == False)
            for uid, tc, amt, pl, ig in (await session.execute(q5)).all():
                d = _include(pl, ig, amt)
                if d is not None:
                    rows.append((uid, tc, d))

            q6 = select(
                PaymentsWataSBP.user_id,
                PaymentsWataSBP.time_created,
                PaymentsWataSBP.amount,
                PaymentsWataSBP.payload,
                PaymentsWataSBP.is_gift,
            ).where(PaymentsWataSBP.status.in_(_BILLING_OK_STATUSES), PaymentsWataSBP.is_gift == False)
            for uid, tc, amt, pl, ig in (await session.execute(q6)).all():
                d = _include(pl, ig, amt)
                if d is not None:
                    rows.append((uid, tc, d))

            q7 = select(
                PaymentsWataCard.user_id,
                PaymentsWataCard.time_created,
                PaymentsWataCard.amount,
                PaymentsWataCard.payload,
                PaymentsWataCard.is_gift,
            ).where(PaymentsWataCard.status.in_(_BILLING_OK_STATUSES), PaymentsWataCard.is_gift == False)
            for uid, tc, amt, pl, ig in (await session.execute(q7)).all():
                d = _include(pl, ig, amt)
                if d is not None:
                    rows.append((uid, tc, d))

            q8 = select(
                PaymentsFkSBP.user_id,
                PaymentsFkSBP.time_created,
                PaymentsFkSBP.amount,
                PaymentsFkSBP.payload,
                PaymentsFkSBP.is_gift,
            ).where(PaymentsFkSBP.status.in_(_BILLING_OK_STATUSES), PaymentsFkSBP.is_gift == False)
            for uid, tc, amt, pl, ig in (await session.execute(q8)).all():
                d = _include(pl, ig, amt)
                if d is not None:
                    rows.append((uid, tc, d))

        rows.sort(key=lambda x: (x[1], x[0]))
        return rows

    async def get_all_gifts(self) -> List[Gifts]:
        """Возвращает список всех подарков."""
        async with self.session_factory() as session:
            result = await session.execute(select(Gifts))
            return result.scalars().all()

    async def get_all_online(self) -> List[Online]:
        """Возвращает список всех записей онлайн-статистики."""
        async with self.session_factory() as session:
            result = await session.execute(select(Online))
            return result.scalars().all()

    async def get_all_white_counter(self) -> List[WhiteCounter]:
        """Возвращает список всех записей white_counter."""
        async with self.session_factory() as session:
            result = await session.execute(select(WhiteCounter))
            return result.scalars().all()

    async def get_export_snapshot(self, *, include_users: bool = True) -> Dict[str, List[Any]]:
        """
        Одна сессия БД: все SELECT для /export подряд.
        Меньше открытий соединения, чем несколько отдельных get_all_*.
        При include_users=False таблица users не читается (/export_fast).
        """
        async with self.session_factory() as session:
            users_list: List[Any] = []
            if include_users:
                users_list = (
                    await session.execute(select(Users).order_by(Users.create_user.asc()))
                ).scalars().all()
            payments_list = (await session.execute(select(Payments))).scalars().all()
            payments_cards_list = (await session.execute(select(PaymentsCards))).scalars().all()
            payments_platega_crypto_list = (await session.execute(select(PaymentsPlategaCrypto))).scalars().all()
            payments_stars_list = (await session.execute(select(PaymentsStars))).scalars().all()
            payments_cryptobot_list = (await session.execute(select(PaymentsCryptobot))).scalars().all()
            payments_wata_sbp_list = (await session.execute(select(PaymentsWataSBP))).scalars().all()
            payments_wata_card_list = (await session.execute(select(PaymentsWataCard))).scalars().all()
            payments_fk_sbp_list = (await session.execute(select(PaymentsFkSBP))).scalars().all()
            gifts_list = (await session.execute(select(Gifts))).scalars().all()
            online_list = (await session.execute(select(Online))).scalars().all()
            white_counter_list = (await session.execute(select(WhiteCounter))).scalars().all()
        return {
            "users": users_list,
            "payments": payments_list,
            "payments_cards": payments_cards_list,
            "payments_platega_crypto": payments_platega_crypto_list,
            "payments_stars": payments_stars_list,
            "payments_cryptobot": payments_cryptobot_list,
            "payments_wata_sbp": payments_wata_sbp_list,
            "payments_wata_card": payments_wata_card_list,
            "payments_fk_sbp": payments_fk_sbp_list,
            "gifts": gifts_list,
            "online": online_list,
            "white_counter": white_counter_list,
        }

    async def import_replace_all_from_export_workbook(
        self,
        *,
        users: List[Users],
        payments: List[Payments],
        payments_cards: List[PaymentsCards],
        payments_platega_crypto: List[PaymentsPlategaCrypto],
        payments_stars: List[PaymentsStars],
        payments_cryptobot: List[PaymentsCryptobot],
        payments_wata_sbp: List[PaymentsWataSBP],
        payments_wata_card: List[PaymentsWataCard],
        payments_fk_sbp: List[PaymentsFkSBP],
        gifts: List[Gifts],
        online: List[Online],
        white_counter: List[WhiteCounter],
    ) -> Dict[str, int]:
        """
        Полная замена данных таблиц, которые попадают в /export_partner: TRUNCATE … RESTART IDENTITY,
        затем вставка списков ORM-объектов. Таблицы linking_codes / password_reset_codes не трогаются.
        """
        batch = 500

        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "TRUNCATE TABLE payments, payments_cards, payments_platega_crypto, "
                        "payments_stars, payments_cryptobot, payments_wata_sbp, payments_wata_card, payments_fk_sbp, "
                        "gifts, online, white_counter, users RESTART IDENTITY CASCADE"
                    )
                )

                def add_chunk(objs: List[Any]) -> None:
                    for i in range(0, len(objs), batch):
                        session.add_all(objs[i : i + batch])

                add_chunk(users)
                add_chunk(payments)
                add_chunk(payments_cards)
                add_chunk(payments_platega_crypto)
                add_chunk(payments_stars)
                add_chunk(payments_cryptobot)
                add_chunk(payments_wata_sbp)
                add_chunk(payments_wata_card)
                add_chunk(payments_fk_sbp)
                add_chunk(gifts)
                add_chunk(online)
                add_chunk(white_counter)

                await session.flush()

                for tbl, col in (
                    ("users", "id"),
                    ("payments", "id"),
                    ("payments_cards", "id"),
                    ("payments_platega_crypto", "id"),
                    ("payments_stars", "id"),
                    ("payments_cryptobot", "id"),
                    ("payments_wata_sbp", "id"),
                    ("payments_wata_card", "id"),
                    ("payments_fk_sbp", "id"),
                    ("white_counter", "id"),
                    ("online", "online_id"),
                ):
                    await session.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{tbl}', '{col}'), "
                            f"COALESCE((SELECT MAX(\"{col}\") FROM \"{tbl}\"), 1))"
                        )
                    )

        return {
            "users": len(users),
            "payments": len(payments),
            "payments_cards": len(payments_cards),
            "payments_platega_crypto": len(payments_platega_crypto),
            "payments_stars": len(payments_stars),
            "payments_cryptobot": len(payments_cryptobot),
            "payments_wata_sbp": len(payments_wata_sbp),
            "payments_wata_card": len(payments_wata_card),
            "payments_fk_sbp": len(payments_fk_sbp),
            "gifts": len(gifts),
            "online": len(online),
            "white_counter": len(white_counter),
        }

    async def add_white_counter_if_not_exists(self, user_id: int) -> None:
        """
        Добавляет запись в white_counter, если её ещё нет для данного пользователя.
        """
        async with self.session_factory() as session:
            stmt = select(WhiteCounter).where(WhiteCounter.user_id == user_id)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                session.add(WhiteCounter(user_id=user_id))
                await session.commit()
                logger.info(f"✅ Добавлена запись в white_counter для пользователя {user_id}")

    async def get_users_with_payment(self) -> List[int]:
        """Возвращает список user_id пользователей с reserve_field=True."""
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.reserve_field == True
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def get_user_subscription_payment_report(
        self, user_id: int
    ) -> List[Tuple[datetime, str, str, str]]:
        """
        Успешные платежи пользователя (confirmed/paid) по всем таблицам оплат.
        Возвращает список (time_created, тип, способ оплаты, детали: «N дн.» или «N GB»).
        """
        rows_acc: List[Tuple[datetime, str, str, str]] = []

        def _parse_map(payload: Optional[str]) -> dict[str, str]:
            if not payload:
                return {}
            out: dict[str, str] = {}
            for part in payload.split(","):
                if ":" not in part:
                    continue
                k, _, v = part.partition(":")
                out[k.strip()] = v.strip()
            return out

        def _row_report_fields(
            payload: Optional[str], is_gift: bool, amount: Any
        ) -> Tuple[str, str, str]:
            m = _parse_map(payload)
            method = _payment_method_label(m.get("method"))
            raw_duration = m.get("duration")

            traffic_gb = _parse_traffic_duration(raw_duration)
            if traffic_gb is not None:
                return "Трафик", method, f"{traffic_gb} GB"

            white = m.get("white", "False").lower() == "true"
            gift = bool(is_gift) or m.get("gift", "False").lower() == "true"
            dur = _payload_duration_to_panel_days(raw_duration)
            if dur is None:
                try:
                    amt_f = float(amount)
                except (TypeError, ValueError):
                    amt_f = None
                if amt_f is not None:
                    if white:
                        dur = _white_days_from_amount_fallback(amt_f)
                    else:
                        dur = _billing_duration_from_amount_fallback(amt_f)

            if gift and white:
                label = "Подарок, вайт (mobile)"
            elif gift:
                label = "Подарок, обычная"
            elif white:
                label = "Вайт (mobile)"
            else:
                label = "Обычная"

            days_s = f"{dur} дн." if dur is not None else "—"
            return label, method, days_s

        async with self.session_factory() as session:
            queries: List[Any] = [
                select(
                    Payments.user_id,
                    Payments.time_created,
                    Payments.amount,
                    Payments.payload,
                    Payments.is_gift,
                ).where(
                    Payments.user_id == user_id,
                    Payments.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsCards.user_id,
                    PaymentsCards.time_created,
                    PaymentsCards.amount,
                    PaymentsCards.payload,
                    PaymentsCards.is_gift,
                ).where(
                    PaymentsCards.user_id == user_id,
                    PaymentsCards.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsPlategaCrypto.user_id,
                    PaymentsPlategaCrypto.time_created,
                    PaymentsPlategaCrypto.amount,
                    PaymentsPlategaCrypto.payload,
                    PaymentsPlategaCrypto.is_gift,
                ).where(
                    PaymentsPlategaCrypto.user_id == user_id,
                    PaymentsPlategaCrypto.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsStars.user_id,
                    PaymentsStars.time_created,
                    PaymentsStars.amount,
                    PaymentsStars.payload,
                    PaymentsStars.is_gift,
                ).where(
                    PaymentsStars.user_id == user_id,
                    PaymentsStars.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsCryptobot.user_id,
                    PaymentsCryptobot.time_created,
                    PaymentsCryptobot.amount,
                    PaymentsCryptobot.payload,
                    PaymentsCryptobot.is_gift,
                ).where(
                    PaymentsCryptobot.user_id == user_id,
                    PaymentsCryptobot.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsWataSBP.user_id,
                    PaymentsWataSBP.time_created,
                    PaymentsWataSBP.amount,
                    PaymentsWataSBP.payload,
                    PaymentsWataSBP.is_gift,
                ).where(
                    PaymentsWataSBP.user_id == user_id,
                    PaymentsWataSBP.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsWataCard.user_id,
                    PaymentsWataCard.time_created,
                    PaymentsWataCard.amount,
                    PaymentsWataCard.payload,
                    PaymentsWataCard.is_gift,
                ).where(
                    PaymentsWataCard.user_id == user_id,
                    PaymentsWataCard.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsFkSBP.user_id,
                    PaymentsFkSBP.time_created,
                    PaymentsFkSBP.amount,
                    PaymentsFkSBP.payload,
                    PaymentsFkSBP.is_gift,
                ).where(
                    PaymentsFkSBP.user_id == user_id,
                    PaymentsFkSBP.status.in_(_BILLING_OK_STATUSES),
                ),
            ]
            for q in queries:
                for _uid, tc, amt, pl, ig in (await session.execute(q)).all():
                    kind, method, detail = _row_report_fields(pl, bool(ig), amt)
                    rows_acc.append((tc, kind, method, detail))

        rows_acc.sort(key=lambda x: (x[0], x[1]))
        return rows_acc

