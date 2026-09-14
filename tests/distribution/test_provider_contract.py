import pytest
import json
from watcherobot.distribution.publish import publish_application
from watcherobot.distribution.submit import submit_application
from watcherobot.distribution.providers import get_provider
from watcherobot.distribution.catalog_submission import catalog_pull_request_title
from watcherobot.distribution.ports import AccessToken, CatalogDocument, CatalogPullRequest
from tests.distribution._publishing_fakes import (
    SPACE_ID, SPACE_COMMIT, CATALOG_COMMIT, FakeCredentialStore,
    FakeIdentityHub, FakePublishHub, RecordingEvents, write_application,
)
from watcherobot.distribution.download import download_application_snapshot
from watcherobot.distribution.install import install_application
from watcherobot.distribution.marketplace import load_official_marketplace
from tests.distribution.test_marketplace import FakeMarketplaceHub, _catalog, _manifest


@pytest.mark.parametrize('provider', ['huggingface', 'gitee'])
def test_provider_marketplace_reads_own_catalog_and_fixed_manifest(provider):
    hub = FakeMarketplaceHub(
        catalog=_catalog([{'repo_id': SPACE_ID, 'commit': SPACE_COMMIT}]),
        manifests={(SPACE_ID, SPACE_COMMIT): _manifest('com.orulink.demo', name='示例')},
    )
    result = load_official_marketplace(
        provider=provider, hub=hub, watcherobot_version='1.5.0',
    )
    assert len(result.to_dict()['applications']) == 1
    assert hub.calls == [
        ('catalog', (get_provider(provider).catalog, 'app-list.json')),
        ('file', (SPACE_ID, SPACE_COMMIT, 'app.json')),
    ]


def test_unknown_provider_fails_before_local_side_effects(tmp_path):
    with pytest.raises(ValueError):
        install_application(
            provider="unknown",
            repo_id="user/app",
            commit="a" * 40,
            store_root=tmp_path / "store",
            runtime_root=tmp_path / "runtime",
            hub=None,
        )
    with pytest.raises(ValueError):
        download_application_snapshot(
            provider="unknown",
            repo_id="user/app",
            commit="a" * 40,
            target=tmp_path,
            hub=None,
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('provider', ['huggingface', 'gitee'])
@pytest.mark.parametrize('created', [True, False])
def test_providers_publish_source_without_submitting(tmp_path, provider, created):
    write_application(tmp_path)
    hub = FakePublishHub(space_created=created)
    result = publish_application(
        tmp_path, provider=provider, publish_hub=hub,
        credentials=FakeCredentialStore(AccessToken('test-token')),
        identity_hub=FakeIdentityHub(), events=RecordingEvents(),
        watcherobot_version='0.1.1a3',
    )
    assert result.repo_id == SPACE_ID
    assert result.commit == SPACE_COMMIT
    assert result.repository_url == get_provider(provider).repository_url(SPACE_ID)
    assert [name for name, _ in hub.calls] == ['ensure', 'upload', 'head']
    paths = {item.path_in_repo for item in hub.uploaded_files}
    assert {'app.json', 'app.py', 'icon.png'} <= paths
    assert '.env' not in paths


@pytest.mark.parametrize('provider', ['huggingface', 'gitee'])
@pytest.mark.parametrize('state', ['new', 'pending', 'listed', 'update'])
def test_providers_submit_fixed_source_to_separate_catalogs(tmp_path, provider, state):
    write_application(tmp_path)
    config = get_provider(provider)
    existing = CatalogPullRequest(
        number=7, title=catalog_pull_request_title(SPACE_ID, SPACE_COMMIT),
        url=config.repository_url(config.catalog) + '/pulls/7', status='open',
    )
    content = [] if state in ('new', 'pending') else [{
        'repo_id': SPACE_ID,
        'commit': SPACE_COMMIT if state == 'listed' else 'c' * 40,
    }]
    hub = FakePublishHub(
        catalog=CatalogDocument(json.dumps(content).encode(), CATALOG_COMMIT),
        open_pull_requests=(existing,) if state == 'pending' else (),
    )
    result = submit_application(
        tmp_path, provider=provider, commit=SPACE_COMMIT, publish_hub=hub,
        credentials=FakeCredentialStore(AccessToken('test-token')),
        identity_hub=FakeIdentityHub(), events=RecordingEvents(),
        watcherobot_version='0.1.1a3',
    )
    names = [name for name, _ in hub.calls]
    assert not {'ensure', 'upload', 'head'} & set(names)
    assert ('read_catalog', (config.catalog, 'app-list.json')) in hub.calls
    assert result.source_url == config.source_url(SPACE_ID, SPACE_COMMIT)
    assert result.pr_status == ('already_listed' if state == 'listed' else 'pending')
    if state in ('new', 'update'):
        call = next(value for name, value in hub.calls if name == 'create_pr')
        assert call[0] == config.catalog
        assert json.loads(call[2]) == [{'repo_id': SPACE_ID, 'commit': SPACE_COMMIT}]
    else:
        assert 'create_pr' not in names
    if state == 'pending':
        assert result.pr_url == existing.url


from watcherobot.distribution.cli import build_parser
from watcherobot.distribution.catalog_submission import (
    parse_catalog_entries,
    CatalogDocumentError,
)


def test_catalog_accepts_only_generic_repository_identifier():
    entry = parse_catalog_entries(
        b'[{"repo_id":"user/app","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]'
    )[0]
    assert entry.repo_id == "user/app"
    with pytest.raises(CatalogDocumentError):
        parse_catalog_entries(
            b'[{"space_id":"user/app","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]'
        )


@pytest.mark.parametrize(
    "command,extra",
    [
        ("publish", ["."]),
        ("submit", ["."]),
        ("marketplace", []),
        ("download", ["--repo-id", "user/app", "--commit", "a" * 40, "--target", "."]),
        (
            "install",
            [
                "--repo-id",
                "user/app",
                "--commit",
                "a" * 40,
                "--store-root",
                ".",
                "--runtime-root",
                ".",
            ],
        ),
    ],
)
def test_remote_commands_require_provider(command, extra):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["app", command, *extra])
    assert (
        build_parser()
        .parse_args(["app", command, *extra, "--provider", "gitee"])
        .provider
        == "gitee"
    )
