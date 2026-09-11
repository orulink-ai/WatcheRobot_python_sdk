import base64
import hashlib

import pytest

from watcherobot.distribution.gitee_public import GiteePublicRepository
from watcherobot.distribution.hub_http import JsonResponse
from watcherobot.distribution.ports import HubFileNotFound, HubInvalidResponse, HubNetworkError

SHA = 'a' * 40


def file_payload(content=b'[]\n'):
    return {
        'type': 'file', 'encoding': 'base64', 'size': len(content),
        'content': base64.b64encode(content).decode(),
        'sha': hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest(),
    }


class Transport:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.urls = []

    def get_json(self, url, headers, *, timeout):
        assert 'Authorization' not in headers
        self.urls.append(url)
        return JsonResponse(200, next(self.responses))


def test_catalog_resolves_branch_then_reads_fixed_commit():
    transport = Transport({'commit': {'sha': SHA}}, file_payload())
    document = GiteePublicRepository(transport=transport).read_public_catalog(
        repo_id='orulink-sz/watcherobot-app-store', path='app-list.json',
    )
    assert document.commit == SHA
    assert document.content == b'[]\n'
    assert transport.urls[0].endswith('/branches/master')
    assert transport.urls[1].endswith('/contents/app-list.json?ref=' + SHA)


@pytest.mark.parametrize('payload', [[], {}, {'type': 'dir'}, {**file_payload(), 'sha': 'b' * 40}, {**file_payload(), 'content': '!!!'}])
def test_invalid_file_responses_fail_closed(payload):
    client = GiteePublicRepository(transport=Transport({'sha': SHA}, payload))
    with pytest.raises(HubInvalidResponse):
        client.read_file(repo_id='owner/app', commit=SHA, path='app.json')


@pytest.mark.parametrize('repo,commit,path', [('bad', SHA, 'app.json'), ('owner/app', 'master', 'app.json'), ('owner/app', SHA, '../token'), ('owner/app', SHA, 'a?ref=master')])
def test_invalid_references_never_request_network(repo, commit, path):
    transport = Transport()
    with pytest.raises(HubInvalidResponse):
        GiteePublicRepository(transport=transport).read_file(repo_id=repo, commit=commit, path=path)
    assert transport.urls == []


def test_mismatched_revision_is_rejected_before_file_read():
    transport = Transport({'sha': 'b' * 40})
    with pytest.raises(HubInvalidResponse):
        GiteePublicRepository(transport=transport).read_file(repo_id='owner/app', commit=SHA, path='app.json')
    assert len(transport.urls) == 1


@pytest.mark.parametrize('status,error', [(404, HubFileNotFound), (401, HubNetworkError), (429, HubNetworkError), (500, HubNetworkError)])
def test_http_failures_are_sanitized(status, error):
    class FailedTransport:
        def get_json(self, url, headers, *, timeout):
            return JsonResponse(status, {'message': 'private-server-details'})
    with pytest.raises(error) as exc:
        GiteePublicRepository(transport=FailedTransport()).read_public_catalog()
    assert 'private-server-details' not in str(exc.value)


@pytest.mark.parametrize('changes', [{'size': True}, {'size': 1024 * 1024 + 1}, {'size': 0}, {'encoding': 'plain'}, {'type': 'symlink'}])
def test_file_limits_and_type_are_enforced(changes):
    payload = {**file_payload(), **changes}
    with pytest.raises(HubInvalidResponse):
        GiteePublicRepository(transport=Transport({'sha': SHA}, payload)).read_file(
            repo_id='owner/app', commit=SHA, path='app.json',
        )
