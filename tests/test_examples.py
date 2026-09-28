import json
from pathlib import Path
import subprocess

from watcherobot.distribution.source_files import collect_application_source_files


ROOT = Path(__file__).parents[1]
EXAMPLE_IDS = {
    "hello_robot": "example.hello_robot",
    "quickstart": "example.quickstart",
    "play_audio_file": "example.play_audio_file",
    "capture_photo": "example.capture_photo",
    "record_microphone": "example.record_microphone",
    "sdk_media_lab": "example.sdk_media_lab",
    "vision_debug_lab": "com.orulink.vision_debug_lab",
}


def test_every_example_is_a_complete_managed_application() -> None:
    for directory_name, app_id in EXAMPLE_IDS.items():
        root = ROOT / "examples" / directory_name
        manifest = json.loads(root.joinpath("app.json").read_text())
        source = root.joinpath("app.py").read_text(encoding="utf-8")

        assert manifest["id"] == app_id
        assert manifest["requires_watcherobot"]
        assert "ApplicationContext.from_environment()" in source
        assert "WatcheRobot.connect" not in source
        assert "WATCHEROBOT_PAIRING_CODE" not in source


def test_vision_debug_lab_declares_its_reviewed_marketplace_platform() -> None:
    manifest = json.loads(
        (ROOT / "examples" / "vision_debug_lab" / "app.json").read_text()
    )

    assert manifest["schema_version"] == 2
    assert manifest["supported_host_platforms"] == ["windows"]


def test_quickstart_demonstrates_domain_apis_through_context_robot() -> None:
    source = (
        ROOT / "examples" / "quickstart" / "app.py"
    ).read_text(encoding="utf-8")

    assert "app.robot.behavior.play" in source
    assert "app.robot.lights.set_color" in source
    assert "app.robot.motion.move_to" in source


def test_microphone_example_records_decoded_pcm() -> None:
    source = (
        ROOT / "examples" / "record_microphone" / "app.py"
    ).read_text(encoding="utf-8")

    assert "app.robot.microphone.record_pcm" in source
    assert "app.robot.microphone.record," not in source


def test_media_lab_is_a_local_managed_web_application() -> None:
    root = ROOT / "examples" / "sdk_media_lab"
    entrypoint = root.joinpath("app.py").read_text(encoding="utf-8")
    page = root.joinpath("web", "index.html").read_text(encoding="utf-8")
    script = root.joinpath("web", "app.js").read_text(encoding="utf-8")
    styles = root.joinpath("web", "styles.css").read_text(encoding="utf-8")

    assert "127.0.0.1" in entrypoint
    assert "ApplicationContext.from_environment()" in entrypoint
    assert "app.robot" in entrypoint
    assert 'lang="en"' in page
    assert "SDK Test Bench" in page
    assert "api/status" in script
    assert "api/actions/play-audio" in script
    assert "api/actions/capture-photo" in script
    assert "api/actions/record-microphone" in script
    assert "ws://" not in script
    assert "wss://" not in script
    assert "drawEmptyWaveform();" in script
    assert 'drawWaveform("/artifacts/microphone.wav")' not in script
    assert "[hidden]" in styles
    assert "display: none !important" in styles

    examples_guide = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
    hardware_guide = (ROOT / "docs" / "hardware-testing.md").read_text(encoding="utf-8")
    assert "sdk_media_lab" in examples_guide
    assert "sdk_media_lab" in hardware_guide
    assert "127.0.0.1" in hardware_guide


def test_vision_debug_lab_is_loopback_only_and_uses_managed_vision_apis() -> None:
    root = ROOT / "examples" / "vision_debug_lab"
    entrypoint = root.joinpath("app.py").read_text(encoding="utf-8")
    service = root.joinpath("service.py").read_text(encoding="utf-8")
    page = root.joinpath("web", "index.html").read_text(encoding="utf-8")
    script = root.joinpath("web", "app.js").read_text(encoding="utf-8")
    packet_module = root.joinpath("web", "preview-packet.mjs").read_text(
        encoding="utf-8"
    )

    assert 'HOST = "127.0.0.1"' in entrypoint
    assert "ApplicationContext.from_environment()" in entrypoint
    assert "app.robot" in entrypoint
    assert "robot.vision.status" in service
    assert "robot.face_tracking.open_preview" in service
    assert "192.168." not in service
    assert "Vision Debug Lab" in page
    assert "/ws/preview" in packet_module
    assert "new WebSocket" in script
    assert "drawImage" in script
    assert "strokeRect" in script
    assert "192.168." not in script
    assert "192.168." not in packet_module


def test_media_lab_is_ready_for_marketplace_distribution() -> None:
    root = ROOT / "examples" / "sdk_media_lab"
    manifest = json.loads(root.joinpath("app.json").read_text(encoding="utf-8"))
    entrypoint = root.joinpath("app.py").read_text(encoding="utf-8")

    assert manifest["name"] == "SDK Test Bench"
    assert manifest["version"] == "1.1.0"
    assert manifest["author"] == "Orulink AI"
    assert manifest["description"].startswith("Validate WatcheRobot")
    assert manifest["icon"] == "icon.svg"
    assert root.joinpath(manifest["icon"]).is_file()
    assert root.joinpath("assets", "sample_speech.wav").is_file()
    assert 'ROOT / "assets" / "sample_speech.wav"' in entrypoint
    assert 'ROOT.parent / "assets"' not in entrypoint

    published_files = {
        path.as_posix() for path in collect_application_source_files(root)
    }
    assert "assets/sample_speech.wav" in published_files
    assert "web/i18n.mjs" in published_files
    assert "icon.svg" in published_files
    assert not any(path.startswith("artifacts/") for path in published_files)
    assert not any("__pycache__" in path for path in published_files)


def test_media_lab_defaults_to_english_and_can_switch_to_chinese() -> None:
    web_root = ROOT / "examples" / "sdk_media_lab" / "web"
    page = web_root.joinpath("index.html").read_text(encoding="utf-8")
    script = web_root.joinpath("app.js").read_text(encoding="utf-8")
    i18n = web_root.joinpath("i18n.mjs").read_text(encoding="utf-8")

    assert '<html lang="en" data-i18n-ready="false">' in page
    assert 'id="localeEnglish"' in page
    assert 'id="localeChinese"' in page
    assert 'aria-label="Language"' in page
    assert 'import { initializeI18n, translateText } from "./i18n.mjs";' in script
    assert 'const i18n = initializeI18n({' in script
    assert 'defaultLocale: "en-US"' in script
    assert 'storageKey: "watcherobot.sdk-test-bench.locale"' in script
    assert '"en-US"' in i18n
    assert '"zh-CN"' in i18n
    assert "MutationObserver" in i18n


def test_runtime_artifacts_are_ignored(tmp_path: Path) -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/artifacts/" in ignore
    assert "/examples/*/artifacts/" in ignore
    assert "/.vscode/" in ignore

    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".gitignore").write_text(ignore, encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=repository,
        check=True,
    )
    result = subprocess.run(
        ["git", "check-ignore", "examples/capture_photo/artifacts/camera.jpg"],
        cwd=repository,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
