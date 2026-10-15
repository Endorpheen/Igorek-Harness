"""Guest channel queues apply finite backpressure and close without deadlock."""
import threading
import socket
import time

import pytest

from factory.core.isolation.channel import ChannelMessage, MessageKind, _BoundedInbox


@pytest.mark.parametrize('bytes_limit,frame_limit,payload', [(512, 10, 'x' * 200), (4096, 2, 'x')])
def test_queue_bounds_block_producer_until_drain_and_close(bytes_limit, frame_limit, payload):
    inbox = _BoundedInbox(bytes_limit, frame_limit)
    message = ChannelMessage(MessageKind.HEARTBEAT, 1, {'text': payload})
    assert inbox.put(message)
    assert inbox.put(message)
    entered = threading.Event()
    completed = threading.Event()
    result = []

    def produce():
        entered.set()
        result.append(inbox.put(message))
        completed.set()

    thread = threading.Thread(target=produce, daemon=True)
    thread.start()
    assert entered.wait(1)
    assert not completed.wait(.05)
    assert inbox.qsize() == 2
    assert inbox.get(timeout=1) == message
    assert completed.wait(1)
    assert result == [True]
    entered.clear()
    completed.clear()
    thread = threading.Thread(target=produce, daemon=True)
    thread.start()
    assert entered.wait(1)
    assert not completed.wait(.05)
    inbox.close()
    assert completed.wait(1)
    assert result == [True, False]
    assert inbox.get(timeout=1) == message
    assert inbox.get(timeout=1) == message
    assert inbox.get(timeout=1) is None


def test_reconnection_closes_old_reader_without_eviction_of_the_new_peer():
    from factory.core.isolation.channel import ChannelConfig, ChannelServer, SessionMap, PeerCid, SessionId, guest_connect
    config = ChannelConfig()
    sessions = SessionMap()
    cid, identity = PeerCid(11), SessionId('fixture-session')
    sessions.assign(cid, identity)
    server = ChannelServer(config, sessions)
    peers = []

    def connect():
        left, right = socket.socketpair()
        guest = []
        thread = threading.Thread(target=lambda: guest.append(guest_connect(right, config)), daemon=True)
        thread.start()
        host = server.handle_connection(left, cid)
        thread.join(timeout=1)
        assert host is not None and guest
        peers.append(guest[0])
        return host, guest[0]

    try:
        first, _ = connect()
        second, guest = connect()
        assert first.closed
        time.sleep(.05)
        assert server.session_for(identity) is second
        guest.send(MessageKind.HEARTBEAT, {'state': 'ready'})
        assert second.recv_from_guest(timeout_s=1).kind is MessageKind.HEARTBEAT
    finally:
        for peer in peers:
            peer.close()
        server.close()
