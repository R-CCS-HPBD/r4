"""Hermeticity guard for the companion suite.

``CompanionSession.__init__`` wires the process-global ``tool_search``
backend (G18, 2026-10-28). Left unrestored, that callback leaks the previous
test's session into every later test in the process (the availability gate
then keeps ``tool_search`` offered where older pins expect the dead stub to
be filtered). Reset the callback to its pre-suite value after every test.
"""

from __future__ import annotations

import pytest

import r4.agent.tools as agent_tools
import r4.companion.user_hooks as user_hooks

# Captured at collection time, before any test in this package can wire it.
_PRE_SUITE_TOOL_SEARCH = agent_tools._tool_search_fn
_PRE_SUITE_USER_HOOKS = user_hooks._current_user_hooks


@pytest.fixture(autouse=True)
def _restore_meta_tool_callbacks():
    yield
    agent_tools._tool_search_fn = _PRE_SUITE_TOOL_SEARCH
    user_hooks._current_user_hooks = _PRE_SUITE_USER_HOOKS
