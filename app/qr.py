"""Render a string as an inline SVG QR code for two-factor enrollment.
Uses segno (pure-Python, no binary deps). If segno isn't installed, svg() returns None and the
account page falls back to showing the manual setup key only."""

try:
    import segno
except ImportError:
    segno = None


def available():
    return segno is not None


def svg(text, scale=6, border=4):
    if segno is None:
        return None
    m = segno.make(text, error="m").matrix
    size = len(m)
    dim = (size + border * 2) * scale
    rects = []
    for r, row in enumerate(m):
        for c, v in enumerate(row):
            if v:
                rects.append(f'<rect x="{(c + border) * scale}" y="{(r + border) * scale}" '
                             f'width="{scale}" height="{scale}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
            f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges" role="img" aria-label="QR">'
            f'<rect width="{dim}" height="{dim}" fill="#fff"/>'
            f'<g fill="#000">{"".join(rects)}</g></svg>')
