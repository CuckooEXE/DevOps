"""Target classes contributed by installed devops plugins.

Plugins are discovered at ``builder`` import time via the
``devops.targets`` entry-point group. Each registered class is bound
as an attribute of this module, so projects write::

    from builder.plugins import TarballArtifact, TestRangeTest

to make it obvious at every call site that the target isn't part of
the core feature set — it's coming from an installed plugin.

``__all__`` is populated dynamically; an IDE that can't see a plugin
class is a signal that the plugin package isn't installed in the
active environment.
"""

from __future__ import annotations


__all__: list[str] = []
