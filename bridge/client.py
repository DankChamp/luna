from __future__ import annotations

import httpx


class EmmaBridge:
    def __init__(self, api_url: str, api_key: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    async def is_connected(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.api_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def get_persona(self) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                resp = await client.get(f"{self.api_url}/memory?tier=persona", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("content")
        except Exception:
            pass
        return None

    async def save_to_memory(self, tier: str, content: str, tags: list[str] | None = None) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                body = {"tier": tier, "content": content}
                if tags:
                    body["tags"] = tags
                resp = await client.post(f"{self.api_url}/memory", json=body, headers=headers)
                return resp.status_code == 200
        except Exception:
            return False

    async def chat(self, message: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                resp = await client.post(
                    f"{self.api_url}/chat",
                    json={"message": message, "session_id": "luna-bridge"},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("response")
        except Exception:
            pass
        return None
