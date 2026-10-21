"""Fixed-endpoint public search without exporting Vault secrets to tools."""
from copy import deepcopy
import json
import urllib.error
import urllib.request


class SearchRequestError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DesktopWebTools:
    def __init__(self, lifecycle, credential_id, config, *, enabled=True):
        self.enabled = enabled
        self.lifecycle = lifecycle
        self.credential_id = credential_id
        self.config = deepcopy(config)

    def available(self):
        if not self.enabled:
            return False
        try:
            self.lifecycle.web_search_runtime_headers(self.credential_id, self.config)
            return True
        except Exception:
            return False

    def request(self, endpoint, payload):
        if not self.enabled:
            raise RuntimeError("Desktop web search is unavailable in isolated execution")
        if endpoint not in {"search", "extract"} or not isinstance(payload, dict):
            raise ValueError("Unsupported web search operation")
        provider, headers = self.lifecycle.web_search_runtime_connection(self.credential_id, self.config)
        return search_request(provider, headers, endpoint, payload)

    def supports_extract(self):
        try:
            provider, _ = self.lifecycle.web_search_runtime_connection(self.credential_id, self.config)
            return self.enabled and provider != "serper"
        except Exception:
            return False


def search_request(provider, headers, endpoint, payload):
    if endpoint not in {"search", "extract"} or provider not in {"tavily", "exa", "serper"}:
        raise ValueError("Unsupported web search operation")
    if provider == "serper" and endpoint == "extract":
        raise ValueError("Serper supports search only; use the browser to read a page")
    limit = max(1, min(int(payload.get("max_results", 5)), 10))
    if provider == "tavily":
        url, body = "https://api.tavily.com/" + endpoint, payload
    elif provider == "exa":
        url = "https://api.exa.ai/" + ("search" if endpoint == "search" else "contents")
        body = {"query": payload["query"], "numResults": limit, "type": "auto", "contents": {"highlights": True}} if endpoint == "search" else {"urls": payload["urls"], "text": True}
    else:
        url, body = "https://google.serper.dev/search", {"q": payload["query"], "num": limit}
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=60) as reply:
            raw = reply.read(4 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        code = "search_auth_failed" if error.code in {401, 403} else "search_limit_reached" if error.code in {402, 429} else "search_unavailable"
        raise SearchRequestError(code, "Web search service returned HTTP " + str(error.code)) from None
    except Exception:
        raise SearchRequestError("search_unavailable", "Web search service is unavailable") from None
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("Web search response exceeds the size limit")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RuntimeError("Invalid web search response") from None
    if not isinstance(result, dict):
        raise RuntimeError("Invalid web search response")
    field = "organic" if provider == "serper" else "results"
    if result.get("error") or not isinstance(result.get(field), list):
        raise SearchRequestError("search_invalid_response", "Invalid web search response")
    if provider == "serper":
        return {"results": [{"title": item.get("title", ""), "url": item.get("link", ""), "content": item.get("snippet", "")}
                            for item in result.get("organic", []) if isinstance(item, dict)]}
    if provider == "exa":
        return {"results": [{"title": item.get("title", ""), "url": item.get("url", ""),
                              "content": "\n".join(str(value) for value in item.get("highlights", [])),
                              "raw_content": item.get("text", "")}
                             for item in result.get("results", []) if isinstance(item, dict)]}
    return result
