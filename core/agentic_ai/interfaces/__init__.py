"""Abstract contracts shared across core subsystems.

Intended for small, explicit interface definitions (e.g. ``typing.Protocol``
or ABCs) that several subsystems depend on, so concrete implementations can
be swapped without circular imports. Exact scope is an open question
(see dev_mem/status_update.md, Known Issues).
"""
