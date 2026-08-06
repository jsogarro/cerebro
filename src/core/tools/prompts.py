"""The renderer seam: prompt text and its digest, minted as one value.

Packet 4A's non-guarantee 1, stated exactly: ``PromptBinding.for_rendered()``
computes honest digests, but nothing stops a caller passing ``PromptBinding``
arbitrary hex, so the contract enforces presence and shape, not correspondence
to text actually sent. Its proposed fix was to change the *shape* of the
seam — the renderer returns ``(text, binding)`` as one value and is the only
mint site, and the boundary refuses a binding it did not mint.

**How "did not mint" is decided, and why not with a ledger.** The obvious
reading is provenance: the renderer remembers what it produced and the boundary
checks membership. That implementation is weaker than it looks. It is
process-local, so a binding minted before a restart — or in the worker that
replays a run — is refused although it is perfectly honest. It grows without
bound. And it answers the wrong question: it establishes that *we made this*,
when what matters is that *this digest describes this text*.

So the seam is a **self-verifying pair** instead. :class:`RenderedPrompt` holds
the template source, the exact text to be sent, and the binding, and it cannot
exist unless both digests actually match the material beside them. Forgery is
not detected, it is impossible: hand-constructing one with arbitrary hex raises,
because the hex does not hash the text it accompanies. The property survives
restarts and replay, which a ledger does not.

The boundary re-verifies at the decision point anyway, and takes
``RenderedPrompt`` rather than ``PromptBinding`` in its public signature. There
is no parameter through which a bare binding can arrive.

**What this does not establish**, stated rather than implied: the digests prove
that this text was rendered from this template source. They do not prove that
the template source is what ``prompt_id`` names in the repository. Registration
is the trust root, and a caller that registers a fabricated template gets an
honest digest of a fabricated template. Closing that needs a build-time
manifest of template digests, which is not this packet's.
"""

from collections.abc import Mapping
from string import Template
from typing import final

from src.core.contracts.provenance import PromptBinding
from src.core.contracts.redaction import redact

from .errors import PromptBindingRefusedError
from .secrets import SecretProvider


@final
class PromptTemplate:
    """One registered prompt template, identified and versioned."""

    __slots__ = ("_prompt_id", "_source", "_version")

    def __init__(self, *, prompt_id: str, version: str, source: str) -> None:
        if not prompt_id:
            raise ValueError("a prompt template needs an id")
        if not version:
            raise ValueError(f"prompt {prompt_id!r} needs a version")
        if not source:
            raise ValueError(f"prompt {prompt_id!r} needs a body")
        self._prompt_id = prompt_id
        self._version = version
        self._source = source

    @property
    def prompt_id(self) -> str:
        return self._prompt_id

    @property
    def version(self) -> str:
        return self._version

    @property
    def source(self) -> str:
        return self._source

    def substitute(self, variables: Mapping[str, str]) -> str:
        """Fill the template, raising on a missing variable.

        ``string.Template`` rather than ``str.format`` on purpose. Format
        strings evaluate attribute and index access, so ``{x.__class__}``
        inside a value that reached a template from an untrusted page is a read
        primitive. ``Template`` substitutes names and nothing else. And
        ``substitute`` rather than ``safe_substitute``: a missing variable
        should fail loudly, not ship a prompt with a literal ``$placeholder``
        in it and hash that as though it were intended.
        """

        return Template(self._source).substitute(variables)


@final
class PromptRegistry:
    """The templates a renderer may render. The only source of prompt bodies."""

    __slots__ = ("_templates",)

    def __init__(self) -> None:
        self._templates: dict[str, PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> None:
        existing = self._templates.get(template.prompt_id)
        if existing is not None and existing.source != template.source:
            raise ValueError(
                f"prompt {template.prompt_id!r} is already registered with a "
                "different body; re-registering would change what an "
                "already-admitted run means"
            )
        self._templates[template.prompt_id] = template

    def get(self, prompt_id: str) -> PromptTemplate:
        try:
            return self._templates[prompt_id]
        except KeyError:
            raise KeyError(f"no prompt is registered as {prompt_id!r}") from None


@final
class RenderedPrompt:
    """Prompt text and its binding, inseparable and mutually verified.

    The invariant is checked in ``__init__``, so an instance that exists is an
    instance whose digests describe its own contents. The boundary never has to
    trust where one came from.
    """

    __slots__ = ("_binding", "_template_source", "_text")

    def __init__(
        self, *, template_source: str, text: str, binding: PromptBinding
    ) -> None:
        if not binding.matches_template(template_source):
            raise PromptBindingRefusedError(
                f"template_sha256 does not hash the template source it "
                f"accompanies for prompt {binding.prompt_id!r}"
            )
        if not binding.matches_rendered(text):
            raise PromptBindingRefusedError(
                f"rendered_sha256 does not hash the text it accompanies for "
                f"prompt {binding.prompt_id!r}"
            )
        self._template_source = template_source
        self._text = text
        self._binding = binding

    @property
    def text(self) -> str:
        """The exact, already-redacted text to send. Nothing else may be sent."""

        return self._text

    @property
    def template_source(self) -> str:
        return self._template_source

    @property
    def binding(self) -> PromptBinding:
        return self._binding

    def verify(self) -> None:
        """Re-check the invariant at the point of use.

        Cheap, and it is what turns "was constructed correctly" into "is
        correct now" for a value that crossed a call boundary.
        """

        if not self._binding.matches_template(self._template_source):
            raise PromptBindingRefusedError(
                f"template digest no longer matches for {self._binding.prompt_id!r}"
            )
        if not self._binding.matches_rendered(self._text):
            raise PromptBindingRefusedError(
                f"rendered digest no longer matches for {self._binding.prompt_id!r}"
            )


@final
class PromptRenderer:
    """The sole mint site for a :class:`RenderedPrompt`.

    Redaction happens here rather than downstream because
    :class:`~src.core.contracts.provenance.PromptBinding` documents
    ``rendered_sha256`` as covering the redacted text — the exact bytes sent to
    the model. Redacting after hashing would pin a digest of text that was
    never sent; redacting after sending would send the secret.
    """

    __slots__ = ("_registry", "_secret_provider")

    def __init__(
        self, *, registry: PromptRegistry, secret_provider: SecretProvider
    ) -> None:
        self._registry = registry
        self._secret_provider = secret_provider

    def render(
        self, *, prompt_id: str, variables: Mapping[str, str] | None = None
    ) -> RenderedPrompt:
        """Render a registered template and pin what was rendered."""

        template = self._registry.get(prompt_id)
        filled = template.substitute(variables or {})
        redacted = redact(
            filled, known_secret_values=self._secret_provider.secret_values()
        )
        if not isinstance(redacted, str):  # pragma: no cover - redact is total on str
            raise TypeError("redacting prompt text must yield text")
        binding = PromptBinding.for_rendered(
            prompt_id=template.prompt_id,
            prompt_version=template.version,
            template_source=template.source,
            rendered_text=redacted,
        )
        return RenderedPrompt(
            template_source=template.source, text=redacted, binding=binding
        )


def require_rendered_prompt(prompt: object) -> RenderedPrompt:
    """Accept only a verified :class:`RenderedPrompt`.

    A bare :class:`PromptBinding` is rejected by name, because that is the
    exact shape 4A reported as unenforceable and the error should say so rather
    than read as a generic type complaint.
    """

    if isinstance(prompt, PromptBinding):
        raise PromptBindingRefusedError(
            "a bare PromptBinding is not accepted: its digests are unverifiable "
            "without the text they claim to pin. Pass the RenderedPrompt the "
            "renderer returned."
        )
    if not isinstance(prompt, RenderedPrompt):
        raise PromptBindingRefusedError(
            f"expected a RenderedPrompt, got {type(prompt).__name__}"
        )
    prompt.verify()
    return prompt


__all__ = [
    "PromptRegistry",
    "PromptRenderer",
    "PromptTemplate",
    "RenderedPrompt",
    "require_rendered_prompt",
]
