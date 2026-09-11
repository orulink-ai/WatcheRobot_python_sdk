"""Gitee token identity validation, independent of login presentation."""

import re

from .hub_http import JsonTransport, UrllibJsonTransport
from .ports import (
    AccessToken, HubAuthenticationError, HubIdentity, HubInvalidResponse,
    HubNetworkError,
)


class GiteeHubClient:
    def __init__(self, *, transport: JsonTransport | None = None) -> None:
        self._transport = transport or UrllibJsonTransport()

    def whoami(self, token: AccessToken) -> HubIdentity:
        try:
            response = self._transport.get_json(
                'https://gitee.com/api/v5/user',
                {'Authorization': f'Bearer {token.value}', 'Accept': 'application/json'},
                timeout=15.0,
            )
        except HubNetworkError:
            raise HubNetworkError('Gitee identity request failed') from None
        except HubInvalidResponse:
            raise HubInvalidResponse('Gitee returned invalid JSON') from None
        if response.status in {401, 403}:
            raise HubAuthenticationError('Gitee token is invalid or lacks permission')
        if response.status != 200:
            raise HubNetworkError('Gitee identity service is unavailable')
        username = response.payload.get('login')
        name = response.payload.get('name') or ''
        if (
            not isinstance(username, str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', username) is None
            or not isinstance(name, str)
        ):
            raise HubInvalidResponse('Gitee returned an invalid identity')
        return HubIdentity(username=username, display_name=name)
