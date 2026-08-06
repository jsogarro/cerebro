"""Packet 4A's non-guarantee 2: pattern redaction is a net, not a barrier.

4A: only the ``SecretRef`` barrier is structural, and nothing forces a tool
author to use it — layer 1 "holds only where a tool adapter actually uses it,
which the contract layer cannot force". Its proposed fix was typed tool I/O
making credential parameters ``SecretRef``-typed so a literal cannot
type-check.

Registration is where that becomes forceable. A tool whose input schema
declares a credential-named field as anything a literal could inhabit is
refused *when it is registered* — an authoring-time error, found once, rather
than a call-time hope that a pattern catches the value.

The credential-name test is redaction's own, imported rather than restated: two
copies of a security word list drift, and the one that drifts is the one nobody
is reading.
"""

from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from src.core.contracts.capabilities import SensitivityClass
from src.core.contracts.redaction import REDACTION_MARKER, SecretRef
from src.core.tools import (
    MappingSecretProvider,
    ToolBoundary,
    ToolCallContext,
    ToolSpec,
    ToolSpecError,
    UnknownSecretError,
)

from .conftest import EchoOutput, invoke_kwargs

HELD = "held-secret-value-abcdef"


def spec_for(
    model: type[BaseModel], *, name: str = "t", handler: Any = None
) -> ToolSpec:
    async def noop(args: Any, context: ToolCallContext) -> Mapping[str, Any]:
        return {"echoed": "ok"}

    return ToolSpec(
        name=name,
        version="1.0.0",
        sensitivity=SensitivityClass.READ_ONLY,
        input_model=model,
        output_model=EchoOutput,
        timeout_seconds=1.0,
        handler=handler or noop,
    )


class TestALiteralCredentialCannotBeDeclared:
    def test_a_string_api_key_is_refused_at_registration(self) -> None:
        class Bad(BaseModel):
            api_key: str

        with pytest.raises(ToolSpecError, match="must be typed"):
            spec_for(Bad)

    @pytest.mark.parametrize(
        "field_name",
        ["password", "token", "authorization", "x_api_key", "client_secret", "cookie"],
    )
    def test_the_rule_covers_redactions_whole_credential_vocabulary(
        self, field_name: str
    ) -> None:
        model = type("Bad", (BaseModel,), {"__annotations__": {field_name: str}})

        with pytest.raises(ToolSpecError):
            spec_for(model)

    def test_a_credential_nested_one_level_down_is_still_a_credential(self) -> None:
        class Inner(BaseModel):
            api_key: str

        class Outer(BaseModel):
            inner: Inner

        with pytest.raises(ToolSpecError, match=r"inner\.api_key"):
            spec_for(Outer)

    def test_a_union_that_admits_a_string_is_refused(self) -> None:
        """A union a string can inhabit is a union a string arrives through."""

        class Bad(BaseModel):
            api_key: str | SecretRef

        with pytest.raises(ToolSpecError):
            spec_for(Bad)

    def test_a_secret_ref_is_accepted(self) -> None:
        class Good(BaseModel):
            api_key: SecretRef

        assert spec_for(Good).name == "t"

    def test_an_optional_secret_ref_is_accepted(self) -> None:
        class Good(BaseModel):
            api_key: SecretRef | None = None

        assert spec_for(Good).name == "t"

    def test_a_non_credential_string_is_untouched(self) -> None:
        class Fine(BaseModel):
            query: str

        assert spec_for(Fine).name == "t"


class TestTheHandleResolvesOnlyInsideTheAdapter:
    async def test_the_value_never_enters_the_persisted_record(
        self, boundary_dependencies: dict[str, Any], audit_store: Any
    ) -> None:
        """The handle is what is hashed and stored; the value is not."""

        seen: list[str] = []

        class WithCredential(BaseModel):
            query: str
            api_key: SecretRef

        async def handler(
            args: WithCredential, context: ToolCallContext
        ) -> Mapping[str, Any]:
            seen.append(context.resolve_secret(args.api_key))
            return {"echoed": args.query}

        boundary_dependencies["secret_provider"] = MappingSecretProvider(
            {"prod-key": HELD}
        )
        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(spec_for(WithCredential, name="creds", handler=handler))

        outcome = await boundary.invoke(
            **invoke_kwargs(
                tool_name="creds",
                arguments={"query": "hi", "api_key": {"secret_id": "prod-key"}},
            )
        )

        assert outcome.succeeded
        assert seen == [HELD]
        stored = str([i.model_dump(mode="json") for i in audit_store.invocations])
        assert HELD not in stored
        assert "prod-key" in stored

    async def test_an_unknown_handle_fails_rather_than_resolving_to_nothing(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        class WithCredential(BaseModel):
            query: str
            api_key: SecretRef

        async def handler(
            args: WithCredential, context: ToolCallContext
        ) -> Mapping[str, Any]:
            context.resolve_secret(args.api_key)
            return {"echoed": args.query}

        boundary_dependencies["secret_provider"] = MappingSecretProvider(
            {"prod-key": HELD}
        )
        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(spec_for(WithCredential, name="creds", handler=handler))

        outcome = await boundary.invoke(
            **invoke_kwargs(
                tool_name="creds",
                arguments={"query": "hi", "api_key": {"secret_id": "absent"}},
            )
        )

        assert not outcome.succeeded
        assert outcome.detail is not None
        assert UnknownSecretError.__name__ in outcome.detail


class TestTheNetStillCatchesWhatComesFromOutside:
    async def test_a_held_secret_echoed_back_by_a_tool_is_scrubbed(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        """Layer 2 covers what layer 1 cannot: a value arriving in a response.

        4A's exact-value layer is only live because the provider is a required
        dependency. Losing it here is the failure mode this whole arrangement
        exists to prevent, so it is asserted rather than assumed.
        """

        class Out(BaseModel):
            echoed: str

        async def leaky(args: Any, context: ToolCallContext) -> Mapping[str, Any]:
            return {"echoed": f"the upstream page said {HELD}"}

        boundary_dependencies["secret_provider"] = MappingSecretProvider(
            {"prod-key": HELD}
        )
        boundary = ToolBoundary(**boundary_dependencies)

        class In(BaseModel):
            query: str

        boundary.register(
            ToolSpec(
                name="leaky",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=In,
                output_model=Out,
                timeout_seconds=1.0,
                handler=leaky,
            )
        )

        outcome = await boundary.invoke(**invoke_kwargs(tool_name="leaky"))

        assert outcome.succeeded
        assert HELD not in str(outcome.unwrap())
        assert REDACTION_MARKER in str(outcome.unwrap())


class TestAnInputModelCannotAdmitFieldsItDoesNotDeclare:
    """The credential rule above is enforced over ``model_fields``.

    A model configured ``extra="allow"`` accepts a field that is in no
    ``model_fields`` entry, so a credential arrives by *not being declared* —
    and the whole check above walks straight past it. The boundary then
    serializes the validated model by iterating ``model_fields`` too, so the
    field is absent from the record, from ``input_sha256``, from the approval
    fingerprint that ``input_sha256`` binds, and from the derived idempotency
    key, while still being readable from ``model_extra`` by a handler.

    ``extra="ignore"`` — what a model gets by omission — is still permitted.
    Under ``ignore`` the field cannot enter the validated object at all, so
    nothing a handler can reach is missing from the record. What it does not
    cover is the caller's *submission*, which is the non-guarantee this wave
    already wrote down. ``allow`` is the strictly worse case and is what this
    refuses.
    """

    def test_a_permissive_input_model_is_refused_at_registration(self) -> None:
        class Open(BaseModel):
            model_config = ConfigDict(extra="allow")

            query: str

        with pytest.raises(ToolSpecError, match="undeclared"):
            spec_for(Open)

    def test_the_credential_rule_can_no_longer_be_evaded_by_omission(self) -> None:
        """The same model, named for what it buys an author.

        ``Open`` declares no credential field, so ``_audit_credential_fields``
        finds nothing to object to — and ``{"api_key": "sk-live-..."}``
        validates against it, reaching a handler through ``model_extra``.
        """

        class Open(BaseModel):
            model_config = ConfigDict(extra="allow")

            query: str

        assert Open.model_validate({"query": "q", "api_key": "sk-live-x"}).model_extra

        with pytest.raises(ToolSpecError):
            spec_for(Open)

    def test_a_permissive_model_nested_one_level_down_is_refused_too(self) -> None:
        class Open(BaseModel):
            model_config = ConfigDict(extra="allow")

            value: str

        class Outer(BaseModel):
            inner: Open

        with pytest.raises(ToolSpecError, match=r"inner"):
            spec_for(Outer)

    def test_a_closed_model_is_accepted(self) -> None:
        class Closed(BaseModel):
            model_config = ConfigDict(extra="forbid")

            query: str

        assert spec_for(Closed).name == "t"

    def test_the_default_configuration_is_still_accepted(self) -> None:
        """``ignore`` drops the field before it can reach anything."""

        class Default(BaseModel):
            query: str

        assert spec_for(Default).name == "t"


class TestOtherOmissionsAreRefusedToo:
    def test_a_tool_without_a_deadline_cannot_be_registered(self) -> None:
        class Fine(BaseModel):
            query: str

        async def noop(args: Any, context: ToolCallContext) -> Mapping[str, Any]:
            return {"echoed": "ok"}

        with pytest.raises(ToolSpecError, match="deadline"):
            ToolSpec(
                name="t",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=Fine,
                output_model=EchoOutput,
                timeout_seconds=0,
                handler=noop,
            )

    def test_registering_a_second_version_under_one_name_is_refused(
        self, boundary: ToolBoundary, echo_spec: ToolSpec
    ) -> None:
        from dataclasses import replace

        with pytest.raises(ToolSpecError, match="already registered"):
            boundary.register(replace(echo_spec, version="2.0.0"))
