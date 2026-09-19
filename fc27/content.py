import json
from urllib.request import Request, urlopen

from .errors import FC27Error


FUTGG_R2_BASE = "https://r2.fut.gg/27"
USER_AGENT = "Mozilla/5.0 (compatible; FC27-Copilot/0.5)"
CONTENT_TYPES = ("objective", "evolution", "sbc")
CONTENT_SOURCES = ("auto", "ea", "futgg")


class FutggContentClient:
    def __init__(self, base_url=FUTGG_R2_BASE, fetch_json=None):
        self.base_url = base_url.rstrip("/")
        self.fetch_json = fetch_json or self._fetch_json

    def evolutions(self, scope="active"):
        key = "all-evolutions" if scope == "all" else "active-evolutions"
        manifest = self.fetch_json(f"{self.base_url}/manifest.json")
        digest = manifest.get(key)
        version = manifest.get("_version", 1)
        if not digest:
            raise FC27Error(
                "FUTGG_CONTENT_SCHEMA_INVALID",
                f"FUT.GG manifest is missing {key}.",
                retryable=True,
                recovery="Inspect the current FC27 FUT.GG manifest before retrying.",
            )
        rows = self.fetch_json(f"{self.base_url}/{key}.v{version}.{digest}.json")
        if not isinstance(rows, list):
            raise FC27Error(
                "FUTGG_CONTENT_SCHEMA_INVALID",
                f"FUT.GG {key} dataset must be an array.",
                recovery="Keep using EA account content and inspect the current FUT.GG dataset.",
            )
        return {
            "source": "futgg",
            "manifest_version": version,
            "manifest_key": key,
            "manifest_hash": digest,
            "evolutions": [self._normalize_evolution(row) for row in rows],
        }

    @staticmethod
    def _normalize_evolution(row):
        levels = []
        for level in row.get("levels") or []:
            levels.append(
                {
                    "index": level.get("idx"),
                    "challenges": level.get("challenges") or [],
                    "training_time": level.get("readableTrainingTime"),
                    "upgrades": level.get("totalUpgradesText") or [],
                    "has_upgrade_choices": bool(level.get("hasUpgradeChoices")),
                }
            )
        return {
            "id": row.get("id"),
            "ea_id": row.get("eaId"),
            "name": row.get("name"),
            "description": row.get("description"),
            "url": f"https://www.fut.gg{row['url']}" if str(row.get("url") or "").startswith("/") else row.get("url"),
            "coins_cost": row.get("coinsCost"),
            "points_cost": row.get("pointsCost"),
            "token_cost": row.get("tokenCost"),
            "repeatability_count": row.get("repeatabilityCount"),
            "created_at": row.get("createdAt"),
            "enrollment_end_time": row.get("endSubmissionTime"),
            "end_time": row.get("endTime"),
            "expired": bool(row.get("isExpired")),
            "timed": bool(row.get("isTimed")),
            "training_time": row.get("readableTrainingTime"),
            "requirements": row.get("requirementsText") or [],
            "levels": levels,
            "total_upgrades": row.get("totalUpgradesText") or [],
        }

    @staticmethod
    def _fetch_json(url):
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Referer": "https://www.fut.gg/",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise FC27Error(
                "FUTGG_REQUEST_FAILED",
                "Could not read current FUT.GG content data.",
                retryable=True,
                recovery="Check FUT.GG connectivity and the local proxy route before retrying.",
                details={"url": url, "exception": type(error).__name__, "message": str(error)},
            ) from error


class ContentService:
    @staticmethod
    def validate_arguments(arguments):
        content_type = str(arguments.get("content_type") or "").lower()
        source = str(arguments.get("source") or "auto").lower()
        detail = str(arguments.get("detail") or "summary").lower()
        scope = str(arguments.get("scope") or "active").lower()
        if content_type not in CONTENT_TYPES:
            raise FC27Error(
                "INVALID_CONTENT_TYPE",
                f"content_type must be one of: {', '.join(CONTENT_TYPES)}.",
            )
        if source not in CONTENT_SOURCES:
            raise FC27Error(
                "INVALID_CONTENT_SOURCE",
                f"source must be one of: {', '.join(CONTENT_SOURCES)}.",
            )
        if detail not in ("summary", "detailed"):
            raise FC27Error("INVALID_CONTENT_DETAIL", "detail must be summary or detailed.")
        if scope not in ("active", "all"):
            raise FC27Error("INVALID_CONTENT_SCOPE", "scope must be active or all.")
        return {
            "content_type": content_type,
            "source": source,
            "detail": detail,
            "scope": scope,
            "text": str(arguments.get("text") or "").strip().casefold(),
            "include_completed": bool(arguments.get("include_completed", False)),
            "limit": max(1, min(int(arguments.get("limit", 50)), 200)),
        }

    @classmethod
    def normalize_ea(cls, content_type, raw, options):
        if content_type == "objective":
            items = []
            for category in raw.get("categories") or []:
                for group in category.get("groups") or []:
                    items.append(
                        {
                            **group,
                            "category_id": category.get("id"),
                            "category_name": category.get("name"),
                        }
                    )
        elif content_type == "evolution":
            items = list(raw.get("evolutions") or [])
        else:
            items = list(raw.get("sets") or [])
        return cls._finish(content_type, "ea", items, options, {"status": raw.get("status")})

    @classmethod
    def normalize_futgg_evolutions(cls, raw, options):
        metadata = {
            "manifest_version": raw.get("manifest_version"),
            "manifest_key": raw.get("manifest_key"),
            "manifest_hash": raw.get("manifest_hash"),
        }
        return cls._finish("evolution", "futgg", raw.get("evolutions") or [], options, metadata)

    @classmethod
    def _finish(cls, content_type, source, items, options, metadata):
        filtered = []
        for item in items:
            if not options["include_completed"] and cls._completed(content_type, item):
                continue
            if options["scope"] == "active" and cls._expired(content_type, item):
                continue
            if options["text"] and options["text"] not in cls._search_text(item):
                continue
            filtered.append(item)
        filtered.sort(key=lambda item: cls._sort_key(content_type, item))
        total_count = len(filtered)
        selected = filtered[: options["limit"]]
        if options["detail"] == "summary":
            selected = [cls._summary(content_type, item) for item in selected]
        return {
            "content_type": content_type,
            "source": source,
            "detail": options["detail"],
            "scope": options["scope"],
            "total_count": total_count,
            "returned_count": len(selected),
            "items": selected,
            "source_meta": metadata,
        }

    @staticmethod
    def _completed(content_type, item):
        if content_type == "evolution":
            return bool(item.get("completed"))
        return bool(item.get("completed"))

    @staticmethod
    def _expired(content_type, item):
        if content_type == "evolution":
            return bool(item.get("expired"))
        return False

    @staticmethod
    def _search_text(item):
        return " ".join(
            str(item.get(key) or "")
            for key in ("name", "title", "description", "subtitle", "category_name")
        ).casefold()

    @staticmethod
    def _sort_key(content_type, item):
        end_time = item.get("end_time") or item.get("expires") or "9999"
        name = item.get("name") or item.get("title") or ""
        return (str(end_time), str(name).casefold(), str(item.get("id") or ""))

    @staticmethod
    def _summary(content_type, item):
        if content_type == "objective":
            return {
                key: item.get(key)
                for key in (
                    "id",
                    "composite_id",
                    "title",
                    "subtitle",
                    "category_id",
                    "category_name",
                    "type",
                    "state",
                    "start_time",
                    "end_time",
                    "completed",
                    "completed_tasks",
                    "required_tasks",
                    "times_completed",
                    "claimable",
                )
            } | {"task_count": len(item.get("tasks") or [])}
        if content_type == "evolution":
            return {
                key: item.get(key)
                for key in (
                    "id",
                    "ea_id",
                    "name",
                    "description",
                    "category_id",
                    "category_name",
                    "status",
                    "active",
                    "started",
                    "completed",
                    "claimable",
                    "timed",
                    "coins_cost",
                    "points_cost",
                    "token_cost",
                    "repeatability_count",
                    "remaining_repetitions",
                    "enrollment_end_time",
                    "end_time",
                    "expired",
                    "training_time",
                    "url",
                )
            } | {"level_count": len(item.get("levels") or [])}
        return {
            key: item.get(key)
            for key in (
                "id",
                "name",
                "description",
                "status",
                "expires",
                "repeatable",
                "completed",
                "completed_count",
                "times_completed",
                "challenge_count",
            )
        }
