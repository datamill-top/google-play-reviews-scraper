"""Google Play Scraper — no personal data.

Собирает карточки приложений и отзывы из Google Play.
Персональные данные не попадают в выдачу: имя автора и аватар
отбрасываются до записи, вместо них — необратимый хеш.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

from apify import Actor
from google_play_scraper import Sort, app as gp_app, reviews as gp_reviews, search as gp_search
from google_play_scraper.exceptions import NotFoundError

# ---------------------------------------------------------------- константы

# Поля, которые НИКОГДА не попадают в выдачу.
PERSONAL_REVIEW_FIELDS = ("userName", "userImage")
PERSONAL_APP_FIELDS = ("developerEmail", "developerAddress")

# Тяжёлые поля карточки — включаются по запросу, иначе раздувают выгрузку.
HEAVY_APP_FIELDS = ("description", "descriptionHTML", "screenshots", "comments")

SORT_MAP = {
    "newest": Sort.NEWEST,
    "rating": Sort.RATING,
    "helpfulness": Sort.MOST_RELEVANT,
}

# Соль для хеширования. Задаётся переменной окружения в настройках актора.
# Без секретной соли хеш теоретически подбирается по словарю имён.
SALT = os.environ.get("AUTHOR_HASH_SALT", "datamill-gplay-v1")

# Идентификатор пакета: сегменты из букв, цифр и подчёркиваний через точку.
_PKG_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")
# Ссылка из браузера или из приложения Play Маркет.
_ID_IN_URL = re.compile(r"[?&]id=([A-Za-z0-9_.]+)")

REVIEWS_PAGE = 100          # сколько отзывов запрашиваем за один заход
MAX_ATTEMPTS = 4            # попыток на один запрос к источнику
MAX_ANNOTATE = 50_000       # выше этого объёма разметку тёзок пропускаем


# ------------------------------------------------------------- утилиты

def author_hash(user_name: str | None) -> str | None:
    """Псевдоним автора вместо имени. Восстановить имя из хеша нельзя.

    У Google Play нет открытого идентификатора пользователя, поэтому хеш
    считается от отображаемого имени — значит полные тёзки склеятся.
    Насколько это мешает, показывают поля authorHashCount и authorHashApps.
    """
    if not user_name:
        return None
    return hashlib.sha256(f"{SALT}:{user_name}".encode()).hexdigest()[:16]


def annotate_authors(rows: list[dict]) -> list[dict]:
    """Размечает совпадения ников.

    На Google Play один аккаунт оставляет не больше одного отзыва на приложение.
    Поэтому повтор хеша ВНУТРИ одного приложения — это всегда разные люди,
    то есть тёзки. А повтор в РАЗНЫХ приложениях может означать одного автора.

    authorHashCount — сколько всего отзывов в запуске с таким хешем.
                      Большое число = слишком частое имя, доверять нельзя.
    authorHashApps  — в скольких разных приложениях этот хеш встретился.
    """
    total: dict[str, int] = {}
    apps: dict[str, set[str]] = {}
    for r in rows:
        h = r.get("authorHash")
        if not h:
            continue
        total[h] = total.get(h, 0) + 1
        apps.setdefault(h, set()).add(r.get("appId", ""))

    for r in rows:
        h = r.get("authorHash")
        r["authorHashCount"] = total.get(h) if h else None
        r["authorHashApps"] = len(apps.get(h, ())) if h else None
    return rows


# Форматы дат, которые встречаются в карточке приложения.
_DATE_FORMATS = (
    "%b %d, %Y",      # Sep 6, 2013      — английский
    "%B %d, %Y",      # September 6, 2013
    "%d %b %Y",       # 6 Sep 2013
    "%Y-%m-%d",       # 2013-09-06
    "%d.%m.%Y",       # 06.09.2013       — немецкий, русский числом
    "%d/%m/%Y",       # 06/09/2013       — большинство европейских
    "%d-%m-%Y",
)


def normalize_app_id(raw: str) -> str | None:
    """Приводит ввод к идентификатору пакета.

    Клиенты вставляют адрес из браузера целиком — это самая частая причина
    ответа «не найдено» и низких оценок у похожих инструментов.
    Принимаем и адрес, и голый идентификатор, и то же с лишними пробелами.
    """
    value = (raw or "").strip().strip('"\'')
    if not value:
        return None

    if "id=" in value:
        found = _ID_IN_URL.search(value)
        if found:
            value = found.group(1)

    value = value.split("&")[0].split("#")[0].strip()
    return value if _PKG_RE.match(value) else None


def prepare_app_ids(raw_list: list[str]) -> tuple[list[str], list[str]]:
    """Чистит список: разбирает адреса, убирает повторы, отсеивает мусор.

    Повторы критичны: без их удаления клиент получит дублирующиеся строки
    и будет посчитан за одни и те же данные дважды.
    """
    good: list[str] = []
    bad: list[str] = []
    seen: set[str] = set()

    for item in raw_list or []:
        app_id = normalize_app_id(str(item))
        if not app_id:
            bad.append(str(item)[:80])
            continue
        if app_id in seen:
            continue
        seen.add(app_id)
        good.append(app_id)

    return good, bad


def iso(value: Any) -> str | None:
    """Приводит любую дату из источника к единому виду ISO 8601.

    Источник смешивает три формата: объект даты в отзывах, метку времени Unix
    в поле updated и человекочитаемую строку в released. В выгрузке всё это
    должно быть одинаковым, иначе колонку невозможно ни отсортировать,
    ни отфильтровать.
    """
    if value in (None, "", 0):
        return None

    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()

    # метка времени Unix — числом или строкой из цифр
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            seconds = int(value)
            if 10 ** 8 < seconds < 10 ** 11:          # разумный диапазон дат
                return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass

    if isinstance(value, str):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), fmt).replace(
                    tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue

    # Разобрать не вышло. Возвращаем None, а не исходную строку: колонка с датой
    # должна быть либо датой, либо пустой, иначе её не отсортировать.
    # Исходное значение при этом не теряется — оно лежит в releasedText.
    return None


def dedupe(items: Any) -> Any:
    """Google отдаёт часть ссылок на скриншоты по два раза."""
    if not isinstance(items, list):
        return items
    seen, out = set(), []
    for x in items:
        key = x if isinstance(x, str) else repr(x)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    Actor.log.warning(f"Не разобрал дату «{value}», фильтр по дате отключён")
    return None


async def with_retry(func, *args, label: str = "", **kwargs):
    """Повтор с нарастающей паузой. Ради этого клиенты и уходят от конкурентов."""
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except NotFoundError:
            raise
        except Exception as exc:                      # noqa: BLE001
            last = exc
            if attempt < MAX_ATTEMPTS:
                pause = 2 ** attempt
                Actor.log.warning(
                    f"{label}: попытка {attempt} не удалась ({exc}); повтор через {pause} c")
                await asyncio.sleep(pause)
    raise last  # type: ignore[misc]


# Включена ли оплата за события. Выясняем один раз, чтобы не сыпать
# предупреждениями в лог клиента, когда актор работает бесплатно.
_charging_enabled: bool | None = None


def charging_available() -> bool:
    global _charging_enabled
    if _charging_enabled is None:
        _charging_enabled = False
        try:
            manager = Actor.get_charging_manager()
            info = manager.get_pricing_info()
            _charging_enabled = bool(getattr(info, "is_pay_per_event", False))
        except Exception:                              # noqa: BLE001
            _charging_enabled = False
    return _charging_enabled


async def charge(event: str, count: int = 1) -> None:
    """Списание за результат. При бесплатной модели молча пропускается."""
    if count <= 0 or not charging_available():
        return
    try:
        await Actor.charge(event_name=event, count=count)
    except Exception as exc:                           # noqa: BLE001
        Actor.log.warning(f"Не удалось учесть событие {event}: {exc}")


# --------------------------------------------------------------- сборка

def clean_app(raw: dict, keep_heavy: bool) -> dict:
    out = {k: v for k, v in raw.items() if k not in PERSONAL_APP_FIELDS}
    if not keep_heavy:
        for field in HEAVY_APP_FIELDS:
            out.pop(field, None)
    else:
        out.pop("descriptionHTML", None)
        out.pop("comments", None)

    # Дата выпуска приходит на языке запроса: "Sep 6, 2013", "06.09.2013",
    # "6 сент. 2013 г." Исходную строку сохраняем, в released кладём только
    # то, что удалось привести к единому виду.
    out["releasedText"] = out.get("released")
    out["released"] = iso(out.get("released"))
    out["updated"] = iso(out.get("updated"))

    # lastUpdatedOn тоже локализован, но та же дата уже есть в updated
    # меткой времени — берём её, она от языка не зависит.
    out["lastUpdatedOn"] = iso(out.get("lastUpdatedOn")) or out["updated"]
    if "screenshots" in out:
        out["screenshots"] = dedupe(out["screenshots"])
    out["scrapedAt"] = datetime.now(timezone.utc).isoformat()
    out["type"] = "app"
    return out


def clean_review(raw: dict, app_id: str) -> dict:
    out = {k: v for k, v in raw.items() if k not in PERSONAL_REVIEW_FIELDS}
    out["authorHash"] = author_hash(raw.get("userName"))
    out["appId"] = app_id
    out["at"] = iso(out.get("at"))
    out["repliedAt"] = iso(out.get("repliedAt"))
    out["scrapedAt"] = datetime.now(timezone.utc).isoformat()
    out["type"] = "review"
    return out


async def fetch_app(app_id: str, lang: str, country: str, keep_heavy: bool) -> dict | None:
    try:
        raw = await with_retry(gp_app, app_id, lang=lang, country=country,
                               label=f"карточка {app_id}")
    except NotFoundError:
        Actor.log.warning(f"Приложение {app_id} не найдено в стране {country}")
        return None
    except Exception as exc:                           # noqa: BLE001
        Actor.log.error(f"Карточка {app_id}: не удалось получить — {exc}")
        return None

    record = clean_app(raw, keep_heavy)

    # Если дату выпуска не удалось разобрать на языке клиента, добираем её
    # с английской карточки. Один лишний запрос на приложение, и только когда
    # это действительно нужно — на английских запусках не тратится ничего.
    if record.get("released") is None and record.get("releasedText") and lang != "en":
        try:
            en = await with_retry(gp_app, app_id, lang="en", country=country,
                                  label=f"дата выпуска {app_id}")
            record["released"] = iso(en.get("released"))
            if record["released"]:
                Actor.log.info(f"{app_id}: дату выпуска добрал с английской карточки")
        except Exception:                              # noqa: BLE001
            Actor.log.warning(
                f"{app_id}: дату выпуска «{record['releasedText']}» разобрать не вышло, "
                f"оставляю только в releasedText")

    return record


async def fetch_reviews(app_id: str, lang: str, country: str, sort_key: str,
                        limit: int, score_filter: int | None,
                        since: datetime | None) -> list[dict]:
    collected: list[dict] = []
    token = None
    stop = False

    while len(collected) < limit and not stop:
        want = min(REVIEWS_PAGE, limit - len(collected))
        try:
            batch, token = await with_retry(
                gp_reviews, app_id,
                lang=lang, country=country, sort=SORT_MAP[sort_key],
                count=want, filter_score_with=score_filter,
                continuation_token=token,
                label=f"отзывы {app_id}")
        except NotFoundError:
            Actor.log.warning(f"Отзывы для {app_id} недоступны")
            break
        except Exception as exc:                       # noqa: BLE001
            Actor.log.error(f"Отзывы {app_id}: прерываю после ошибок — {exc}")
            break

        if not batch:
            break

        for raw in batch:
            at = raw.get("at")
            if since and isinstance(at, datetime):
                if at.replace(tzinfo=at.tzinfo or timezone.utc) < since:
                    stop = True          # сортировка по дате: дальше только старее
                    break
            collected.append(clean_review(raw, app_id))

        if token is None:
            break

    return collected[:limit]


# ----------------------------------------------------------------- main

async def main() -> None:
    async with Actor:
        cfg = await Actor.get_input() or {}

        app_ids, rejected = prepare_app_ids(cfg.get("appIds") or [])
        duplicates = len([a for a in (cfg.get("appIds") or []) if a]) - len(app_ids) - len(rejected)
        if duplicates > 0:
            Actor.log.info(f"Повторов во вводе убрано: {duplicates}")
        for item in rejected:
            Actor.log.warning(
                f"Не разобрал «{item}» — ожидается идентификатор вида com.whatsapp "
                f"или ссылка на страницу приложения")
        query: str = (cfg.get("searchQuery") or "").strip()
        max_from_search: int = int(cfg.get("maxAppsFromSearch") or 10)
        lang: str = cfg.get("language") or "en"
        country: str = cfg.get("country") or "us"

        want_apps: bool = cfg.get("scrapeAppDetails", True)
        want_reviews: bool = cfg.get("scrapeReviews", True)
        keep_heavy: bool = cfg.get("includeDescription", True)

        max_reviews: int = int(cfg.get("maxReviewsPerApp") or 0)
        sort_key: str = cfg.get("reviewsSort") or "newest"
        score_filter = cfg.get("filterByScore")
        score_filter = int(score_filter) if score_filter else None
        since = parse_date(cfg.get("reviewsSince"))
        concurrency: int = max(1, min(int(cfg.get("maxConcurrency") or 5), 10))

        if sort_key not in SORT_MAP:
            Actor.log.warning(f"Неизвестная сортировка «{sort_key}», беру newest")
            sort_key = "newest"

        # --- откуда берём список приложений ---
        if query:
            Actor.log.info(f"Поиск приложений по запросу: {query}")
            try:
                hits = await with_retry(gp_search, query, lang=lang, country=country,
                                        n_hits=max_from_search, label="поиск")
                found = [h["appId"] for h in hits if h.get("appId")]
                Actor.log.info(f"Найдено приложений: {len(found)}")
                app_ids, _ = prepare_app_ids(app_ids + found)
            except Exception as exc:                   # noqa: BLE001
                Actor.log.error(f"Поиск не удался: {exc}")

        if not app_ids:
            hint = (f" Не разобрано: {', '.join(rejected[:3])}." if rejected else "")
            await Actor.fail(status_message=(
                "Не задано ни одного приложения. Укажите appIds — идентификатор "
                "вида com.whatsapp или ссылку на страницу в Google Play."
                + hint))
            return

        if not (want_apps or want_reviews):
            await Actor.fail(status_message="Обе выгрузки отключены — собирать нечего.")
            return

        Actor.log.info(f"К обработке приложений: {len(app_ids)} | язык={lang} страна={country}")

        annotate: bool = cfg.get("annotateAuthorCollisions", True)

        sem = asyncio.Semaphore(concurrency)
        stats = {"apps_ok": 0, "apps_failed": 0, "reviews": 0}
        review_buffer: list[dict] = []

        async def handle(app_id: str) -> None:
            async with sem:
                if want_apps:
                    record = await fetch_app(app_id, lang, country, keep_heavy)
                    if record:
                        await Actor.push_data(record)
                        await charge("app-detail", 1)
                        stats["apps_ok"] += 1
                    else:
                        stats["apps_failed"] += 1
                        return

                if want_reviews and max_reviews > 0:
                    rows = await fetch_reviews(app_id, lang, country, sort_key,
                                               max_reviews, score_filter, since)
                    if rows:
                        review_buffer.extend(rows)
                        stats["reviews"] += len(rows)
                    Actor.log.info(f"{app_id}: отзывов собрано {len(rows)}")

                await Actor.set_status_message(
                    f"Готово приложений: {stats['apps_ok']}/{len(app_ids)}, "
                    f"отзывов: {stats['reviews']}")

        await asyncio.gather(*(handle(a) for a in app_ids))

        # Разметка совпадающих ников возможна только когда собраны все отзывы.
        if review_buffer:
            if annotate and len(review_buffer) <= MAX_ANNOTATE:
                annotate_authors(review_buffer)
                dupes = sum(1 for r in review_buffer if (r.get("authorHashCount") or 0) > 1)
                Actor.log.info(
                    f"Совпадающих ников: {dupes} отзывов из {len(review_buffer)}. "
                    f"Внутри одного приложения совпадение означает тёзок, "
                    f"в разных — возможно одного автора.")
            elif annotate:
                Actor.log.warning(
                    f"Отзывов {len(review_buffer)} — больше порога {MAX_ANNOTATE}, "
                    f"разметку совпадений пропускаю ради памяти.")

            for i in range(0, len(review_buffer), 500):
                await Actor.push_data(review_buffer[i:i + 500])
            await charge("review", len(review_buffer))

        Actor.log.info(
            f"Итог: карточек {stats['apps_ok']}, не удалось {stats['apps_failed']}, "
            f"отзывов {stats['reviews']}. Персональные данные в выдачу не включались.")
        await Actor.set_status_message(
            f"Собрано: {stats['apps_ok']} карточек, {stats['reviews']} отзывов")
