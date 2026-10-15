def http_curl_prompt_rules() -> str:
    return (
        "When internet access is needed, prefer Bash with curl, Tavily-backed flow, or direct HTTP requests instead of WebSearch/WebFetch.\n"
        "If the user asks for the current weather in a city and does not specify a source, prefer Bash with curl against wttr.in instead of Tavily research.\n"
        "For generic current-weather questions, default to wttr.in JSON output.\n"
        "If you generate a Bash curl command with a URL that contains `?` or `&`, you MUST wrap the full URL in double quotes.\n"
        "Never emit curl URLs with raw query separators outside quotes.\n"
        "For CoinGecko price requests, prefer the official API form, for example:\n"
        "{\"tool\":\"Bash\",\"input\":{\"command\":\"curl -s \\\"https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd\\\"\"}}\n"
        "For wttr.in requests, never replace a user-provided city with a guessed 3-letter code.\n"
        "Prefer explicit city names in the URL path.\n"
        "For example:\n"
        "{\"tool\":\"Bash\",\"input\":{\"command\":\"curl -s \\\"https://wttr.in/Moscow?format=j1\\\"\"}}\n"
        "To run Python scripts, always use `uv run <script.py>`. If `uv` is unavailable, use `python3 <script.py>`. "
        "Never use bare `python`. "
        "Correct: `uv run main.py`. "
        "Wrong: `uv run python3 main.py` or `python main.py`.\n"
    )


def failure_recovery_prompt_rules() -> str:
    return (
        "Do not explain the error to the user. "
        "Do not apologize. "
        "Do not print a corrected command as plain text. "
        "If you emit a brief visible preamble before the tool call, make it explicit that this is a retry/correction. "
        "Instead, immediately make another tool attempt that fixes the problem. "
        "Generate a new tool call only if possible. "
        "If a Bash command fails with 'command not found' or exit code 127 for `python`, "
        "retry using `python3` or `uv run` instead of `python`. "
        "Example: if `python main.py` failed, run `uv run main.py` or `python3 main.py`. "
        "If you generate a Bash curl command with a URL that contains query parameters, you MUST wrap the full URL in double quotes. "
        "Never emit curl URLs with raw `&` outside quotes. "
        "For CoinGecko price requests, prefer the official API endpoint, for example: "
        "curl -s \"https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd\" "
        "For wttr.in requests, never replace a user-provided city with a guessed 3-letter code. "
        "Prefer explicit city names in the URL path, for example: "
        "curl -s \"https://wttr.in/Moscow?format=j1\""
    )
