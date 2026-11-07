"""video_feed — generalized video ingest → workflow → results plane.

Single shared client path used by app profiles (fixed cameras, field
cameras, ...; real profiles load from ``$VIDEO_FEED_PROFILE_DIR``). Slice:

- :mod:`video_feed.client`   — r4 Gateway HTTPS client (upload + trigger)
- :mod:`video_feed.profiles` — application profile dataclasses
- :mod:`video_feed.ingest`   — scan/dedupe/upload/trigger driver
- :mod:`video_feed.results`  — worker-side result rows + kg wiring (platform)
- :mod:`video_feed.manifest` — client-side idempotency ledger
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
