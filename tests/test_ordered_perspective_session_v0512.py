"""Strict session ordering and teardown without native library installation."""
import asyncio
import threading
import time
from types import SimpleNamespace

from timepilot.native_bi_api import _OrderedPerspectiveHandler, _TimedPerspectiveExecutor
from starlette.websockets import WebSocketDisconnect


class FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.events = []

    async def accept(self):
        self.events.append('accept')

    async def receive_bytes(self):
        await asyncio.sleep(.001)
        try:
            return next(self.messages)
        except StopIteration:
            raise WebSocketDisconnect(1001, '')

    async def send_bytes(self, message):
        self.events.append(('send', message))


class FakeServer:
    def __init__(self, events):
        self.events = events

    def new_session(self, callback):
        events = self.events
        class Session:
            closed = False
            busy = False
            def handle_request(self, message):
                assert not self.busy
                assert not self.closed
                self.busy = True
                events.append(('handle', message))
                time.sleep(.003)
                callback(message + b'-ok')
                self.busy = False
            def close(self):
                assert not self.busy, 'session closed while SQL request was in flight'
                events.append('close')
                self.closed = True
        return Session()


def test_session_drains_all_requests_before_closing():
    async def runner():
        events = []
        socket = FakeSocket([b'one', b'two', b'three'])
        executor = _TimedPerspectiveExecutor()
        handler = _OrderedPerspectiveHandler(FakeServer(events), socket, executor,
                                             asyncio.get_running_loop())
        try:
            await handler.run()
        except WebSocketDisconnect as exc:
            assert exc.code == 1001
        assert events == [('handle', b'one'), ('handle', b'two'),
                          ('handle', b'three'), 'close']
    asyncio.run(runner())


def test_close_even_when_first_native_request_raises():
    async def runner():
        events = []
        socket = FakeSocket([b'fail'])
        class FailingServer:
            def new_session(self, callback):
                class Session:
                    def handle_request(self, msg):
                        events.append('failed')
                        raise RuntimeError('SQL failed')
                    def close(self):
                        events.append('close')
                return Session()
        handler = _OrderedPerspectiveHandler(FailingServer(), socket,
                                              _TimedPerspectiveExecutor(), asyncio.get_running_loop())
        try:
            await handler.run()
        except RuntimeError as exc:
            assert 'SQL failed' in str(exc)
        assert events == ['failed', 'close']
    asyncio.run(runner())


def test_frontend_retires_old_view_before_detaching_dom():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / 'src/timepilot/static/native-bi.js').read_text(encoding='utf-8')
    remote = source.split('async function loadVirtual(', 1)[1].split('async function displayArrow(', 1)[0]
    assert remote.index('await module.disposePerspective()') < remote.index('slot.innerHTML=')
    local = source.split('async function displayArrow(', 1)[1].split('async function getArrow(', 1)[0]
    assert local.index('await module.disposePerspective()') < local.index('slot.innerHTML=')
    wrapper = (root / 'frontend/native-bi/src/main.js').read_text(encoding='utf-8')
    for header in ('mountPerspective(container', 'mountPerspectiveRemote(container'):
        block = wrapper.split('export async function ' + header, 1)[1].split('export async function ', 1)[0]
        assert block.index('await disposePerspective()') < block.index('await initialize()')
