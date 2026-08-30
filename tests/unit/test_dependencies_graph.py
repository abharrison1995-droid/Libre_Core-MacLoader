"""Unit tests for the DAG dependency graph, cycle detection, and topological sorting."""

import pytest
from macloader.dependencies.graph import DependencyGraph
from macloader.exceptions import DependencyCycleError, DependencyNotFoundError


def test_graph_resolves_transitive_dependencies() -> None:
    graph = DependencyGraph()
    graph.add_node("lilu", [])
    graph.add_node("whatevergreen", ["lilu"])
    graph.add_node("virtualsmc", ["lilu"])
    graph.add_node("smcbatterymanager", ["virtualsmc"])

    resolved = graph.resolve_ordered_set(["smcbatterymanager", "whatevergreen"])

    # Lilu must come first, followed by virtualsmc and whatevergreen, and smcbatterymanager after virtualsmc
    assert "lilu" in resolved
    assert "virtualsmc" in resolved
    assert "whatevergreen" in resolved
    assert "smcbatterymanager" in resolved

    assert resolved.index("lilu") < resolved.index("whatevergreen")
    assert resolved.index("lilu") < resolved.index("virtualsmc")
    assert resolved.index("virtualsmc") < resolved.index("smcbatterymanager")


def test_graph_detects_cycles() -> None:
    graph = DependencyGraph()
    graph.add_node("a", ["b"])
    graph.add_node("b", ["c"])
    graph.add_node("c", ["a"])  # cycle: a -> b -> c -> a

    with pytest.raises(DependencyCycleError) as exc:
        graph.resolve_ordered_set(["a"])
    assert "Circular dependency detected" in str(exc.value)


def test_graph_fails_on_missing_dependency() -> None:
    graph = DependencyGraph()
    graph.add_node("whatevergreen", ["lilu"])
    # lilu was never added

    with pytest.raises(DependencyNotFoundError) as exc:
        graph.resolve_ordered_set(["whatevergreen"])
    assert "Dependency 'lilu' not found" in str(exc.value)
