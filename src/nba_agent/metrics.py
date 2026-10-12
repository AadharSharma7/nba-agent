"""Per-model-call accounting for the non-streaming chat pipeline."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from time import monotonic
from typing import Literal
from uuid import UUID

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from nba_agent.domain import ModelMetric


class MeteredModel(WrapperModel):
    """One wrapper per chat request; shared agents never hold mutable accounting state."""

    def __init__(
        self,
        model: Model,
        records: list[ModelMetric],
        user_message_id: UUID,
        stage: Literal["gatekeeper", "analyst"],
    ) -> None:
        super().__init__(model)
        self.records = records
        self.user_message_id = user_message_id
        self.stage = stage

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        metric = ModelMetric(
            user_message_id=self.user_message_id,
            stage=self.stage,
            call_sequence=len(self.records) + 1,
            provider=self.system,
            model=self.model_name,
            started_at=datetime.now(UTC),
        )
        self.records.append(metric)
        started = monotonic()
        try:
            response = await self.wrapped.request(
                messages, model_settings, model_request_parameters
            )
        except (Exception, asyncio.CancelledError) as exc:
            metric.status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            metric.error_type = type(exc).__name__
            raise
        finally:
            metric.duration_ms = round((monotonic() - started) * 1000)
        metric.status = "completed"
        metric.model = response.model_name or self.model_name
        metric.provider = response.provider_name or self.system
        # An empty default usage object cannot establish that the request was free.
        usage = response.usage
        if not usage.has_values() and usage.cost is None:
            return response
        metric.input_tokens = usage.input_tokens
        metric.output_tokens = usage.output_tokens
        metric.cached_input_tokens = usage.cache_read_tokens
        metric.pricing_status = "unavailable"
        metric.pricing_metadata = {"genai_prices_version": version("genai-prices")}
        try:
            price = response.cost()
            metric.estimated_cost_usd = price.total_price
            metric.pricing_metadata.update(
                {
                    "source": "genai-prices",
                    "input_cost_usd": str(price.input_price),
                    "output_cost_usd": str(price.output_price),
                    "rates": {
                        key: asdict(value) if hasattr(value, "__dataclass_fields__") else value
                        for key, value in vars(price.model_price).items()
                        if not key.startswith("_")
                    },
                    "price_update_timestamp": price.auto_update_timestamp,
                }
            )
        except Exception as exc:
            # Pricing is best-effort and never prevents the NBA answer from completing.
            # Keep only a type, never an exception string or provider response body.
            metric.pricing_metadata["error_type"] = type(exc).__name__
        if usage.cost is not None:
            # Preserve a cost already attached by the model/provider.
            metric.estimated_cost_usd = usage.cost
            metric.pricing_metadata["source"] = "response_usage"
        if metric.estimated_cost_usd is not None:
            metric.pricing_status = "priced"
        return response
