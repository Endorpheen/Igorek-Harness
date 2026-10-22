## Verified platform test matrix

The ordinary isolated Python suite, native Windows job and opt-in KVM host/guest checks run in their matching environments. Windows-only and live-VM skips in a Linux unit job remain visible; matching native receipts establish their outcomes. The local acceptance matrix covers all fifteen guarded VM/vsock checks, including the original guest functions and a full approval/write/cancel/stop flow.

Controlled local inference uses the allowed QEMU host alias in the guest request URL. It preserves the configured port and path, and rejects a route mismatch. Cloud and credential-proxy routing are unchanged. Live Ubuntu 26.04 acceptance retained CPU/RAM caps with system libvirt and a session disk large enough for the image; the session backend on that host rejected enforced memory tuning. Test configuration never changes the owner's active image or model server.
