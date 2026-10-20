from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playmatch.config import Settings
from playmatch.main import create_app


def game(
    identifier: str, title: str, themes: list[str], *, chinese="verified", coop="none", horror="no"
) -> dict:
    return {
        "id": identifier,
        "steam_app_id": 100 + len(identifier),
        "title": title,
        "title_zh": title,
        "description": "测试专用目录 " + " ".join(themes),
        "themes": themes,
        "genres": ["测试"],
        "platforms": ["PC"],
        "chinese_support": chinese,
        "coop": coop,
        "horror": horror,
        "image_url": "https://example.invalid/test.png",
        "store_url": "https://example.invalid/game",
        "evidence": [
            {
                "label": "synthetic test evidence, not production metadata",
                "url": "https://example.invalid/source",
                "field": "description",
                "observed_at": "2026-10-20T00:00:00Z",
            }
        ],
        "metadata_status": "curated",
        "estimated_hours": None,
    }


@pytest.fixture
def catalog():
    return [
        game("farm", "Farm", ["经营", "田园", "放松"], coop="online"),
        game("space", "Space", ["科幻", "太空", "探索"]),
        game("mystery", "Mystery", ["悬疑", "解谜", "剧情"], chinese="unknown", horror="unknown"),
        game("action", "Action", ["动作", "挑战"], coop="online", horror="yes"),
    ]


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(
        database_url="sqlite:///" + str(tmp_path / "test.sqlite"),
        llm_api_key="",
        llm_model="",
        allowed_origins="http://localhost:3000",
    )


@pytest.fixture
def client(settings, catalog):
    with TestClient(create_app(settings, catalog=catalog)) as client:
        yield client
