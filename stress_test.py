#!/usr/bin/env python3
"""Прогон актора по проблемным случаям.

Запускать из корня проекта, при активированном окружении:
    python3 stress_test.py
    python3 stress_test.py --only locale,search     # выборочно
    python3 stress_test.py --keep                   # не удалять данные после прогона

Скрипт подставляет разные настройки, запускает актор и проверяет результат.
Все проверки автоматические — глазами смотреть не нужно, кроме итогового отчёта.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "storage" / "key_value_stores" / "default" / "INPUT.json"
DATASET = ROOT / "storage" / "datasets" / "default"

PERSONAL = ("userName", "userImage", "developerEmail", "developerAddress")
DATE_FIELDS = ("released", "updated", "lastUpdatedOn", "at", "repliedAt", "scrapedAt")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

# Приложения выбраны так, чтобы существовали годами и в разных странах.
WHATSAPP = "com.whatsapp"
TELEGRAM = "org.telegram.messenger"
MINECRAFT = "com.mojang.minecraftpe"      # платное
DUOLINGO = "com.duolingo"
GHOST = "com.datamill.this.does.not.exist"


SCENARIOS: dict[str, dict] = {
    "basic": {
        "why": "обычный прогон: карточка плюс немного отзывов",
        "input": {"appIds": [WHATSAPP], "maxReviewsPerApp": 20},
        "expect": {"min_apps": 1, "min_reviews": 15},
    },
    "paid_app": {
        "why": "платное приложение — проверяем поля цены",
        "input": {"appIds": [MINECRAFT], "maxReviewsPerApp": 5},
        "expect": {"min_apps": 1, "price_not_free": True},
    },
    "missing_only": {
        "why": "несуществующее приложение: не должно ронять запуск",
        "input": {"appIds": [GHOST], "maxReviewsPerApp": 5},
        "expect": {"min_apps": 0, "exit_ok": True},
    },
    "missing_mixed": {
        "why": "битый идентификатор рядом с рабочим — остальное должно собраться",
        "input": {"appIds": [GHOST, WHATSAPP], "maxReviewsPerApp": 10},
        "expect": {"min_apps": 1, "min_reviews": 5},
    },
    "apps_only": {
        "why": "только карточки, лёгкая выгрузка без описаний",
        "input": {"appIds": [WHATSAPP, TELEGRAM], "maxReviewsPerApp": 0,
                  "includeDescription": False},
        "expect": {"min_apps": 2, "max_reviews": 0, "no_description": True},
    },
    "locale": {
        "why": "другая страна и язык — цены и тексты должны отличаться",
        "input": {"appIds": [TELEGRAM], "maxReviewsPerApp": 15,
                  "language": "de", "country": "de"},
        "expect": {"min_apps": 1, "min_reviews": 10},
    },
    "cyrillic": {
        "why": "нелатинские тексты в отзывах — проверка кодировки",
        "input": {"appIds": [TELEGRAM], "maxReviewsPerApp": 30,
                  "language": "ru", "country": "kz"},
        "expect": {"min_apps": 1, "min_reviews": 10},
    },
    "score_filter": {
        "why": "только единицы — все отзывы должны быть с оценкой 1",
        "input": {"appIds": [WHATSAPP], "maxReviewsPerApp": 25, "filterByScore": 1},
        "expect": {"min_reviews": 10, "all_score": 1},
    },
    "since_date": {
        "why": "фильтр по дате — досрочная остановка сбора",
        "input": {"appIds": [WHATSAPP], "maxReviewsPerApp": 500,
                  "reviewsSince": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
        "expect": {"reviews_after": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
    },
    "pagination": {
        "why": "больше сотни отзывов — работа токена продолжения",
        "input": {"appIds": [WHATSAPP], "maxReviewsPerApp": 350},
        "expect": {"min_reviews": 250, "no_duplicate_reviews": True},
    },
    "over_request": {
        "why": "просим больше, чем есть — не должно зациклиться",
        "input": {"appIds": [GHOST, DUOLINGO], "maxReviewsPerApp": 100,
                  "filterByScore": 3},
        "expect": {"exit_ok": True},
    },
    "search": {
        "why": "поиск вместо списка идентификаторов",
        "input": {"searchQuery": "habit tracker", "maxAppsFromSearch": 3,
                  "maxReviewsPerApp": 5},
        "expect": {"min_apps": 2},
    },
    "search_empty": {
        "why": "поиск без результатов — понятная ошибка, а не падение",
        "input": {"searchQuery": "qwzxjklmnoppp zzz nothing", "maxAppsFromSearch": 5,
                  "maxReviewsPerApp": 0},
        "expect": {"exit_any": True},
    },
    "collisions": {
        "why": "разметка совпадающих ников по нескольким приложениям",
        "input": {"appIds": [WHATSAPP, TELEGRAM, DUOLINGO], "maxReviewsPerApp": 120},
        "expect": {"min_reviews": 200, "has_collision_fields": True},
    },
    "bad_sort": {
        "why": "неверная сортировка — платформа обязана отклонить по схеме",
        "input": {"appIds": [DUOLINGO], "maxReviewsPerApp": 10, "reviewsSort": "wrong"},
        "expect": {"exit_any": True, "must_fail": True},
    },
    "messy_input": {
        "why": "ссылка из браузера, пробелы, повторы и мусор в списке",
        "input": {"appIds": [
            f"https://play.google.com/store/apps/details?id={WHATSAPP}&hl=en",
            f"  {DUOLINGO}  ", WHATSAPP, WHATSAPP, "не идентификатор"],
            "maxReviewsPerApp": 10},
        "expect": {"min_apps": 2, "exact_apps": 2, "no_duplicate_reviews": True,
                   "unique_app_ids": True},
    },
    "released_locale": {
        "why": "дата выпуска на трёх языках — везде должен быть единый формат",
        "input": {"appIds": [TELEGRAM], "maxReviewsPerApp": 0, "language": "ru",
                  "country": "ru"},
        "expect": {"min_apps": 1, "released_parsed": True},
    },
}


# ------------------------------------------------------------------ запуск

def run_actor(payload: dict) -> tuple[int, str]:
    INPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    INPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if DATASET.exists():
        shutil.rmtree(DATASET)
    proc = subprocess.run(["apify", "run"], cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr)


def load_rows() -> list[dict]:
    if not DATASET.exists():
        return []
    rows = []
    for f in sorted(DATASET.glob("*.json")):
        try:
            rows.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:                       # noqa: BLE001
            rows.append({"__broken__": str(exc), "type": "broken"})
    return rows


# --------------------------------------------------------------- проверки

def check(name: str, rows: list[dict], expect: dict, code: int, log: str) -> list[str]:
    problems: list[str] = []
    apps = [r for r in rows if r.get("type") == "app"]
    revs = [r for r in rows if r.get("type") == "review"]

    if any(r.get("type") == "broken" for r in rows):
        problems.append("часть файлов датасета не читается")

    if not expect.get("exit_any") and code != 0 and "min_apps" in expect \
            and expect["min_apps"] > 0:
        problems.append(f"код выхода {code}")

    # --- обязательное для всех сценариев ---
    for r in rows:
        for field in PERSONAL:
            if field in r:
                problems.append(f"УТЕЧКА: поле {field} попало в выдачу")
                break
        for field in DATE_FIELDS:
            v = r.get(field)
            if v and isinstance(v, str) and not ISO.match(v) and field != "version":
                problems.append(f"дата {field} не в формате ISO: {v[:30]}")
                break

    for r in apps:
        if not r.get("appId") or not r.get("title"):
            problems.append("в карточке нет appId или title")
            break
    for r in revs:
        if not r.get("appId") or not r.get("reviewId"):
            problems.append("в отзыве нет appId или reviewId")
            break
        if "content" not in r or "score" not in r:
            problems.append("в отзыве нет текста или оценки")
            break

    # --- ожидания сценария ---
    if "min_apps" in expect and len(apps) < expect["min_apps"]:
        problems.append(f"карточек {len(apps)}, ожидалось ≥{expect['min_apps']}")
    if "min_reviews" in expect and len(revs) < expect["min_reviews"]:
        problems.append(f"отзывов {len(revs)}, ожидалось ≥{expect['min_reviews']}")
    if "max_reviews" in expect and len(revs) > expect["max_reviews"]:
        problems.append(f"отзывов {len(revs)}, ожидалось ≤{expect['max_reviews']}")

    if expect.get("price_not_free") and apps:
        if apps[0].get("free") is not False:
            problems.append("платное приложение помечено как бесплатное")

    if expect.get("no_description") and apps:
        for f in ("description", "screenshots", "descriptionHTML"):
            if f in apps[0]:
                problems.append(f"поле {f} не исключено при includeDescription=false")

    if expect.get("all_score") and revs:
        wrong = {r.get("score") for r in revs} - {expect["all_score"]}
        if wrong:
            problems.append(f"фильтр оценки не сработал, встретились: {sorted(wrong)}")

    if expect.get("reviews_after") and revs:
        limit = expect["reviews_after"]
        older = [r for r in revs if (r.get("at") or "")[:10] < limit]
        if older:
            problems.append(f"{len(older)} отзывов старее {limit}")

    if expect.get("no_duplicate_reviews") and revs:
        ids = [r.get("reviewId") for r in revs]
        if len(ids) != len(set(ids)):
            problems.append(f"повторов отзывов: {len(ids) - len(set(ids))}")

    if "exact_apps" in expect and len(apps) != expect["exact_apps"]:
        problems.append(f"карточек {len(apps)}, ожидалось ровно {expect['exact_apps']}")

    if expect.get("unique_app_ids"):
        ids = [r.get("appId") for r in apps]
        if len(ids) != len(set(ids)):
            problems.append("одно приложение собрано несколько раз — клиент заплатит дважды")

    if expect.get("must_fail") and code == 0:
        problems.append("платформа приняла заведомо неверные настройки")

    if expect.get("released_parsed") and apps:
        if not apps[0].get("released"):
            problems.append(
                f"дата выпуска не разобрана: {apps[0].get('releasedText')!r}")
        if "releasedText" not in apps[0]:
            problems.append("нет поля releasedText с исходной строкой")

    if expect.get("has_collision_fields") and revs:
        if "authorHashCount" not in revs[0] or "authorHashApps" not in revs[0]:
            problems.append("нет полей разметки совпадений ников")

    if "Traceback" in log:
        problems.append("в логе есть трассировка исключения")

    return problems


# ------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="через запятую: basic,locale,search")
    ap.add_argument("--keep", action="store_true", help="не чистить данные в конце")
    args = ap.parse_args()

    names = list(SCENARIOS)
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip() in SCENARIOS]
        if not names:
            print("Неизвестные сценарии. Доступны:", ", ".join(SCENARIOS))
            sys.exit(1)

    print(f"Сценариев к прогону: {len(names)}\n")
    results = []

    for i, name in enumerate(names, 1):
        sc = SCENARIOS[name]
        print(f"[{i}/{len(names)}] {name} — {sc['why']}")
        started = time.time()
        code, log = run_actor(sc["input"])
        rows = load_rows()
        problems = check(name, rows, sc["expect"], code, log)
        took = time.time() - started

        apps = sum(1 for r in rows if r.get("type") == "app")
        revs = sum(1 for r in rows if r.get("type") == "review")
        status = "ОК   " if not problems else "СБОЙ "
        print(f"      {status} карточек {apps}, отзывов {revs}, {took:.0f} c")
        for p in problems:
            print(f"        ! {p}")
        results.append((name, not problems, apps, revs, problems, took))
        print()

    ok = sum(1 for r in results if r[1])
    print("=" * 62)
    print(f"ИТОГ: успешно {ok} из {len(results)}")
    for name, good, apps, revs, problems, took in results:
        mark = "✓" if good else "✗"
        print(f"  {mark} {name:15} карточек {apps:3}  отзывов {revs:5}  {took:5.0f} c")
        for p in problems:
            print(f"      {p}")

    if not args.keep and DATASET.exists():
        shutil.rmtree(DATASET)

    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
