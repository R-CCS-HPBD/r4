"""Science domains — pluggable scientific domain implementations.

Each domain implements the :class:`~science.domain.ScienceDomain` Protocol,
providing the methods needed to run the autonomous science loop for that
specific field of inquiry.

Available domains:
    (none bundled — implement your own by following the Protocol)

To add a new domain:
    1. Create a new file in this package (e.g. ``my_domain.py``)
    2. Implement the :class:`~science.domain.ScienceDomain` Protocol
    3. Export the class from this ``__init__.py``
"""

__all__: list[str] = []
