"""Exact Steam-ID resolution through IGDB. Never title-match, translate prose or infer negatives."""

import asyncio
from datetime import UTC, datetime

from playmatch.catalog_collect import FIELDS, mapped_steam_id, normalize_collected
from playmatch.catalog_import import ImportItem, digest, normalized
from playmatch.providers.cheapshark import ProviderRequestError
from playmatch.schemas import Game

POLICY = "steam-igdb-conservative-v1"
FIELDS_EXTRA = (
    ",language_supports.language.name,language_supports.language.locale,"
    "language_supports.language_support_type.name,"
    "multiplayer_modes.platform,multiplayer_modes.onlinecoop,multiplayer_modes.offlinecoop,"
    "game_modes.name"
)
# Exact taxonomy labels -> editorial query vocabulary. Not generated gameplay facts.
TAXONOMY_ZH = {
    "science fiction": "科幻",
    "fantasy": "奇幻",
    "horror": "恐怖",
    "survival": "生存",
    "sandbox": "沙盒",
    "open world": "开放世界",
    "action": "动作",
    "stealth": "潜行",
    "mystery": "悬疑",
    "historical": "历史",
    "comedy": "喜剧",
    "warfare": "战争",
    "puzzle": "解谜",
    "adventure": "冒险",
    "role-playing (rpg)": "角色扮演",
    "strategy": "策略",
    "real time strategy (rts)": "即时战略",
    "simulator": "模拟",
    "turn-based strategy (tbs)": "回合制策略",
    "racing": "竞速",
    "sport": "体育",
    "platform": "平台跳跃",
    "shooter": "射击",
    "music": "音乐",
    "visual novel": "视觉小说",
}


def enrich_record(raw: dict, appid: int, observed_at: str) -> tuple[dict | None, str | None]:
    item, error = normalize_collected(raw)
    if error:
        return None, error
    if item["steam_app_id"] != appid:
        return None, "identity_conflict"
    updated = raw.get("updated_at")
    observed = datetime.fromisoformat(observed_at).timestamp()
    if type(updated) is not int or not 0 < updated <= observed + 300:
        return None, "invalid_source_timestamp"
    payload = normalized(ImportItem.model_validate(item), observed_at)
    # Keep the original provider text. title_zh is a display fallback, not a translation.
    labels = [*item["themes"], *item["genres"]]
    payload["themes"] = list(
        dict.fromkeys(
            [
                *item["themes"],
                *[TAXONOMY_ZH[v.casefold()] for v in labels if v.casefold() in TAXONOMY_ZH],
            ]
        )
    )
    payload["evidence"] = [
        {
            "field": field,
            "label": f"IGDB {field} 来源记录；非官方独立核验",
            "url": item["source_url"],
            "observed_at": observed_at,
        }
        for field in ("metadata", "description", "themes", "platforms")
    ]

    def evidence(field, label):
        payload["evidence"].append(
            {"field": field, "label": label, "url": item["source_url"], "observed_at": observed_at}
        )

    # IGDB language supports are game-wide, not platform-scoped: only a PC-only
    # record is used as positive PC interface evidence. Missing != unsupported.
    pc_only = item["platforms"] == ["PC (Microsoft Windows)"]
    languages = raw.get("language_supports", [])
    if isinstance(languages, list):
        for support in languages[:100]:
            if not isinstance(support, dict):
                continue
            language, kind = support.get("language", {}), support.get("language_support_type", {})
            if not isinstance(language, dict) or not isinstance(kind, dict):
                continue
            locale = str(language.get("locale", "")).casefold()
            name = str(language.get("name", "")).casefold()
            if (
                locale in {"zh-cn", "zh-hans"}
                or name in {"chinese (simplified)", "simplified chinese"}
            ) and str(kind.get("name", "")).casefold() == "interface":
                evidence(
                    "language_support_report",
                    "IGDB 报告简体中文界面；跨平台记录不直接核验 Steam 版本",
                )
                if pc_only:
                    payload["chinese_support"] = "verified"
                    evidence(
                        "chinese_support",
                        "IGDB 仅 PC 记录明确列出简体界面；供应商资料，不代表配音或其他版本",
                    )
                break
    modes = raw.get("multiplayer_modes", [])
    if isinstance(modes, list):
        pc_modes = [
            m
            for m in modes
            if isinstance(m, dict) and type(m.get("platform")) is int and m["platform"] == 6
        ]
        if any(m.get("onlinecoop") is True for m in pc_modes):
            payload["coop"] = "online"
        elif any(m.get("offlinecoop") is True for m in pc_modes):
            payload["coop"] = "local"
        if payload["coop"] != "unknown":
            evidence("coop", "IGDB PC 平台合作记录；人数上限尚未核验")
    if any(v.casefold() == "horror" for v in item["themes"]):
        payload["horror"] = "yes"
        evidence("horror", "IGDB 明确标注 Horror 主题；未标注不代表无恐怖")
    modes = raw.get("game_modes", [])
    if (
        pc_only
        and isinstance(modes, list)
        and any(
            isinstance(m, dict) and str(m.get("name", "")).casefold() == "single player"
            for m in modes
        )
    ):
        evidence("single_player", "IGDB 仅 PC 记录明确标注单人模式")
    checked = Game.model_validate(payload).model_dump()
    semantic = {
        **checked,
        "evidence": [
            {k: v for k, v in row.items() if k != "observed_at"} for row in checked["evidence"]
        ],
    }
    return {
        "payload": checked,
        "provider_id": item["provider_id"],
        "updated_at": updated,
        "hash": digest([POLICY, semantic]),
    }, None


async def lookup_batch(client, appids: list[int]) -> dict[int, dict]:
    if (
        not appids
        or len(appids) > 10
        or len(appids) != len(set(appids))
        or any(type(i) is not int or not 0 < i <= 4294967295 for i in appids)
    ):
        raise ValueError("Invalid Steam batch")
    # Integer-only IDs prevent APICalypse injection; validate returned identities again.
    uids = ",".join(f'"{i}"' for i in appids)
    body = f'fields {FIELDS}{FIELDS_EXTRA}; where external_games.uid = ({uids}) & external_games.external_game_source.name = "Steam"; sort id asc; limit 51;'
    async with asyncio.timeout(25):
        rows = await client.catalog_page(body)
    if len(rows) >= 51:
        raise ProviderRequestError("ambiguous_response_limit")
    observed_at = datetime.now(UTC).isoformat()
    resolved = {i: [] for i in appids}
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderRequestError("invalid_response")
        mapped = mapped_steam_id(row)
        if mapped in resolved:
            resolved[mapped].append(row)
        else:
            # A malformed/contradictory Steam mapping might overlap one requested id.
            # Reject the batch rather than selecting another apparently valid match.
            raise ProviderRequestError("identity_conflict")
    result = {}
    for appid, matches in resolved.items():
        if len(matches) != 1:
            result[appid] = {"error": "unmatched" if not matches else "ambiguous_mapping"}
            continue
        value, error = enrich_record(matches[0], appid, observed_at)
        result[appid] = {"error": error} if error else {**value, "observed_at": observed_at}
    return result
