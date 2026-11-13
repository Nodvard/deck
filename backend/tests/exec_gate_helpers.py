"""Führt SSH-Testaufrufe durch den echten Vorschlags- und Freigabeablauf."""

from uuid import uuid4

import pytest
from nodvard_deck.core import gate
from nodvard_deck.ext.context import ExecHandle, _PermissionChecker
from nodvard_sdk import ActionRequest, ActionResult, Actor, GateOutcome


class ReadingExec(ExecHandle):
    def __init__(self, *, ext_id, run=None):
        super().__init__(_PermissionChecker(ext_id, ["hosts.read", "hosts.execute"]), settings=None)
        if run is not None:
            self.run = run


async def execute_approved(session, callback, *, host_id=None):
    results = []
    errors = []

    class Executor:
        async def execute(self, request):
            try:
                results.append(await callback())
            except Exception as exc:
                errors.append(exc)
                raise
            return ActionResult(success=True)

    async def find_executor(action_type):
        return Executor()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gate, "find_executor", find_executor)
        decision = await gate.propose(
            session, ext_id="test-ext", command_field=None,
            request=ActionRequest(
                action_type=f"test.exec.{uuid4().hex}", host_ref=host_id,
                proposed_by=Actor.extension("test-ext"), reason="SSH-Zugriff prüfen",
            ),
        )
        assert decision.outcome == GateOutcome.REQUIRE_CONFIRMATION
        action, moved = await gate.approve(session, decision.action_id, user_id="test-user")
        assert moved
        if errors:
            assert action.status == "failed"
            raise errors[0]
        assert action.status == "succeeded"
    return results[0]
