import hashlib
import json

from .errors import FC27Error


SQUAD_SELECTIONS = ("all", "active", "exact")


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class SquadService:
    @staticmethod
    def validate_arguments(arguments):
        arguments = arguments or {}
        selection = str(arguments.get("selection") or "active").lower()
        detail = str(arguments.get("detail") or "detailed").lower()
        if selection not in SQUAD_SELECTIONS:
            raise FC27Error(
                "INVALID_SQUAD_SELECTION",
                f"selection must be one of: {', '.join(SQUAD_SELECTIONS)}.",
            )
        if detail not in ("summary", "detailed"):
            raise FC27Error(
                "INVALID_SQUAD_DETAIL", "detail must be summary or detailed."
            )
        squad_id = arguments.get("squad_id")
        if selection == "exact" and squad_id is None:
            raise FC27Error(
                "INVALID_SQUAD_SELECTION",
                "selection=exact requires squad_id.",
                recovery="Provide the squad_id returned by FC27:squad_query.",
            )
        if squad_id is not None:
            try:
                squad_id = int(squad_id)
            except (TypeError, ValueError) as error:
                raise FC27Error(
                    "INVALID_SQUAD_ID", "squad_id must be an integer."
                ) from error
        return {"selection": selection, "detail": detail, "squad_id": squad_id}

    @classmethod
    def normalize(cls, raw, options):
        active_squad_id = cls._optional_int(raw.get("active_squad_id"))
        squads = []
        for value in raw.get("squads") or []:
            squad = dict(value)
            squad_id = cls._optional_int(squad.get("squad_id"))
            squad["squad_id"] = squad_id
            squad["active"] = squad_id == active_squad_id
            if options["detail"] == "detailed":
                squad["squad_hash"] = cls.hash_squad(squad)
            squads.append(squad)
        if options["selection"] == "active":
            squads = [value for value in squads if value.get("active")]
        elif options["selection"] == "exact":
            squads = [
                value
                for value in squads
                if value.get("squad_id") == options["squad_id"]
            ]
        if options["selection"] in ("active", "exact") and not squads:
            target = active_squad_id if options["selection"] == "active" else options["squad_id"]
            raise FC27Error(
                "SQUAD_NOT_FOUND",
                f"Squad {target} was not returned by the authenticated Web App.",
                retryable=True,
                recovery="Refresh the Web App squad list and retry squad_query.",
            )
        squads.sort(key=lambda value: (not value.get("active"), value.get("name") or "", value.get("squad_id") or 0))
        return {
            "selection": options["selection"],
            "detail": options["detail"],
            "active_squad_id": active_squad_id,
            "total_count": len(squads),
            "squads": squads,
            "catalog": raw.get("catalog") if options["detail"] == "detailed" else None,
            "source_meta": {
                "status": raw.get("status"),
                "max_squads": raw.get("max_squads"),
                "list_full": bool(raw.get("list_full")),
            },
        }

    @classmethod
    def hash_squad(cls, squad):
        canonical = {
            "squad_id": cls._optional_int(squad.get("squad_id")),
            "active": bool(squad.get("active")),
            "formation_id": cls._optional_int((squad.get("formation") or {}).get("id")),
            "slots": [
                {
                    "slot_index": cls._optional_int(slot.get("slot_index")),
                    "item_id": cls._optional_int((slot.get("item") or {}).get("item_id")),
                }
                for slot in sorted(
                    squad.get("slots") or [],
                    key=lambda value: cls._optional_int(value.get("slot_index")) or -1,
                )
            ],
            "active_tactic_id": cls._optional_int(squad.get("active_tactic_id")),
            "tactics": [cls._canonical_tactic(value) for value in squad.get("tactics") or []],
        }
        return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()

    @classmethod
    def _canonical_tactic(cls, tactic):
        return {
            "id": cls._optional_int(tactic.get("id")),
            "name": tactic.get("name"),
            "state": cls._optional_int(tactic.get("state")),
            "formation_id": cls._optional_int((tactic.get("formation") or {}).get("id")),
            "defensive_style": cls._optional_int(tactic.get("defensive_style")),
            "defensive_line_height": cls._optional_int(tactic.get("defensive_line_height")),
            "build_up_play_style": cls._optional_int(tactic.get("build_up_play_style")),
            "positions": [int(value) for value in tactic.get("positions") or []],
            "instructions": [
                {
                    "slot_index": cls._optional_int(value.get("slot_index")),
                    "position": cls._optional_int(value.get("position")),
                    "role_id": cls._optional_int(value.get("role_id")),
                    "variation_id": cls._optional_int(value.get("variation_id")),
                }
                for value in tactic.get("instructions") or []
            ],
        }

    @staticmethod
    def _optional_int(value):
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
