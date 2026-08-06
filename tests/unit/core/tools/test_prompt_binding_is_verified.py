"""Packet 4A's non-guarantee 1: a prompt digest's truthfulness.

4A: ``PromptBinding.for_rendered()`` computes honest digests, but nothing stops
a caller passing ``PromptBinding(...)`` arbitrary hex; the contract enforces
presence and shape, not correspondence to text actually sent.

These tests pin the seam that closes it. The property asserted is
correspondence, not provenance: a binding is refused because its digest does
not hash the material beside it, which is a stronger statement than "we do not
remember minting this" and one that survives a restart.
"""

import hashlib
from typing import Any

import pytest

from src.core.contracts.provenance import ProducerKind, PromptBinding
from src.core.tools import (
    MappingSecretProvider,
    PromptBindingRefusedError,
    PromptRegistry,
    PromptRenderer,
    PromptTemplate,
    RenderedPrompt,
    ToolBoundary,
    require_rendered_prompt,
)

from .conftest import invoke_kwargs

TEMPLATE = "Summarize $topic for a reviewer."
FAKE_DIGEST = "f" * 64


@pytest.fixture
def renderer() -> PromptRenderer:
    registry = PromptRegistry()
    registry.register(
        PromptTemplate(prompt_id="summarize", version="1.0.0", source=TEMPLATE)
    )
    return PromptRenderer(registry=registry, secret_provider=MappingSecretProvider({}))


class TestTheRendererIsTheMintSite:
    def test_it_returns_text_and_binding_as_one_value(
        self, renderer: PromptRenderer
    ) -> None:
        rendered = renderer.render(
            prompt_id="summarize", variables={"topic": "inflation"}
        )

        assert rendered.text == "Summarize inflation for a reviewer."
        assert rendered.binding.matches_rendered(rendered.text)
        assert rendered.binding.matches_template(TEMPLATE)

    def test_the_digest_tracks_an_edit_the_version_string_cannot(
        self, renderer: PromptRenderer
    ) -> None:
        """The reason 4A pinned identity by digest rather than by version."""

        first = renderer.render(prompt_id="summarize", variables={"topic": "a"})
        second = renderer.render(prompt_id="summarize", variables={"topic": "b"})

        assert first.binding.prompt_version == second.binding.prompt_version
        assert first.binding.rendered_sha256 != second.binding.rendered_sha256

    def test_a_missing_variable_fails_rather_than_pinning_a_placeholder(
        self, renderer: PromptRenderer
    ) -> None:
        with pytest.raises(KeyError):
            renderer.render(prompt_id="summarize", variables={})

    def test_re_registering_a_different_body_under_one_id_is_refused(self) -> None:
        registry = PromptRegistry()
        registry.register(PromptTemplate(prompt_id="p", version="1.0.0", source="one"))

        with pytest.raises(ValueError, match="already registered"):
            registry.register(
                PromptTemplate(prompt_id="p", version="1.0.0", source="two")
            )


class TestAForgedBindingCannotExist:
    def test_arbitrary_hex_for_the_rendered_digest_is_refused(self) -> None:
        binding = PromptBinding(
            prompt_id="summarize",
            prompt_version="1.0.0",
            template_sha256=hashlib.sha256(TEMPLATE.encode()).hexdigest(),
            rendered_sha256=FAKE_DIGEST,
        )

        with pytest.raises(PromptBindingRefusedError, match="rendered_sha256"):
            RenderedPrompt(
                template_source=TEMPLATE, text="whatever was sent", binding=binding
            )

    def test_arbitrary_hex_for_the_template_digest_is_refused(self) -> None:
        text = "Summarize inflation for a reviewer."
        binding = PromptBinding(
            prompt_id="summarize",
            prompt_version="1.0.0",
            template_sha256=FAKE_DIGEST,
            rendered_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )

        with pytest.raises(PromptBindingRefusedError, match="template_sha256"):
            RenderedPrompt(template_source=TEMPLATE, text=text, binding=binding)

    def test_a_binding_from_one_render_cannot_be_paired_with_another_text(
        self, renderer: PromptRenderer
    ) -> None:
        """Swapping halves of two honest renders is still a lie."""

        first = renderer.render(prompt_id="summarize", variables={"topic": "a"})
        second = renderer.render(prompt_id="summarize", variables={"topic": "b"})

        with pytest.raises(PromptBindingRefusedError):
            RenderedPrompt(
                template_source=TEMPLATE, text=second.text, binding=first.binding
            )


class TestTheBoundaryRefusesWhatItCannotVerify:
    def test_a_bare_prompt_binding_is_rejected_by_name(self) -> None:
        binding = PromptBinding(
            prompt_id="summarize",
            prompt_version="1.0.0",
            template_sha256=FAKE_DIGEST,
            rendered_sha256=FAKE_DIGEST,
        )

        with pytest.raises(PromptBindingRefusedError, match="bare PromptBinding"):
            require_rendered_prompt(binding)

    def test_anything_else_is_rejected_too(self) -> None:
        with pytest.raises(
            PromptBindingRefusedError, match="expected a RenderedPrompt"
        ):
            require_rendered_prompt({"rendered_sha256": FAKE_DIGEST})

    async def test_a_tampered_prompt_never_reaches_the_tool(
        self,
        boundary: ToolBoundary,
        renderer: PromptRenderer,
        audit_store: Any,
    ) -> None:
        """Verification happens at the decision point, not only at construction."""

        rendered = renderer.render(prompt_id="summarize", variables={"topic": "a"})
        object.__setattr__(rendered, "_text", "a different prompt entirely")

        with pytest.raises(PromptBindingRefusedError):
            await boundary.invoke(**invoke_kwargs(prompt=rendered))

        assert audit_store.invocations == []

    async def test_a_verified_prompt_is_recorded_on_the_invocation(
        self, boundary: ToolBoundary, renderer: PromptRenderer
    ) -> None:
        rendered = renderer.render(prompt_id="summarize", variables={"topic": "a"})

        outcome = await boundary.invoke(**invoke_kwargs(prompt=rendered))

        assert outcome.succeeded
        assert outcome.invocation.producer_kind is ProducerKind.MODEL_TURN
        assert outcome.invocation.prompt_binding == rendered.binding

    async def test_a_call_with_no_prompt_is_typed_as_system_not_forgotten(
        self, boundary: ToolBoundary
    ) -> None:
        outcome = await boundary.invoke(**invoke_kwargs())

        assert outcome.invocation.producer_kind is ProducerKind.SYSTEM
        assert outcome.invocation.prompt_binding is None


class TestRedactionHappensBeforeTheDigest:
    def test_a_held_secret_is_scrubbed_from_both_the_text_and_its_hash(self) -> None:
        """``rendered_sha256`` covers the redacted text — the bytes actually sent."""

        secret = "super-secret-value-1234"
        registry = PromptRegistry()
        registry.register(
            PromptTemplate(prompt_id="p", version="1.0.0", source="key is $key")
        )
        renderer = PromptRenderer(
            registry=registry,
            secret_provider=MappingSecretProvider({"api": secret}),
        )

        rendered = renderer.render(prompt_id="p", variables={"key": secret})

        assert secret not in rendered.text
        assert rendered.binding.matches_rendered(rendered.text)
