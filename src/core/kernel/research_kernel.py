"""Small execution boundary shared by current and future adapters."""

from dataclasses import dataclass
from typing import Generic, ParamSpec, Protocol, TypeVar

from .registry import TypedRegistry

ParametersT = ParamSpec("ParametersT")
ResultT = TypeVar("ResultT", covariant=True)


class KernelExecutor(Protocol[ParametersT, ResultT]):
    """Async callable implementing the execution behavior behind the kernel."""

    async def __call__(
        self,
        *args: ParametersT.args,
        **kwargs: ParametersT.kwargs,
    ) -> ResultT: ...


@dataclass(frozen=True, slots=True)
class ResearchKernel(Generic[ParametersT, ResultT]):
    """Own the explicit executor and component registry for one execution path."""

    executor: KernelExecutor[ParametersT, ResultT]
    registry: TypedRegistry

    async def execute(
        self,
        *args: ParametersT.args,
        **kwargs: ParametersT.kwargs,
    ) -> ResultT:
        """Delegate through the single injected execution callable."""

        return await self.executor(*args, **kwargs)
