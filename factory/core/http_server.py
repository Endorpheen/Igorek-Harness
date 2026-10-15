"""Bound local HTTP concurrency and unauthenticated header read time."""
from http.server import ThreadingHTTPServer
import socket
import threading


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    max_requests = 32
    header_timeout = 10.0
    client_read_timeout = 30.0

    def __init__(self, *args, **kwargs):
        self._request_slots = threading.BoundedSemaphore(self.max_requests)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(self.client_read_timeout)
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class HeaderDeadlineMixin:
    """Expire a whole header read, including clients that send one byte at a time."""

    def handle_one_request(self):
        self._headers_done = threading.Event()
        done = self._headers_done

        def expire():
            if not done.is_set():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        self._header_timer = threading.Timer(getattr(self.server, 'header_timeout', 10.0), expire)
        self._header_timer.daemon = True
        self._header_timer.start()
        try:
            super().handle_one_request()
        finally:
            done.set()
            self._header_timer.cancel()

    def parse_request(self):
        try:
            return super().parse_request()
        finally:
            self._headers_done.set()
            self._header_timer.cancel()
