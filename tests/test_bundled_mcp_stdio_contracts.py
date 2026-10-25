"""Bundled MCP adapters retain protocol framing and provider error details."""

import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import pytest

from factory.mcp_servers import coingecko_server as coins
from factory.mcp_servers import tavily_server as tavily


@pytest.fixture(autouse=True)
def isolate_provider_environment(monkeypatch, tmp_path):
    for key in ("TAVILY_API_KEY", "FACTORY_TAVILY_API_KEY", "COINGECKO_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    fixture_file = tmp_path / "factory/mcp_servers/fixture.py"
    fixture_file.parent.mkdir(parents=True)
    for module in (coins, tavily):
        monkeypatch.setattr(module, "__file__", str(fixture_file))
    forbidden = Mock(side_effect=AssertionError("Unexpected external provider request"))
    monkeypatch.setattr(tavily.urllib_request, "urlopen", forbidden)
    monkeypatch.setattr(coins.urllib_request, "build_opener", forbidden)
    return fixture_file.parents[1]


class Output(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self):
        self.flushes += 1


def exchange(module, monkeypatch, messages):
    output = Output()
    raw = "\n".join(
        item if isinstance(item, str) else json.dumps(item) for item in messages
    )
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(module.sys, "stdout", output)
    module.main()
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert output.flushes == len(responses)
    assert all(row["jsonrpc"] == "2.0" for row in responses)
    return responses


@pytest.mark.parametrize("module", [coins, tavily], ids=["coingecko", "tavily"])
def test_protocol_initialization_catalog_and_notifications(module, monkeypatch):
    responses = exchange(
        module,
        monkeypatch,
        [
            "",
            {"jsonrpc": "2.0", "id": "init", "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 10, "method": "unsupported"},
        ],
    )
    assert [row["id"] for row in responses] == ["init", 9, 10]
    info = responses[0]["result"]
    assert info == {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": module.SERVER_NAME, "version": module.SERVER_VERSION},
    }
    tools = responses[1]["result"]["tools"]
    expected = (
        {"coingecko_price_history", "coingecko_price_current", "coingecko_search"}
        if module is coins
        else {"tavily_search", "tavily_extract"}
    )
    assert {tool["name"] for tool in tools} == expected
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)
    assert responses[2]["error"]["code"] == -32601


@pytest.mark.parametrize("module", [coins, tavily], ids=["coingecko", "tavily"])
def test_dotenv_priority_preserves_inherited_values(
    module, monkeypatch, isolate_provider_environment
):
    root = isolate_provider_environment
    monkeypatch.setenv("MCP_FIXTURE_INHERITED", "process")
    for name, text in {
        ".env.gateway": "# comment\n\nignored\nMCP_FIXTURE_INHERITED=must-not-overwrite\nMCP_FIXTURE_FIRST='gateway'\nMCP_FIXTURE_QUOTED=\"hello world\"\n",
        ".env": "MCP_FIXTURE_FIRST=second\nMCP_FIXTURE_SECOND=env=value\n",
        ".env.local": "MCP_FIXTURE_LOCAL=local\n",
    }.items():
        (root / name).write_text(text)
    with patch.dict(module.os.environ):
        module._load_dotenv()
        assert module.os.environ["MCP_FIXTURE_INHERITED"] == "process"
        assert module.os.environ["MCP_FIXTURE_FIRST"] == "gateway"
        assert module.os.environ["MCP_FIXTURE_QUOTED"] == "hello world"
        assert module.os.environ["MCP_FIXTURE_SECOND"] == "env=value"
        assert module.os.environ["MCP_FIXTURE_LOCAL"] == "local"


def test_tavily_ping_and_bad_json_do_not_prevent_next_request(monkeypatch):
    responses = exchange(
        tavily, monkeypatch, ["{", {"jsonrpc": "2.0", "id": 7, "method": "ping"}]
    )
    assert responses[0]["error"]["code"] == -32000
    assert responses[0]["id"] is None
    assert responses[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_coingecko_bad_json_and_notifications_do_not_create_output(monkeypatch):
    responses = exchange(
        coins,
        monkeypatch,
        ["{", {"method": "notifications/cancelled"}, {"id": 5, "method": "tools/list"}],
    )
    assert len(responses) == 1 and responses[0]["id"] == 5


@pytest.mark.parametrize("arguments", [None, [], "wrong"])
def test_coingecko_nonobject_arguments_use_handler_defaults(monkeypatch, arguments):
    handler = Mock(return_value={"fixture": "result"})
    monkeypatch.setitem(coins._TOOLS_MAP, "fixture_tool", handler)
    response = exchange(
        coins,
        monkeypatch,
        [
            {
                "id": 3,
                "method": "tools/call",
                "params": {"name": "fixture_tool", "arguments": arguments},
            }
        ],
    )[0]
    handler.assert_called_once_with()
    assert json.loads(response["result"]["content"][0]["text"]) == {"fixture": "result"}


def test_coingecko_ignores_common_output_directory_argument(monkeypatch):
    handler = Mock(return_value={"fixture": "данные"})
    monkeypatch.setitem(coins._TOOLS_MAP, "fixture_tool", handler)
    response = exchange(
        coins,
        monkeypatch,
        [
            {
                "id": "call",
                "method": "tools/call",
                "params": {
                    "name": "fixture_tool",
                    "arguments": {"query": "fixture", "output_dir": "/unused"},
                },
            }
        ],
    )[0]
    handler.assert_called_once_with(query="fixture")
    assert json.loads(response["result"]["content"][0]["text"])["fixture"] == "данные"


@pytest.mark.parametrize(
    "failure,code",
    [
        (TypeError("fixture arguments"), -32602),
        (RuntimeError("fixture provider unavailable"), -32000),
    ],
)
def test_coingecko_handler_errors_are_framed_and_next_call_survives(
    monkeypatch, failure, code
):
    handler = Mock(side_effect=[failure, {"ok": True}])
    monkeypatch.setitem(coins._TOOLS_MAP, "fixture_tool", handler)
    call = lambda identifier: {
        "id": identifier,
        "method": "tools/call",
        "params": {"name": "fixture_tool"},
    }
    responses = exchange(coins, monkeypatch, [call(1), call(2)])
    assert responses[0]["error"]["code"] == code
    assert str(failure) in responses[0]["error"]["message"]
    assert json.loads(responses[1]["result"]["content"][0]["text"]) == {"ok": True}


def test_coingecko_unknown_tool_is_reported(monkeypatch):
    response = exchange(
        coins,
        monkeypatch,
        [{"id": 6, "method": "tools/call", "params": {"name": "not-enrolled"}}],
    )[0]
    assert response["error"] == {
        "code": -32601,
        "message": "Tool not found: not-enrolled",
    }


@pytest.mark.parametrize(
    "name,arguments,detail",
    [
        ("tavily_search", {}, "query"),
        ("tavily_search", {"query": "  "}, "query"),
        ("tavily_extract", {}, "urls"),
        ("tavily_extract", {"urls": "wrong"}, "urls"),
        ("unknown", {}, "Неизвестный"),
    ],
)
def test_tavily_bad_tool_arguments_return_error_and_keep_loop_usable(
    monkeypatch, name, arguments, detail
):
    responses = exchange(
        tavily,
        monkeypatch,
        [
            {
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            {"id": 2, "method": "ping"},
        ],
    )
    assert responses[0]["error"]["code"] == -32000
    assert detail in responses[0]["error"]["message"]
    assert responses[1]["result"] == {}


@pytest.mark.parametrize(
    "name,arguments",
    [
        (
            "tavily_search",
            {"query": " fixture query ", "max_results": 3, "search_depth": "advanced"},
        ),
        ("tavily_extract", {"urls": ["https://fixture.invalid"]}),
    ],
)
def test_tavily_dispatch_formats_actual_provider_result(monkeypatch, name, arguments):
    search = Mock(
        return_value={
            "answer": "brief",
            "results": [
                {
                    "title": "Result",
                    "url": "https://fixture.invalid",
                    "content": "fixture body",
                }
            ],
        }
    )
    extract = Mock(
        return_value={
            "results": [
                {"url": "https://fixture.invalid", "raw_content": "fixture body"}
            ]
        }
    )
    monkeypatch.setattr(tavily, "_tavily_search", search)
    monkeypatch.setattr(tavily, "_tavily_extract", extract)
    response = exchange(
        tavily,
        monkeypatch,
        [
            {
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ],
    )[0]
    assert "fixture body" in response["result"]["content"][0]["text"]
    if name == "tavily_search":
        search.assert_called_once_with("fixture query", 3, "advanced")
        extract.assert_not_called()
    else:
        extract.assert_called_once_with(arguments["urls"])
        search.assert_not_called()


@pytest.mark.parametrize(
    "main,secondary,expected",
    [
        (" key-one ", "key-two", "key-one"),
        ("", " key-two ", "key-two"),
        ("key-one", "", "key-one"),
    ],
)
def test_tavily_key_resolution_preserves_precedence(
    monkeypatch, main, secondary, expected
):
    monkeypatch.setenv("TAVILY_API_KEY", main)
    monkeypatch.setenv("FACTORY_TAVILY_API_KEY", secondary)
    assert tavily._resolve_api_key() == expected


def test_tavily_missing_key_never_contacts_network():
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        tavily._tavily_extract(["https://fixture.invalid"])


def response_bytes(data):
    response = io.BytesIO(data)
    return response


@pytest.mark.parametrize(
    "maximum,depth,expected_max,expected_depth",
    [
        (0, "wrong", 1, "basic"),
        (50, "advanced", 10, "advanced"),
        (5, "basic", 5, "basic"),
    ],
)
def test_tavily_search_request_bounds_results_and_preserves_query(
    monkeypatch, maximum, depth, expected_max, expected_depth
):
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-key")
    open_url = Mock(return_value=response_bytes(b'{"results":[]}'))
    monkeypatch.setattr(tavily.urllib_request, "urlopen", open_url)
    assert tavily._tavily_search("Привет", maximum, depth) == {"results": []}
    request = open_url.call_args.args[0]
    assert request.full_url == tavily.TAVILY_SEARCH_URL and request.method == "POST"
    assert json.loads(request.data) == {
        "api_key": "fixture-key",
        "query": "Привет",
        "max_results": expected_max,
        "search_depth": expected_depth,
        "include_answer": True,
        "include_raw_content": False,
    }
    assert open_url.call_args.kwargs == {"timeout": 30}


def test_tavily_extract_limits_urls_without_mutating_input(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-key")
    urls = [f"https://fixture.invalid/{i}" for i in range(13)]
    original = list(urls)
    open_url = Mock(return_value=response_bytes(b'{"results":[]}'))
    monkeypatch.setattr(tavily.urllib_request, "urlopen", open_url)
    assert tavily._tavily_extract(urls) == {"results": []}
    request = open_url.call_args.args[0]
    assert request.full_url == tavily.TAVILY_EXTRACT_URL
    assert json.loads(request.data) == {"api_key": "fixture-key", "urls": original[:10]}
    assert urls == original


@pytest.mark.parametrize("operation", ["search", "extract"])
def test_tavily_http_error_retains_provider_detail(monkeypatch, operation):
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-key")
    error = HTTPError(
        "https://fixture.invalid", 429, "rate limited", {}, io.BytesIO(b"fixture quota")
    )
    monkeypatch.setattr(tavily.urllib_request, "urlopen", Mock(side_effect=error))
    with pytest.raises(RuntimeError, match="HTTP 429: fixture quota") as raised:
        if operation == "search":
            tavily._tavily_search("fixture", 5, "basic")
        else:
            tavily._tavily_extract(["https://fixture.invalid"])
    assert raised.value.__cause__ is error


@pytest.mark.parametrize("payload", [{}, {"results": []}, {"results": None}])
def test_tavily_empty_results_have_clear_messages(payload):
    assert "ничего не найдено" in tavily._format_search_response(payload, "fixture")
    assert "Не удалось извлечь" in tavily._format_extract_response(payload)


def test_tavily_formatters_bound_content_and_keep_failures():
    search = tavily._format_search_response(
        {
            "answer": "brief",
            "results": [
                {
                    "title": " Title ",
                    "url": " https://fixture.invalid ",
                    "content": "x" * 1600,
                },
                {},
            ],
        },
        "fixture",
    )
    assert "brief" in search and "Заголовок: Title" in search
    assert "URL: https://fixture.invalid" in search
    assert "x" * 1500 in search and "x" * 1501 not in search
    text = tavily._format_extract_response(
        {
            "results": [{"url": "a", "raw_content": "y" * 5100}, {"url": "empty"}],
            "failed_results": [
                {"url": "failed", "error": "fixture denied"},
                {"url": "other"},
            ],
        }
    )
    assert "y" * 5000 in text and "y" * 5001 not in text
    assert "(пустой ответ)" in text and "fixture denied" in text and "unknown" in text


@pytest.mark.parametrize(
    "key,path,query",
    [
        ("", "/coins", {}),
        (" fixture-key ", "/coins", {"x_cg_demo_api_key": ["fixture-key"]}),
        (
            "fixture-key",
            "/coins?vs=usd",
            {"vs": ["usd"], "x_cg_demo_api_key": ["fixture-key"]},
        ),
    ],
)
def test_coingecko_key_is_optional_and_query_separator_is_correct(
    monkeypatch, key, path, query
):
    monkeypatch.setenv("COINGECKO_API_KEY", key)
    assert parse_qs(urlsplit(coins._api_url(path)).query) == query


@pytest.mark.parametrize("days,expected", [(7, 7), ("14", 14), ("wrong", 3), (None, 3)])
def test_coingecko_history_encodes_identifiers_and_preserves_series(
    monkeypatch, days, expected
):
    get = Mock(
        return_value={
            "prices": [[1, 2]],
            "market_caps": [[1, 3]],
            "total_volumes": [[1, 4]],
        }
    )
    monkeypatch.setattr(coins, "_http_get", get)
    result = coins._history("coin/name", "us d", days)
    url = get.call_args.args[0]
    assert "/coins/coin%2Fname/market_chart" in url
    assert parse_qs(urlsplit(url).query) == {
        "vs_currency": ["us d"],
        "days": [str(expected)],
    }
    assert result == {
        "coin_id": "coin/name",
        "vs_currency": "us d",
        "days": expected,
        "data_points": 1,
        "prices": [[1, 2]],
        "market_caps": [[1, 3]],
        "total_volumes": [[1, 4]],
    }


def test_coingecko_missing_history_series_are_empty(monkeypatch):
    monkeypatch.setattr(coins, "_http_get", lambda url: {})
    result = coins._history()
    assert result == {
        "coin_id": "bitcoin",
        "vs_currency": "usd",
        "days": 3,
        "data_points": 0,
        "prices": [],
        "market_caps": [],
        "total_volumes": [],
    }


@pytest.mark.parametrize(
    "ids,currencies,expected_ids,expected_currencies",
    [
        (" bitcoin, ethereum, ", " usd, eur, ", "bitcoin,ethereum", "usd,eur"),
        (", ,", " ", "bitcoin", "usd"),
    ],
)
def test_coingecko_current_price_normalizes_lists_and_timestamps(
    monkeypatch, ids, currencies, expected_ids, expected_currencies
):
    get = Mock(return_value={"bitcoin": {"usd": 123}})
    monkeypatch.setattr(coins, "_http_get", get)
    monkeypatch.setattr(coins.time, "time", lambda: 12.345)
    assert coins._current_price(ids, currencies) == {
        "prices": {"bitcoin": {"usd": 123}},
        "queried_at_unix_ms": 12345,
    }
    query = parse_qs(urlsplit(get.call_args.args[0]).query)
    assert query == {
        "ids": [expected_ids],
        "vs_currencies": [expected_currencies],
        "include_24hr_change": ["true"],
        "include_market_cap": ["true"],
        "include_24hr_vol": ["true"],
    }


def test_coingecko_search_caps_result_count_and_selects_public_fields(monkeypatch):
    get = Mock(
        return_value={
            "coins": [
                {
                    "id": str(i),
                    "name": "coin",
                    "symbol": "C",
                    "market_cap_rank": i,
                    "unneeded": "omit",
                }
                for i in range(25)
            ]
        }
    )
    monkeypatch.setattr(coins, "_http_get", get)
    result = coins._search("coin & token")
    assert len(result["results"]) == 20
    assert result["results"][0] == {
        "id": "0",
        "name": "coin",
        "symbol": "C",
        "market_cap_rank": 0,
    }
    assert parse_qs(urlsplit(get.call_args.args[0]).query) == {
        "query": ["coin & token"]
    }
    get.return_value = {"coins": [{}]}
    assert coins._search("fixture")["results"] == [
        {"id": "", "name": "", "symbol": "", "market_cap_rank": None}
    ]
    get.return_value = {}
    assert coins._search("fixture")["results"] == []


@pytest.mark.parametrize("error_kind", ["http", "url", "json"])
def test_coingecko_http_errors_keep_bounded_detail(monkeypatch, error_kind):
    if error_kind == "http":
        error = HTTPError(
            "https://fixture.invalid", 429, "limited", {}, io.BytesIO(b"x" * 700)
        )
    elif error_kind == "url":
        error = URLError("fixture unavailable")
    else:
        error = None
    opener = Mock()
    if error is None:
        opener.open.return_value = response_bytes(b"not JSON")
    else:
        opener.open.side_effect = error
    monkeypatch.setattr(coins.urllib_request, "build_opener", Mock(return_value=opener))
    with pytest.raises(RuntimeError) as raised:
        coins._http_get("https://fixture.invalid/api")
    if error_kind == "http":
        assert "HTTP 429" in str(raised.value)
        assert "x" * 500 in str(raised.value) and "x" * 501 not in str(raised.value)
    elif error_kind == "url":
        assert "fixture unavailable" in str(raised.value)
    else:
        assert "bad JSON response" in str(raised.value)
    assert raised.value.__cause__ is not None


def test_coingecko_get_uses_explicit_proxy_policy_and_json(monkeypatch):
    opener = Mock()
    opener.open.return_value = response_bytes(b'{"fixture":"result"}')
    build = Mock(return_value=opener)
    monkeypatch.setattr(coins.urllib_request, "build_opener", build)
    assert coins._http_get("https://fixture.invalid/api", timeout_s=17) == {
        "fixture": "result"
    }
    proxy = build.call_args.args[0]
    assert proxy.proxies == {}
    request = opener.open.call_args.args[0]
    assert request.method == "GET" and request.full_url == "https://fixture.invalid/api"
    assert request.get_header("Content-type") == "application/json"
    assert opener.open.call_args.kwargs == {"timeout": 17}
