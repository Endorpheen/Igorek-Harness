"""Remote-control bridge for the Android client.

The bridge is an additional process that reuses the existing factory state
(task queue, chat sessions, trace journal, approvals) instead of running a
second agent. It never starts its own executor: tasks are submitted to the
same queue the local worker consumes.
"""
