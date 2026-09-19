import json
import re
import time
import unicodedata
from urllib.request import Request, urlopen

from .errors import FC27Error


FUTGG_R2_BASE = "https://r2.fut.gg/27"
USER_AGENT = "Mozilla/5.0 (compatible; FC27-Copilot/0.5)"
CONTENT_TYPES = ("season", "objective", "evolution", "sbc")
CONTENT_SOURCES = ("auto", "ea", "futgg")
CONTENT_STATES = (
    "current",
    "all",
    "available",
    "started",
    "paused",
    "claimable",
    "completed",
    "expired",
)
DEFAULT_STATES = {
    "season": "all",
    "objective": "current",
    "evolution": "current",
    "sbc": "current",
}
OBJECTIVE_SECTIONS = {
    "all": None,
    "fc_objectives": "FC Objectives",
    "foundations": "Foundations",
    "milestones": "Milestones",
    "mastery": "Mastery",
    "seasonal": "Seasonal",
    "fc_pro": "FC Pro",
}
EVOLUTION_SECTIONS = {
    "all",
    "my_evolutions",
    "training_camp",
    "rewards",
    "evolutions",
    "public",
}


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
            "id": f"futgg:{row.get('id')}",
            "futgg_id": row.get("id"),
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
            "display_group": "public",
            "availability": "public",
            "source_evidence": {
                "ea": False,
                "futgg": True,
                "match_method": "unmatched_public",
            },
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
        section = str(arguments.get("section") or "all").lower()
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
        state = str(arguments.get("state") or DEFAULT_STATES[content_type]).lower()
        allowed_states = {
            "season": {"current", "all", "claimable", "completed"},
            "objective": {"current", "all", "claimable", "completed", "expired"},
            "evolution": set(CONTENT_STATES),
            "sbc": {"current", "all", "completed"},
        }[content_type]
        if state not in allowed_states:
            raise FC27Error(
                "INVALID_CONTENT_STATE",
                f"state={state} is not valid for content_type={content_type}.",
                recovery=f"Use one of: {', '.join(sorted(allowed_states))}.",
            )
        allowed_sections = {
            "season": {"all"},
            "objective": set(OBJECTIVE_SECTIONS),
            "evolution": EVOLUTION_SECTIONS,
            "sbc": {"all"},
        }[content_type]
        if section not in allowed_sections:
            raise FC27Error(
                "INVALID_CONTENT_SECTION",
                f"section={section} is not valid for content_type={content_type}.",
                recovery=f"Use one of: {', '.join(sorted(allowed_sections))}.",
            )
        return {
            "content_type": content_type,
            "source": source,
            "detail": detail,
            "section": section,
            "state": state,
            "text": str(arguments.get("text") or "").strip().casefold(),
            "limit": max(1, min(int(arguments.get("limit", 50)), 200)),
        }

    @staticmethod
    def futgg_scope(options):
        return "all" if options["state"] in {"all", "completed", "expired"} else "active"

    @classmethod
    def normalize_ea(cls, content_type, raw, options):
        metadata = {"status": raw.get("status")}
        if content_type == "season":
            items = list(raw.get("season_levels") or [])
            metadata.update(
                {
                    "campaign": raw.get("campaign"),
                    "objective_sources": raw.get("objective_sources") or [],
                }
            )
        elif content_type == "objective":
            items = []
            for category in raw.get("categories") or []:
                for group in category.get("groups") or []:
                    items.append(
                        {
                            **group,
                            "content_type": group.get("content_type")
                            or str(group.get("composite_id") or "").split("-", 1)[0]
                            or None,
                            "category_id": category.get("id"),
                            "category_name": category.get("name"),
                            "section": cls._objective_section(category.get("name")),
                        }
                    )
            metadata.update(
                {
                    "objective_sources": raw.get("objective_sources") or [],
                    "campaign": raw.get("campaign"),
                    "sections": raw.get("sections") or [],
                }
            )
        elif content_type == "evolution":
            items = [cls._prepare_ea_evolution(item) for item in raw.get("evolutions") or []]
            metadata.update(
                {
                    "categories": raw.get("categories") or [],
                    "lifecycle": raw.get("lifecycle") or {},
                }
            )
        else:
            items = list(raw.get("sets") or [])
        return cls._finish(content_type, "ea", items, options, metadata)

    @classmethod
    def normalize_futgg_evolutions(cls, raw, options):
        metadata = {
            "manifest_version": raw.get("manifest_version"),
            "manifest_key": raw.get("manifest_key"),
            "manifest_hash": raw.get("manifest_hash"),
        }
        items = [
            cls._merge_evolution(None, item, "unmatched_public")
            for item in raw.get("evolutions") or []
        ]
        return cls._finish("evolution", "futgg", items, options, metadata)

    @classmethod
    def merge_evolutions(cls, ea_raw, futgg_raw, options):
        ea_items = [cls._prepare_ea_evolution(item) for item in ea_raw.get("evolutions") or []]
        futgg_items = [dict(item) for item in futgg_raw.get("evolutions") or []]
        futgg_by_ea_id = {}
        futgg_by_name = {}
        for item in futgg_items:
            ea_id = cls._valid_ea_id(item.get("ea_id"))
            if ea_id is not None:
                futgg_by_ea_id.setdefault(ea_id, []).append(item)
            futgg_by_name.setdefault(cls._normalized_name(item.get("name")), []).append(item)

        used_futgg_ids = set()
        merged = []
        match_counts = {"ea_id": 0, "name": 0, "unmatched_account": 0, "unmatched_public": 0}
        for ea_item in ea_items:
            match = None
            method = None
            candidates = futgg_by_ea_id.get(ea_item.get("ea_id"), [])
            if len(candidates) == 1 and candidates[0].get("futgg_id") not in used_futgg_ids:
                match = candidates[0]
                method = "ea_id"
            if match is None:
                candidates = [
                    item
                    for item in futgg_by_name.get(cls._normalized_name(ea_item.get("name")), [])
                    if item.get("futgg_id") not in used_futgg_ids
                ]
                if len(candidates) == 1:
                    match = candidates[0]
                    method = "name"
            if match is not None:
                used_futgg_ids.add(match.get("futgg_id"))
                match_counts[method] += 1
                merged.append(cls._merge_evolution(ea_item, match, method))
            else:
                match_counts["unmatched_account"] += 1
                merged.append(cls._merge_evolution(ea_item, None, "unmatched_account"))

        for item in futgg_items:
            if item.get("futgg_id") in used_futgg_ids:
                continue
            match_counts["unmatched_public"] += 1
            merged.append(cls._merge_evolution(None, item, "unmatched_public"))

        metadata = {
            "ea": {
                "status": ea_raw.get("status"),
                "categories": ea_raw.get("categories") or [],
                "lifecycle": ea_raw.get("lifecycle") or {},
                "count": len(ea_items),
            },
            "futgg": {
                "manifest_version": futgg_raw.get("manifest_version"),
                "manifest_key": futgg_raw.get("manifest_key"),
                "manifest_hash": futgg_raw.get("manifest_hash"),
                "count": len(futgg_items),
            },
            "matches": match_counts,
        }
        return cls._finish("evolution", "merged", merged, options, metadata)

    @staticmethod
    def _objective_section(category_name):
        normalized = str(category_name or "").strip().casefold()
        for section, name in OBJECTIVE_SECTIONS.items():
            if name and normalized == name.casefold():
                return section
        return "all"

    @staticmethod
    def _prepare_ea_evolution(item):
        value = dict(item)
        ea_id = value.get("ea_id") or value.get("id")
        value.update(
            {
                "id": f"ea:{ea_id}",
                "ea_id": ea_id,
                "futgg_id": None,
                "requirements": [],
                "source_evidence": {
                    "ea": True,
                    "futgg": False,
                    "match_method": "unmatched_account",
                },
            }
        )
        return value

    @staticmethod
    def _valid_ea_id(value):
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None
        return normalized if normalized > 0 and normalized != 999999 else None

    @staticmethod
    def _normalized_name(value):
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(character for character in text if not unicodedata.combining(character))
        text = re.sub(r"\s*\[[^\]]+\]\s*$", "", text)
        return " ".join(text.casefold().split())

    @classmethod
    def _merge_evolution(cls, ea_item, futgg_item, match_method):
        ea_item = dict(ea_item or {})
        futgg_item = dict(futgg_item or {})
        ea_id = ea_item.get("ea_id") or cls._valid_ea_id(futgg_item.get("ea_id"))
        futgg_id = futgg_item.get("futgg_id")
        levels = cls._merge_evolution_levels(
            ea_item.get("levels") or [], futgg_item.get("levels") or []
        )
        name = ea_item.get("name") or futgg_item.get("name")
        result = {
            "id": f"ea:{ea_id}" if ea_id is not None else f"futgg:{futgg_id}",
            "ea_id": ea_id,
            "futgg_id": futgg_id,
            "name": name,
            "public_name": futgg_item.get("name") if futgg_item.get("name") != name else None,
            "description": ea_item.get("description") or futgg_item.get("description"),
            "display_group": ea_item.get("display_group") or "public",
            "availability": ea_item.get("availability") or "public",
            "category_id": ea_item.get("category_id"),
            "category_name": ea_item.get("category_name"),
            "status": ea_item.get("status"),
            "active": ea_item.get("active"),
            "started": ea_item.get("started"),
            "paused": ea_item.get("paused"),
            "claimable": ea_item.get("claimable"),
            "completed": ea_item.get("completed"),
            "expired": bool(ea_item.get("expired") or futgg_item.get("expired")),
            "timed": bool(ea_item.get("timed") or futgg_item.get("timed")),
            "enrollment_end_time": ea_item.get("enrollment_end_time") or futgg_item.get("enrollment_end_time"),
            "end_time": ea_item.get("end_time") or futgg_item.get("end_time"),
            "created_at": futgg_item.get("created_at"),
            "repeatability_count": ea_item.get("repeatability_count")
            if ea_item.get("repeatability_count") is not None
            else futgg_item.get("repeatability_count"),
            "remaining_repetitions": ea_item.get("remaining_repetitions"),
            "refresh_period": ea_item.get("refresh_period"),
            "repetition_index": ea_item.get("repetition_index"),
            "real_player_id": ea_item.get("real_player_id"),
            "training_time": ea_item.get("training_time") or futgg_item.get("training_time"),
            "player": ea_item.get("player"),
            "costs": {
                "coins": futgg_item.get("coins_cost"),
                "points": futgg_item.get("points_cost"),
                "tokens": futgg_item.get("token_cost"),
                "account": ea_item.get("costs") or [],
            },
            "requirements": futgg_item.get("requirements") or [],
            "ea_requirements": ea_item.get("ea_requirements") or [],
            "levels": levels,
            "total_upgrades": futgg_item.get("total_upgrades") or [],
            "lifecycle_evidence": ea_item.get("lifecycle_evidence") or {},
            "url": futgg_item.get("url"),
            "source_evidence": {
                "ea": bool(ea_item),
                "futgg": bool(futgg_item),
                "match_method": match_method,
            },
        }
        return result

    @staticmethod
    def _merge_evolution_levels(ea_levels, futgg_levels):
        ea_by_index = {int(level.get("index")): level for level in ea_levels if level.get("index") is not None}
        futgg_by_index = {
            int(level.get("index")): level for level in futgg_levels if level.get("index") is not None
        }
        indices = sorted(set(ea_by_index) | set(futgg_by_index))
        merged = []
        for index in indices:
            ea_level = ea_by_index.get(index, {})
            futgg_level = futgg_by_index.get(index, {})
            merged.append(
                {
                    "index": index,
                    "name": ea_level.get("name"),
                    "state": ea_level.get("state"),
                    "completed": ea_level.get("completed"),
                    "claimable": ea_level.get("claimable"),
                    "selected_award_index": ea_level.get("selected_award_index"),
                    "has_upgrade_choices": bool(
                        ea_level.get("has_upgrade_choices")
                        or futgg_level.get("has_upgrade_choices")
                    ),
                    "objectives": ea_level.get("objectives") or [],
                    "challenges": futgg_level.get("challenges") or [],
                    "upgrades": futgg_level.get("upgrades") or [],
                    "training_time": futgg_level.get("training_time"),
                    "ea_rewards": ea_level.get("ea_rewards") or [],
                }
            )
        return merged

    @classmethod
    def _finish(cls, content_type, source, items, options, metadata):
        filtered = []
        for item in items:
            if not cls._matches_section(content_type, item, options["section"]):
                continue
            if not cls._matches_state(content_type, item, options["state"]):
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
            "section": options["section"],
            "state": options["state"],
            "total_count": total_count,
            "returned_count": len(selected),
            "items": selected,
            "source_meta": metadata,
        }

    @staticmethod
    def _completed(content_type, item):
        if content_type == "season":
            standard = item.get("standard") or {}
            premium = item.get("premium")
            return bool(standard.get("claimed")) and (
                premium is None or bool(premium.get("claimed"))
            )
        return bool(item.get("completed"))

    @staticmethod
    def _expired(content_type, item):
        if content_type == "evolution":
            return bool(item.get("expired"))
        if content_type == "objective":
            end_time = item.get("end_time")
            return bool(end_time and float(end_time) > 0 and float(end_time) < time.time())
        return bool(item.get("expired"))

    @classmethod
    def _matches_state(cls, content_type, item, state):
        if state == "all":
            return True
        if state == "completed":
            return cls._completed(content_type, item)
        if state == "expired":
            return cls._expired(content_type, item)
        if state == "claimable":
            if content_type == "season":
                return any(
                    bool((item.get(track) or {}).get("claimable"))
                    for track in ("standard", "premium")
                )
            return item.get("claimable") is True
        if content_type == "season":
            unlocked = any(
                bool((item.get(track) or {}).get("unlocked"))
                for track in ("standard", "premium")
            )
            return unlocked and not cls._completed(content_type, item)
        if content_type == "evolution":
            if state == "available":
                return item.get("availability") == "account_available" and not cls._expired(content_type, item)
            if state == "started":
                return item.get("started") is True and not cls._completed(content_type, item)
            if state == "paused":
                return item.get("paused") is True and not cls._completed(content_type, item)
            return not cls._completed(content_type, item) and not cls._expired(content_type, item)
        return not cls._completed(content_type, item) and not cls._expired(content_type, item)

    @staticmethod
    def _matches_section(content_type, item, section):
        if section == "all":
            return True
        if content_type == "objective":
            return item.get("section") == section
        if content_type == "evolution":
            if section == "public":
                return item.get("availability") == "public"
            return item.get("display_group") == section
        return False

    @staticmethod
    def _search_text(item):
        return " ".join(
            str(item.get(key) or "")
            for key in (
                "name",
                "public_name",
                "title",
                "description",
                "subtitle",
                "category_name",
                "display_group",
                "section",
                "content_type",
            )
        ).casefold()

    @staticmethod
    def _sort_key(content_type, item):
        if content_type == "season":
            return (int(item.get("level") or 0),)
        end_time = item.get("end_time") or item.get("expires") or "9999"
        name = item.get("name") or item.get("title") or ""
        return (str(end_time), str(name).casefold(), str(item.get("id") or ""))

    @classmethod
    def _summary(cls, content_type, item):
        if content_type == "season":
            return {
                "level": item.get("level"),
                "required_xp": item.get("required_xp"),
                "remaining_xp": item.get("remaining_xp"),
                "standard": cls._season_track_summary(item.get("standard")),
                "premium": cls._season_track_summary(item.get("premium")),
            }
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
                    "section",
                    "content_type",
                    "objective_scope",
                    "game_mode",
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
            summary = {
                key: item.get(key)
                for key in (
                    "id",
                    "ea_id",
                    "futgg_id",
                    "name",
                    "public_name",
                    "description",
                    "category_id",
                    "category_name",
                    "display_group",
                    "availability",
                    "status",
                    "active",
                    "started",
                    "paused",
                    "completed",
                    "claimable",
                    "timed",
                    "repeatability_count",
                    "remaining_repetitions",
                    "enrollment_end_time",
                    "end_time",
                    "expired",
                    "training_time",
                    "url",
                    "source_evidence",
                )
            }
            summary["costs"] = item.get("costs") or {
                "coins": item.get("coins_cost"),
                "points": item.get("points_cost"),
                "tokens": item.get("token_cost"),
                "account": [],
            }
            summary["level_count"] = len(item.get("levels") or [])
            return summary
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

    @staticmethod
    def _season_track_summary(track):
        if track is None:
            return None
        return {
            "state": track.get("state"),
            "unlocked": track.get("unlocked"),
            "claimable": track.get("claimable"),
            "claimed": track.get("claimed"),
            "reward_count": len(track.get("rewards") or []),
        }
