"""SVG fragment primitives shared by the layout and the motifs."""


def n(v: float) -> str:
    return f"{v:.3f}"


def rect(x: float, y: float, w: float, h: float, fill: str) -> str:
    return f'<rect x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" fill="{fill}"/>'
