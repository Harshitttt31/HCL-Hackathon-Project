"""The LangGraph state machine. Control flow is explicit and deterministic; the LLM is never a router."""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.nodes import Deps
from app.agent.state import AgentState


def build_graph(deps: Deps):
    g = StateGraph(AgentState)
    for name, fn in [("input_guard", nodes.input_guard), ("classify", nodes.classify_node), ("privacy_check", nodes.privacy_node),
                     ("refuse", nodes.refuse_node), ("plan", nodes.plan_node), ("execute_tools", nodes.execute_tools),
                     ("document_path", nodes.document_path), ("compose", nodes.compose), ("generate", nodes.generate),
                     ("validate", nodes.validate), ("finalize", nodes.finalize)]:
        g.add_node(name, partial(fn, deps))
    g.add_edge(START, "input_guard")
    g.add_edge("input_guard", "classify")
    g.add_edge("classify", "privacy_check")
    g.add_conditional_edges("privacy_check", lambda s: "plan" if s["privacy"].allowed else "refuse", {"plan": "plan", "refuse": "refuse"})
    g.add_edge("refuse", "compose")
    g.add_edge("plan", "execute_tools")
    g.add_conditional_edges("execute_tools", lambda s: "document_path" if nodes.needs_documents(s) else "compose",
                            {"document_path": "document_path", "compose": "compose"})
    g.add_edge("document_path", "compose")
    g.add_edge("compose", "generate")
    g.add_edge("generate", "validate")
    g.add_edge("validate", "finalize")
    g.add_edge("finalize", END)
    return g.compile()
