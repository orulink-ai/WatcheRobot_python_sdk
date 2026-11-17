"""Transport failures must not permanently lock the editor."""
import pytest
from watcherobot.idle_studio import device


def test_pool_save_requires_explicit_firmware_capability(monkeypatch):
    from watcherobot.idle_studio.model import SaveSession
    commands = []
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): commands.append(command)
        def receive(self, *args): return []
        def close(self): pass
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, 'ports', lambda: [{'port': 'TEST'}])
    monkeypatch.setattr(device, '_SerialProtocol', Protocol)
    monkeypatch.setattr(device, '_parse_ready_device_info', lambda _: {
        'sd_state': 'mounted', 'capabilities': ['joy_idle_v1']})
    monkeypatch.setattr(device, '_receive_work_document', lambda *a: pytest.fail('must reject before transfer'))
    profile = {'schema': 3, 'pool_percentages': {'standby': 100, 'feature': 0},
               'behaviors': [{'id': 'a', 'name': 'a', 'group': 'standby', 'expression': 'standby',
                              'motion': None, 'sound': None, 'weight': 100}]}
    with pytest.raises(ValueError, match='分组概率'):
        reader.save('TEST', SaveSession('test', 0), profile)
    assert not any(command.startswith('IDLE_BEGIN') for command in commands)
    assert commands[-1] == 'CLOSE'


@pytest.mark.parametrize('scenario', ['success', 'busy', 'ack', 'space', 'dedupe'])
def test_import_transaction_boundaries(monkeypatch, scenario):
    from types import SimpleNamespace
    from watcherobot.idle_studio.model import SaveSession
    commands, reads = [], []
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): commands.append(command)
        def close(self): commands.append('closed')
        def receive(self, *args):
            if commands[-1].startswith('DATA '):
                return ['WRSD/2', 'ACK', '999' if scenario == 'ack' else commands[-1].split()[1]]
            return []
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, 'ports', lambda: [{'port': 'TEST'}])
    monkeypatch.setattr(device, '_SerialProtocol', Protocol)
    monkeypatch.setattr(device, '_parse_ready_device_info', lambda _: {
        'sd_state': 'mounted', 'free_bytes': 0 if scenario == 'space' else 10000000,
        'capabilities': ['joy_idle_v1', 'idle_catalog_v1', 'work_write_v2']})
    def document(protocol, command, **kwargs):
        if command == 'IDLE_GET': return {'device_id': 'same', 'revision': 4}
        reads.append(command)
        if scenario == 'busy' and len(reads) == 2:
            raise device.MaintenanceError('commit_in_progress')
        return {'expressions': [{'id': 'we_test'}] if scenario == 'dedupe' or 'COMMIT' in commands else []}
    monkeypatch.setattr(device, '_receive_work_document', document)
    monkeypatch.setattr(device, '_negotiate_chunk_size', lambda _: 4)
    monkeypatch.setattr(device, '_wait_for_sd_activation', lambda *args: ['WRSD/2', 'DONE', 'work'])
    monkeypatch.setattr(device.time, 'sleep', lambda _: None)
    package = SimpleNamespace(payload=b'123456789', expanded_size_bytes=9, sha256='hash', work_id='work')
    if scenario in {'ack', 'space'}:
        with pytest.raises(ValueError):
            reader.import_expression('TEST', SaveSession('same', 4), package, 'we_test', 'test')
        assert 'COMMIT' not in commands
        assert reader.preview_progress('test')['phase'] == 'failed'
    else:
        assert reader.import_expression('TEST', SaveSession('same', 4), package, 'we_test', 'test')['resource'] == 'we_test'
        assert commands.count('COMMIT') == (0 if scenario == 'dedupe' else 1)
        if scenario == 'busy': assert len(reads) == 3
    assert commands[-2:] == ['CLOSE', 'closed']
    assert reader._lock.acquire(blocking=False)
    reader._lock.release()


def test_import_rejects_replaced_device_before_write(monkeypatch):
    from types import SimpleNamespace
    from watcherobot.idle_studio.model import SaveSession
    commands = []
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): commands.append(command)
        def receive(self, *args): return []
        def close(self): pass
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, "ports", lambda: [{"port": "TEST"}])
    monkeypatch.setattr(device, "_SerialProtocol", Protocol)
    monkeypatch.setattr(device, "_parse_ready_device_info", lambda _: {
        "sd_state": "mounted", "capabilities": ["joy_idle_v1", "idle_catalog_v1", "work_write_v2"]})
    monkeypatch.setattr(device, "_receive_work_document", lambda *a, **k: {"device_id": "other", "revision": 0})
    with pytest.raises(ValueError, match="设备已变化"):
        reader.import_expression("TEST", SaveSession("original", 0), SimpleNamespace(), "we_test", "import-1")
    assert not any(c.startswith("WORK_BEGIN") for c in commands)
    assert reader._lock.acquire(blocking=False)
    reader._lock.release()


def test_read_uses_full_catalog_when_supported(monkeypatch):
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): pass
        def receive(self, *args): return []
        def close(self): pass
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, "ports", lambda: [{"port": "TEST"}])
    monkeypatch.setattr(device, "_SerialProtocol", Protocol)
    monkeypatch.setattr(device, "_parse_ready_device_info", lambda _: {
        "sd_state": "mounted", "capabilities": ["joy_idle_v1", "idle_catalog_v1"]})
    def document(protocol, command, **kwargs):
        if command == "IDLE_GET":
            return {"device_id": "test", "revision": 0, "profile": None}
        assert command == "IDLE_RESOURCES"
        return {"expressions": [{"id": "we_custom", "assets": {"animation": True}}]}
    monkeypatch.setattr(device, "_receive_work_document", document)
    assert reader.read("TEST")["catalog"]["expressions"][0]["id"] == "we_custom"


def test_preview_rejects_command_injection_before_opening_port():
    reader = device.DeviceReader()
    with pytest.raises(ValueError):
        reader.preview('TEST', 'standby\nIDLE_COMMIT')


def test_preview_rejects_replaced_device_before_reading_asset(monkeypatch):
    commands = []
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): commands.append(command)
        def receive(self, *args): return []
        def close(self): commands.append('closed')
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, 'ports', lambda: [{'port': 'TEST'}])
    monkeypatch.setattr(device, '_SerialProtocol', Protocol)
    monkeypatch.setattr(device, '_parse_ready_device_info', lambda _: {
        'capabilities': ['resource_read_v1', 'joy_idle_v1'], 'sd_state': 'mounted'})
    def document(protocol, command):
        commands.append(command)
        assert command == 'IDLE_GET'
        return {'device_id': 'replacement', 'revision': 0}
    monkeypatch.setattr(device, '_receive_work_document', document)
    with pytest.raises(ValueError, match='设备已变化'):
        reader.preview('TEST', 'standby', device_id='original')
    assert commands[-2:] == ['CLOSE', 'closed']
    assert reader._lock.acquire(blocking=False)
    reader._lock.release()


def test_preview_reads_bounded_blocks_and_releases_port(monkeypatch):
    commands = []
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): commands.append(command)
        def receive(self, *args): return []
        def close(self): commands.append('closed')
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, 'ports', lambda: [{'port': 'TEST'}])
    monkeypatch.setattr(device, '_SerialProtocol', Protocol)
    monkeypatch.setattr(device, '_parse_ready_device_info', lambda _: {'capabilities': ['resource_read_v1'], 'sd_state': 'mounted'})
    monkeypatch.setattr(device, '_receive_work_document', lambda *a: {'size': 5000})
    def receive(protocol, command, **kwargs):
        commands.append(command)
        assert kwargs['max_bytes'] <= 4096
        state = reader.preview_progress('request1')
        assert state['total'] == 5000
        assert state['received'] == int(command.split()[-2])
        return b'x' * int(command.split()[-1])
    monkeypatch.setattr(device, '_receive_work_payload', receive)
    monkeypatch.setattr(device, 'animation_gif', lambda data: b'GIF' if len(data) == 5000 else b'bad')
    assert reader.preview('TEST', 'standby', request_id='request1') == b'GIF'
    assert reader.preview_progress('request1') == {'phase': 'complete', 'received': 5000, 'total': 5000}
    assert reader.preview_progress('unknown')['phase'] == 'unknown'
    assert 'RESOURCE_READ standby 0 4096' in commands
    assert 'RESOURCE_READ standby 4096 904' in commands
    assert commands[-2:] == ['CLOSE', 'closed']


def test_close_failure_releases_device_lock(monkeypatch):
    class BrokenProtocol:
        def __init__(self, port):
            pass

        def send(self, command):
            raise OSError("USB disconnected")

        def close(self):
            raise OSError("close failed")

    reader = device.DeviceReader()
    monkeypatch.setattr(reader, "ports", lambda: [{"port": "TEST"}])
    monkeypatch.setattr(device, "_SerialProtocol", BrokenProtocol)
    with pytest.raises(OSError):
        reader.read("TEST")
    assert reader._lock.acquire(blocking=False)
    reader._lock.release()


def test_preview_compression_cache_and_same_size_replacement(monkeypatch):
    import zlib
    raw = bytearray(b'a' * 40000)
    reads = []
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): pass
        def receive(self, *args): return []
        def close(self): pass
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, 'ports', lambda: [{'port': 'TEST'}])
    monkeypatch.setattr(device, '_SerialProtocol', Protocol)
    monkeypatch.setattr(device, '_parse_ready_device_info', lambda _: {
        'sd_state': 'mounted', 'capabilities': ['resource_read_v1', 'resource_deflate_v1', 'joy_idle_v1']})
    def document(protocol, command):
        if command == 'IDLE_GET': return {'device_id': 'same'}
        return {'size': len(raw), 'crc32': f'{zlib.crc32(raw):08x}'}
    def receive(protocol, command, **kwargs):
        name, resource, offset, count = command.split()
        assert name == 'RESOURCE_DEFLATE'
        reads.append(command)
        return zlib.compress(raw[int(offset):int(offset) + int(count)])
    monkeypatch.setattr(device, '_receive_work_document', document)
    monkeypatch.setattr(device, '_receive_work_payload', receive)
    monkeypatch.setattr(device, 'animation_gif', lambda data: data)
    assert reader.preview('TEST', 'standby', device_id='same') == bytes(raw)
    assert len(reads) == 2
    assert reader.preview('TEST', 'standby', device_id='same') == bytes(raw)
    assert len(reads) == 2
    raw[0] = 98
    assert reader.preview('TEST', 'standby', device_id='same') == bytes(raw)
    assert len(reads) == 4


def test_save_rejects_changed_device_before_upload(monkeypatch):
    from watcherobot.idle_studio.model import SaveSession
    commands = []
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): commands.append(command)
        def receive(self, *args): return []
        def close(self): pass
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, "ports", lambda: [{"port": "TEST"}])
    monkeypatch.setattr(device, "_SerialProtocol", Protocol)
    monkeypatch.setattr(device, "_parse_ready_device_info", lambda _: {"capabilities": ["joy_idle_v1"], "sd_state": "mounted"})
    monkeypatch.setattr(device, "_receive_work_document", lambda *args, **kwargs: {"device_id": "other", "revision": 1})
    profile = {"schema": 1, "behaviors": [{"id": "a", "name": "a", "expression": "standby", "motion": None, "sound": None, "weight": 1}]}
    with pytest.raises(ValueError, match="设备已变化"):
        reader.save("TEST", SaveSession("original", 1), profile)
    assert not any(c.startswith("IDLE_BEGIN") for c in commands)


@pytest.mark.parametrize('failure', [None, 'revision', 'readback', 'ack'])
def test_save_chunks_and_requires_verified_readback(monkeypatch, failure):
    import base64
    import json
    import zlib
    from watcherobot.idle_studio.model import SaveSession

    commands, chunks = [], []
    profile = {'schema': 1, 'behaviors': [
        {'id': f'b{i}', 'name': '待机行为', 'expression': 'standby',
         'motion': None, 'sound': None, 'weight': i + 1} for i in range(12)
    ]}
    class Protocol:
        def __init__(self, port): pass
        def send(self, command):
            commands.append(command)
            if command.startswith('IDLE_DATA '):
                _, sequence, encoded = command.split()
                assert int(sequence) == len(chunks)
                chunks.append(base64.b64decode(encoded, validate=True))
        def receive(self, *args):
            parts = commands[-1].split()
            if failure == 'ack' and parts[0] == 'IDLE_DATA':
                return ['WRSD/2', 'OK', 'IDLE_DATA', '999']
            return ['WRSD/2', 'OK'] + parts[:2 if parts[0] == 'IDLE_DATA' else 1]
        def close(self): pass

    def document(*args):
        if 'IDLE_COMMIT' not in commands:
            return {'device_id': 'same', 'revision': 4}
        return {'device_id': 'same', 'revision': 4 if failure == 'revision' else 5,
                'profile': {} if failure == 'readback' else profile}

    reader = device.DeviceReader()
    monkeypatch.setattr(reader, 'ports', lambda: [{'port': 'TEST'}])
    monkeypatch.setattr(device, '_SerialProtocol', Protocol)
    monkeypatch.setattr(device, '_parse_ready_device_info', lambda _: {'capabilities': ['joy_idle_v1'], 'sd_state': 'mounted'})
    monkeypatch.setattr(device, '_receive_work_document', document)
    if failure:
        with pytest.raises(ValueError):
            reader.save('TEST', SaveSession('same', 4), profile)
    else:
        assert reader.save('TEST', SaveSession('same', 4), profile)['revision'] == 5
        payload = b''.join(chunks)
        assert json.loads(payload) == profile
        begin = next(c for c in commands if c.startswith('IDLE_BEGIN')).split()
        assert int(begin[3]) == len(payload)
        assert int(begin[4], 16) == zlib.crc32(payload)
        assert len(chunks) > 1 and all(len(c) <= 512 for c in chunks)
    assert commands[-1] == 'CLOSE'
    assert reader._lock.acquire(blocking=False)
    reader._lock.release()


def test_read_timeout_explains_firmware_mode_and_releases_port(monkeypatch):
    commands = []
    class Protocol:
        def __init__(self, port): pass
        def send(self, command): commands.append(command)
        def receive(self, *args):
            raise device.MaintenanceError('等待设备响应超时，请确认已先安装支持 SD 串口维护的固件。')
        def close(self): commands.append('closed')
    reader = device.DeviceReader()
    monkeypatch.setattr(reader, 'ports', lambda: [{'port': 'TEST'}])
    monkeypatch.setattr(device, '_SerialProtocol', Protocol)
    with pytest.raises(device.MaintenanceError, match='AI Chat'):
        reader.read('TEST')
    assert commands[-2:] == ['CLOSE', 'closed']
    assert reader._lock.acquire(blocking=False)
    reader._lock.release()
