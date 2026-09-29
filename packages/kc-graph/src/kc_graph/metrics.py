# networkx ships incomplete type information; results are converted to typed values below.
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
"""Graph statistics for one space's wiki graph (ADR-009, invariant 4).

Computed only from the edges of this space's own Neo4j instance: degree, Louvain
community and Adamic-Adar similarity between pages. Nothing here can see another space.
"""

from collections.abc import Iterable
from typing import Any

import networkx as nx

from kc_graph import Edge, Similar

__all__ = ["graph_metrics"]


def graph_metrics(
    edges: list[Edge], *, top_similar: int = 3
) -> tuple[dict[str, dict[str, float | int]], list[Similar]]:
    g: nx.Graph[str] = nx.Graph()
    pages: set[str] = set()
    for src_kind, src, _rel, dst_kind, dst in edges:
        a, b = f"{src_kind}:{src}", f"{dst_kind}:{dst}"
        g.add_edge(a, b)
        for kind, node in ((src_kind, a), (dst_kind, b)):
            if kind == "page":
                pages.add(node)
    if not pages:
        return {}, []

    communities = nx.community.louvain_communities(g, seed=0)
    community_of = {n: i for i, members in enumerate(communities) for n in members}
    props: dict[str, dict[str, float | int]] = {
        n.removeprefix("page:"): {"degree": int(g.degree(n)), "community": community_of[n]}
        for n in pages
    }
    pairs = [(a, b) for a in sorted(pages) for b in sorted(pages) if a < b and not g.has_edge(a, b)]
    raw: Iterable[tuple[Any, Any, Any]] = nx.adamic_adar_index(g, pairs)
    scored: list[tuple[str, str, float]] = [
        (str(a), str(b), float(s)) for a, b, s in raw if float(s) > 0
    ]
    best: list[Similar] = []
    for page in sorted(pages):
        mine = sorted((x for x in scored if page in x[:2]), key=lambda x: -x[2])[:top_similar]
        best += [(a.removeprefix("page:"), b.removeprefix("page:"), float(s)) for a, b, s in mine]
    return props, list(dict.fromkeys(best))
