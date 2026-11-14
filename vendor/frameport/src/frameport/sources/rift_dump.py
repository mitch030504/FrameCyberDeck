"""Find Oculus Rift (Windows PC VR) games on disk.

When scanning, the chosen folder's subfolders are the games (the folder itself only if a program sits directly in
it). A subfolder is **one game** when it has candidate programs (see analysis.rift.candidates), no APKs (those are Quest
dumps) and all candidates sit at its top level or under a single subfolder — repacks put the game one or more levels
down, next to installers and archives. Otherwise it's a **collection**: every subfolder is one game (or, again, a
collection), e.g. <PC VR games>/<Game vX -TAG>/<Game>/…/Game.exe. Loose programs in a collection are ignored.
"""
from __future__ import annotations

from pathlib import Path

from ..analysis import rift


def classify(path: Path, tree: rift.Tree | None = None) -> tuple[str, rift.Tree]:
    """("game" | "collection" | "none", walk result)."""
    tree = tree or rift.walk(path)
    cands = rift.candidates(tree)
    if not cands:
        return ("collection" if tree.has_apk else "none"), tree
    places = {tree.child_of(p) for p in cands}
    if not tree.has_apk and len(places - {"."}) <= 1:
        return "game", tree
    return "collection", tree


def scan(root: Path, depth: int = 4, trees: dict | None = None) -> list[Path]:
    """Game folders under root (root itself if it is one game). `trees` collects the walk results by folder, so the
    analysis doesn't walk again."""
    root = Path(root)
    if not root.is_dir():
        return []
    kind, tree = classify(root)
    if trees is not None:
        trees[root] = tree
    cands = rift.candidates(tree)
    # the scanned folder is itself a game only if a candidate sits directly in it; otherwise each subfolder is a game
    # (or a collection) — "one folder per game"
    if kind == "game" and any(tree.child_of(p) == "." for p in cands):
        return [root]
    if kind == "none" or depth <= 0:
        return []
    found = []
    children_with_games = {tree.child_of(p) for p in cands} - {"."}
    if tree.has_apk:  # mixed downloads folder: look at every subfolder
        children_with_games = {p.name for p in root.iterdir() if p.is_dir()}
    for name in sorted(children_with_games):
        child = root / name
        if name.startswith((".", "_")) or not child.is_dir():
            continue
        ckind, ctree = classify(child)
        if ckind == "game":
            if trees is not None:
                trees[child] = ctree
            found.append(child)
        elif ckind == "collection":
            found += scan(child, depth - 1, trees)
    return found
