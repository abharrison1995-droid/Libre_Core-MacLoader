"""Directed Acyclic Graph (DAG) for dependency resolution, cycle detection, and topological sorting."""

from typing import Callable, Dict, List, Set

from macloader.exceptions import DependencyCycleError, DependencyNotFoundError


class DependencyGraph:
    """Represents dependency relationships between catalog components."""

    def __init__(self) -> None:
        # node -> list of parent dependencies that this node depends on
        self._adjacency: Dict[str, List[str]] = {}

    def add_node(self, node_id: str, dependencies: List[str]) -> None:
        """Add a dependency node and its immediate prerequisite parent dependencies."""
        self._adjacency[node_id.lower()] = [d.lower() for d in dependencies]

    def has_node(self, node_id: str) -> bool:
        return node_id.lower() in self._adjacency

    def get_dependencies(self, node_id: str) -> List[str]:
        return self._adjacency.get(node_id.lower(), [])

    def resolve_ordered_set(
        self, requested_ids: List[str], cancel: Callable[[], bool] | None = None
    ) -> List[str]:
        """Resolve all transitive dependencies for the requested IDs and return topologically sorted list.

        Prerequisites will always appear BEFORE dependents in the returned list.
        Example: If WhateverGreen depends on Lilu, the returned order is [Lilu, WhateverGreen].
        """
        # 1. Collect all nodes in the transitive subgraph
        all_needed: Set[str] = set()
        to_visit: List[str] = [r.lower() for r in requested_ids]

        while to_visit:
            if cancel and cancel():
                raise ValueError("Dependency resolution cancelled")
            current = to_visit.pop()
            if current not in self._adjacency:
                raise DependencyNotFoundError(f"Dependency '{current}' not found in dependency graph.")

            if current not in all_needed:
                all_needed.add(current)
                for parent_dep in self._adjacency[current]:
                    to_visit.append(parent_dep)

        # 2. Detect cycles using 3-color DFS (0=unvisited, 1=visiting, 2=visited)
        visited: Dict[str, int] = {node: 0 for node in all_needed}
        ordered: List[str] = []

        def dfs(node: str, path: List[str]) -> None:
            if cancel and cancel():
                raise ValueError("Dependency resolution cancelled")
            visited[node] = 1  # visiting
            path.append(node)

            for parent_dep in self._adjacency[node]:
                if cancel and cancel():
                    raise ValueError("Dependency resolution cancelled")
                if parent_dep in all_needed:
                    if visited[parent_dep] == 1:
                        cycle_path = " -> ".join(path + [parent_dep])
                        raise DependencyCycleError(f"Circular dependency detected: {cycle_path}")
                    elif visited[parent_dep] == 0:
                        dfs(parent_dep, path)

            path.pop()
            visited[node] = 2  # visited
            # Append node after all its parents have been visited
            if node not in ordered:
                ordered.append(node)

        for node in sorted(list(all_needed)):
            if visited[node] == 0:
                dfs(node, [])

        return ordered
