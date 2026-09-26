"""llm-cost-sidecar: per-request token/cost tracing middleware with budget alerts."""

from .pricing import Pricing, estimate_tokens
from .trace import TraceStore
from .middleware import TracedCaller, trace_calls, tenant, current_tenant
from .simulate import SimulatedLLM, generate_traffic
from .budgets import BudgetConfig, BudgetWatcher
from .alerts import AlertLog, PrintSink, WebhookSink, FanoutSink
from . import aggregator
from .server import create_app, serve

__version__ = "1.0.0"
__all__ = [
    "Pricing", "estimate_tokens", "TraceStore", "TracedCaller", "trace_calls",
    "tenant", "current_tenant", "SimulatedLLM", "generate_traffic",
    "BudgetConfig", "BudgetWatcher", "AlertLog", "PrintSink", "WebhookSink",
    "FanoutSink", "aggregator", "create_app", "serve",
]
