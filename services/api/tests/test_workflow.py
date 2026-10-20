import httpx
from playmatch.config import Settings
from playmatch.workflow import build_graph, filter_candidates, infer_with_llm, retrieve, rule_intent


def intent(query, **kwargs):
    return rule_intent(
        {"query": query, "country": "US", "budget_minor": None, "exclude_owned": True, **kwargs}
    )


def test_negated_themes_are_not_positive_and_are_filtered(catalog):
    parsed = intent("想轻松经营，但不想种田，也不要动作")
    assert "田园" in parsed["excluded_themes"]
    assert "动作" in parsed["excluded_themes"]
    assert "动作" not in parsed["themes"]
    ranked = retrieve(catalog, parsed, "轻松经营", [])
    result, _ = filter_candidates(ranked, parsed, [])
    assert not any(row["game"]["id"] in {"farm", "action"} for row in result)


def test_unrelated_query_does_not_return_arbitrary_games(catalog):
    parsed = intent("赛车竞速")
    assert retrieve(catalog, parsed, "赛车竞速", []) == []


def test_partial_matches_do_not_fill_with_zero_relevance(catalog):
    parsed = intent("太空探索")
    assert [row["game"]["id"] for row in retrieve(catalog, parsed, "太空探索", [])] == ["space"]


def test_unknown_hard_constraints_never_pass(catalog):
    parsed = intent("悬疑解谜，必须中文，不要恐怖")
    result, warnings = filter_candidates(retrieve(catalog, parsed, "悬疑解谜", []), parsed, [])
    assert result == []
    assert warnings


def test_optional_chinese_is_not_a_requirement():
    for query in ("不需要中文", "不要求中文", "不一定需要中文", "中文无所谓", "Chinese optional"):
        assert intent(query)["chinese_required"] is False


def test_local_coop_not_confused_with_online(catalog):
    parsed = intent("本地合作经营游戏")
    assert parsed["coop_mode"] == "local"
    rows, _ = filter_candidates(retrieve(catalog, parsed, "经营", []), parsed, [])
    assert rows == []


def test_single_player_unknown_explained(catalog):
    parsed = intent("不要合作，只玩单人解谜")
    assert parsed["coop_required"] is False
    rows, warnings = filter_candidates(retrieve(catalog, parsed, "解谜", []), parsed, [])
    assert rows == []
    assert any("单人模式" in warning for warning in warnings)


def test_currency_mismatch_retained_not_converted():
    parsed = intent("预算100元，想玩科幻游戏")
    assert parsed["budget_minor"] is None
    assert parsed["budget_currency_conflict"]
    matched = intent("预算19.99美元，科幻")
    assert matched["budget_minor"] == 1999


def test_personal_likes_affect_ranking_without_creating_relevance(catalog):
    query = "探索经营"
    parsed = intent(query)
    without = retrieve(catalog, parsed, query, [])
    with_like = retrieve(catalog, parsed, query, [{"game_id": "space", "kind": "like"}])
    assert next(row["score"] for row in with_like if row["game"]["id"] == "space") > next(
        row["score"] for row in without if row["game"]["id"] == "space"
    )


def test_llm_error_uses_explicit_rules_fallback(catalog, monkeypatch):
    def failed(*_args, **_kwargs):
        raise httpx.ConnectError("fixture connection refused")

    monkeypatch.setattr("playmatch.workflow.infer_with_llm", failed)
    settings = Settings(llm_api_key="fixture-key", llm_model="fixture-model")
    graph = build_graph(settings, lambda *_args: None)
    result = graph.invoke(
        {
            "request": {"query": "经营", "country": "US", "budget_minor": None},
            "catalog": catalog,
            "feedback": [],
        }
    )
    assert result["mode"] == "rules"
    assert any("规则" in item for item in result["warnings"])


def test_unconfigured_llm_and_insecure_remote_url_are_rules():
    assert not Settings(llm_api_key="", llm_model="fixture").llm_enabled
    assert not Settings(llm_api_key="fixture", llm_model="fixture", llm_base_url="").llm_enabled
    assert not Settings(
        llm_api_key="fixture", llm_model="fixture", llm_base_url="http://remote.invalid/v1"
    ).llm_enabled


def test_real_model_adapter_schema_and_non_overridable_explicit_constraints(monkeypatch):
    import json

    observed = []

    def handler(request):
        observed.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fixture-key"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        parsed = {
            "themes": ["经营", "科幻"],
            "excluded_themes": [],
            "search_terms": ["科幻"],
            "chinese_required": True,
            "coop_required": True,
            "exclude_horror": False,
            "platform": "PC",
            "players": None,
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(parsed)}}]})

    original_client = httpx.Client
    monkeypatch.setattr(
        "playmatch.workflow.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    query = "不想种田，不要合作，不需要中文，想玩科幻"
    result = infer_with_llm(
        query,
        intent(query),
        Settings(
            llm_api_key="fixture-key",
            llm_model="fixture-model",
            llm_base_url="https://model.example/v1",
        ),
    )
    assert result["chinese_required"] is False
    assert result["coop_required"] is False
    assert "经营" in result["excluded_themes"]
    assert "经营" not in result["themes"]
    assert len(observed) == 1
