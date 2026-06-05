"""Minimal async client for TP-Link Cloud V2 API.

Handles HMAC-SHA1 signed requests, regional URL discovery, password login,
optional MFA, refresh-token rotation, and Kasa passthrough commands.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import ssl
import uuid
from pathlib import Path
from typing import Callable

import aiohttp

DEFAULT_HOST = "https://n-wap.tplinkcloud.com"
PATH_ACCOUNT_STATUS = "/api/v2/account/getAccountStatusAndUrl"
PATH_LOGIN = "/api/v2/account/login"
PATH_MFA_LOGIN = "/api/v2/account/checkMFACodeAndLogin"
PATH_REFRESH = "/api/v2/account/refreshToken"
PATH_PASSTHROUGH = "/"

KASA_ACCESS_KEY = "e37525375f8845999bcc56d5e6faa76d"
KASA_SECRET_KEY = "314bc6700b3140ca80bc655e527cb062"
SIGNING_TIMESTAMP = "9999999999"

APP_TYPE = "Kasa_Android_Mix"
APP_VER = "3.4.451"
USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel Build/UP1A)"

ERR_MFA_REQUIRED = -20677
ERR_TOKEN_EXPIRED = -20651
ERR_REFRESH_TOKEN_EXPIRED = -20655

CA_PATH = Path(__file__).parent / "tplink-ca-chain.pem"


class CloudError(Exception):
    pass


class MFARequired(CloudError):
    def __init__(self, mfa_email: str):
        super().__init__(f"MFA required (email: {mfa_email})")
        self.mfa_email = mfa_email


class TPLinkCloud:
    def __init__(
        self,
        email: str,
        password: str,
        refresh_token: str | None = None,
        on_refresh_token_change: Callable[[str], None] | None = None,
    ):
        self._email = email
        self._password = password
        self._refresh_token = refresh_token
        self._on_refresh = on_refresh_token_change
        self._term_id = str(uuid.uuid4())
        self._ssl = ssl.create_default_context(cafile=str(CA_PATH))
        self._host: str | None = None
        self._token: str | None = None
        self._base_params = {
            "appName": APP_TYPE,
            "appVer": APP_VER,
            "netType": "wifi",
            "termID": self._term_id,
            "ospf": "Android 14",
            "brand": "TPLINK",
            "locale": "en_US",
            "model": "Pixel",
            "termName": "Pixel",
            "termMeta": "Pixel",
        }
        self._base_headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json;charset=UTF-8",
        }

    def _signed(self, body_json: str, url_path: str) -> dict[str, str]:
        content_md5 = base64.b64encode(hashlib.md5(body_json.encode()).digest()).decode()
        nonce = str(uuid.uuid4())
        sig_string = f"{content_md5}\n{SIGNING_TIMESTAMP}\n{nonce}\n{url_path}"
        signature = hmac.new(
            KASA_SECRET_KEY.encode(), sig_string.encode(), hashlib.sha1
        ).hexdigest()
        auth = (
            f"Timestamp={SIGNING_TIMESTAMP}, Nonce={nonce}, "
            f"AccessKey={KASA_ACCESS_KEY}, Signature={signature}"
        )
        return {
            **self._base_headers,
            "Content-MD5": content_md5,
            "X-Authorization": auth,
        }

    async def _post(
        self,
        session: aiohttp.ClientSession,
        host: str,
        url_path: str,
        body: dict,
        extra_params: dict | None = None,
    ) -> dict:
        body_json = json.dumps(body, separators=(",", ":"))
        headers = self._signed(body_json, url_path)
        params = {**self._base_params, **(extra_params or {})}
        async with session.post(
            host + url_path,
            data=body_json,
            params=params,
            headers=headers,
            ssl=self._ssl,
        ) as r:
            return await r.json(content_type=None)

    async def _resolve_regional_host(self, session: aiohttp.ClientSession) -> str:
        body = {"appType": APP_TYPE, "cloudUserName": self._email}
        data = await self._post(session, DEFAULT_HOST, PATH_ACCOUNT_STATUS, body)
        if data.get("error_code", -1) != 0:
            return DEFAULT_HOST
        return data["result"].get("appServerUrl", DEFAULT_HOST)

    async def _password_login(self, session: aiohttp.ClientSession, host: str) -> dict:
        body = {
            "appType": APP_TYPE,
            "appVersion": APP_VER,
            "cloudPassword": self._password,
            "cloudUserName": self._email,
            "platform": "Android",
            "refreshTokenNeeded": True,
            "supportBindAccount": False,
            "terminalUUID": self._term_id,
            "terminalName": "Pixel",
            "terminalMeta": "Pixel",
        }
        data = await self._post(session, host, PATH_LOGIN, body)
        if data.get("error_code", -1) != 0:
            raise CloudError(f"login failed: {data}")
        result = data["result"]
        if "token" not in result and (
            result.get("errorCode") == str(ERR_MFA_REQUIRED) or "MFAProcessId" in result
        ):
            raise MFARequired(result.get("mfaEmail", self._email))
        return result

    async def _refresh(self, session: aiohttp.ClientSession, host: str) -> dict:
        body = {
            "appType": APP_TYPE,
            "refreshToken": self._refresh_token,
            "terminalUUID": self._term_id,
        }
        data = await self._post(session, host, PATH_REFRESH, body)
        if data.get("error_code", -1) != 0:
            raise CloudError(f"refresh failed: {data}")
        return data["result"]

    def _store_tokens(self, result: dict) -> None:
        self._token = result.get("token")
        new_refresh = result.get("refreshToken")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._on_refresh:
                self._on_refresh(new_refresh)

    async def _ensure_auth(self, session: aiohttp.ClientSession) -> str:
        if self._host is None:
            self._host = await self._resolve_regional_host(session)
        if self._token:
            return self._token
        if self._refresh_token:
            try:
                self._store_tokens(await self._refresh(session, self._host))
                if self._token:
                    return self._token
            except CloudError:
                pass
        self._store_tokens(await self._password_login(session, self._host))
        if not self._token:
            raise CloudError("login returned no token")
        return self._token

    async def _passthrough_with_retry(
        self,
        session: aiohttp.ClientSession,
        device_id: str,
        request: dict,
    ) -> dict:
        token = await self._ensure_auth(session)
        body = {
            "method": "passthrough",
            "params": {"deviceId": device_id, "requestData": json.dumps(request)},
        }
        data = await self._post(
            session, self._host, PATH_PASSTHROUGH, body, extra_params={"token": token}
        )
        if data.get("error_code") == ERR_TOKEN_EXPIRED:
            self._token = None
            token = await self._ensure_auth(session)
            data = await self._post(
                session, self._host, PATH_PASSTHROUGH, body, extra_params={"token": token}
            )
        if data.get("error_code", -1) != 0:
            raise CloudError(f"passthrough failed: {data}")
        return json.loads(data["result"]["responseData"])

    async def find_device_by_mac(self, mac: str) -> dict | None:
        norm = mac.upper().replace(":", "").replace("-", "")
        async with aiohttp.ClientSession() as session:
            token = await self._ensure_auth(session)
            data = await self._post(
                session,
                self._host,
                PATH_PASSTHROUGH,
                {"method": "getDeviceList"},
                extra_params={"token": token},
            )
            if data.get("error_code", -1) != 0:
                raise CloudError(f"getDeviceList failed: {data}")
            for d in data["result"]["deviceList"]:
                d_mac = (d.get("deviceMac") or "").upper().replace(":", "").replace("-", "")
                if d_mac == norm:
                    return d
        return None

    async def get_relay_state(self, device_id: str) -> bool:
        async with aiohttp.ClientSession() as session:
            resp = await self._passthrough_with_retry(
                session, device_id, {"system": {"get_sysinfo": {}}}
            )
        return bool(resp["system"]["get_sysinfo"]["relay_state"])

    async def set_relay_state(self, device_id: str, on: bool) -> bool:
        async with aiohttp.ClientSession() as session:
            await self._passthrough_with_retry(
                session,
                device_id,
                {"system": {"set_relay_state": {"state": 1 if on else 0}}},
            )
            resp = await self._passthrough_with_retry(
                session, device_id, {"system": {"get_sysinfo": {}}}
            )
        return bool(resp["system"]["get_sysinfo"]["relay_state"])
