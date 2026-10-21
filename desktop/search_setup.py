"""Owner-only search setup using the existing encrypted values store."""
from web_tools import search_request

PROVIDERS = (
    {"id": "tavily", "name": "Tavily", "key_name": "TAVILY_API_KEY", "url": "https://app.tavily.com/", "note": "Бесплатный тариф; актуальный лимит указан в кабинете Tavily."},
    {"id": "exa", "name": "Exa", "key_name": "EXA_API_KEY", "url": "https://dashboard.exa.ai/", "note": "$10 кредитов ежемесячно без карты. Условия проверены 21.10.2026."},
    {"id": "serper", "name": "Serper", "key_name": "SERPER_API_KEY", "url": "https://serper.dev/", "note": "2500 стартовых запросов без карты; это пробный лимит. Только поиск, чтение страниц — через браузер."},
)


class SearchSetup:
    def __init__(self, lifecycle, consumer_check=lambda: True):
        self.lifecycle = lifecycle
        self.consumer_check = consumer_check

    def status(self):
        with self.lifecycle._mutex:
            values = self.lifecycle.list_values()
            return {"provider": self.lifecycle.web_search_selection(), "providers": [
                {**item, "configured": any(value["name"] == item["key_name"] and value["kind"] == "secret"
                    and value["origin"].get("kind") in {"owner", "import"} for value in values)} for item in PROVIDERS]}

    def save(self, provider, api_key, password):
        with self.lifecycle._mutex:
            self.lifecycle.authenticate(password)
            selected = next((item for item in PROVIDERS if item["id"] == provider), None)
            if selected is None and provider != "off":
                raise ValueError()
            selection = next((item for item in self.lifecycle.list_values() if item["name"] == "IGOREK_SEARCH_PROVIDER"), None)
            if selection and selection["origin"].get("kind") not in {"owner", "import"}:
                raise ValueError()
            watched = {"IGOREK_SEARCH_PROVIDER"} | ({selected["key_name"]} if selected else set())
            snapshot = {(item.id, item.encrypted_payload) for item in self.lifecycle._require().list_credentials()
                        if item.service in {"desktop-value:" + name for name in watched}}
            if provider == "off":
                key = None
            else:
                name = selected["key_name"]
                entry = next((item for item in self.lifecycle.list_values() if item["name"] == name), None)
                if entry and entry["origin"].get("kind") not in {"owner", "import"}:
                    raise ValueError()
                key = api_key.strip() if api_key else self.lifecycle._require().get_secret(entry["credential_id"]).secret if entry else ""
                if not key or any(ord(char) < 32 or ord(char) == 127 for char in key):
                    raise ValueError()
                headers = {"Authorization": "Bearer " + key} if provider == "tavily" else {"x-api-key": key}
        # A slow provider must not hold the Vault lock needed by an active task.
        if key is not None:
            search_request(provider, headers, "search", {"query": "Igorek connection check", "max_results": 1})
        with self.lifecycle._mutex:
            self.lifecycle.authenticate(password)
            if not self.consumer_check():
                raise PermissionError()
            current = {(item.id, item.encrypted_payload) for item in self.lifecycle._require().list_credentials()
                       if item.service in {"desktop-value:" + name for name in watched}}
            if current != snapshot:
                raise ValueError("Search configuration changed during the probe")
            if key is not None:
                self.lifecycle.save_value(entry["credential_id"] if entry else "", name, "secret", key, password)
            self.lifecycle.save_value(selection["credential_id"] if selection else "", "IGOREK_SEARCH_PROVIDER", "variable", provider, password)
            return self.status()
