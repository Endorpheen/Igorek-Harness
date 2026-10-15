"""Bounded TLS handshakes outside a threaded HTTP server's accept loop."""
import socket
import threading


class TLSHandshakeMixin:
    tls_handshake_timeout = 5.0
    max_tls_handshakes = 32

    def __init__(self, *args, **kwargs):
        self._tls_context = None
        self._tls_slots = threading.BoundedSemaphore(self.max_tls_handshakes)
        self._tls_pending = set()
        self._tls_lock = threading.Lock()
        self._tls_closing = False
        super().__init__(*args, **kwargs)

    def enable_tls(self, context):
        # Keep the listening socket plain; each worker wraps its accepted peer.
        self._tls_context = context

    def process_request(self, request, client_address):
        if self._tls_context is not None:
            if not self._tls_slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self._tls_slots.release()
                raise
        else:
            super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        if self._tls_context is not None:
            try:
                request.settimeout(self.tls_handshake_timeout)
                with self._tls_lock:
                    if self._tls_closing:
                        self.shutdown_request(request)
                        return
                    request = self._tls_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
                    self._tls_pending.add(request)
                request.do_handshake()
                # HTTP keep-alive, uploads, SSE and WebSockets retain their own timeouts.
                request.settimeout(None)
            except OSError:
                self.shutdown_request(request)
                return
            finally:
                with self._tls_lock:
                    self._tls_pending.discard(request)
                self._tls_slots.release()
        super().process_request_thread(request, client_address)

    def server_close(self):
        with self._tls_lock:
            self._tls_closing = True
            pending = list(self._tls_pending)
        for peer in pending:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            peer.close()
        super().server_close()
