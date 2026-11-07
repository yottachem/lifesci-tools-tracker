"""Server-rendered SVG charts: no JavaScript, readable without CSS, colors from the page's tokens.

Every chart takes year-keyed series. The partial current year is drawn dashed and labeled, so a
half-year never reads as a decline.
"""
import math
from html import escape

FONT = 'font-family="var(--font-data)"'


def fmt(v, kind="n"):
    if v is None:
        return "–"
    if kind == "usd":
        return f"${v / 1e9:,.1f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.0f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}K"
    if kind == "pct":
        return f"{v:.0%}"
    if kind == "rate":
        return f"{v:,.1f}" if v < 100 else f"{v:,.0f}"
    return f"{v:,.0f}"


def scale(d0, d1, r0, r1):
    """Linear map from the domain [d0, d1] to the range [r0, r1]."""
    k = (r1 - r0) / (d1 - d0) if d1 != d0 else 0
    return lambda v: r0 + (v - d0) * k


def nice_ticks(lo, hi, n=4):
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t, out = math.floor(lo / step) * step, []
    while not out or out[-1] < hi - step * 0.001:  # last tick at or above hi
        out.append(round(t, 10))
        t += step
    return out


def tick_label(v, kind):
    if kind == "usd":
        return f"${v / 1e9:g}B" if v >= 1e9 else f"${v / 1e6:g}M" if v >= 1e6 else f"${v:g}"
    if kind == "pct":
        return f"{v:.0%}"
    return f"{v / 1000:g}K" if v >= 10000 else f"{v:,.0f}" if v >= 1 or v == 0 else f"{v:g}"


def line_chart(series, years, *, partial=None, kind="n", width=640, height=230, label_w=150, ymax=None, desc=""):
    """series: [{"name", "values": {year: v}, "emph": 0|1|2}] - emph 1 = accent, 2 = second accent, 0 = gray."""
    top, bottom, left = 14, 26, 44
    right = label_w if any(s.get("label", True) for s in series) else 12
    w, h = width - left - right, height - top - bottom
    ys = [y for y in years]
    vals = [v for s in series for y, v in s["values"].items() if v is not None and y in ys]
    hi = ymax if ymax is not None else max(vals or [1])
    ticks = nice_ticks(0, hi)
    hi = ticks[-1]
    x = scale(ys[0], max(ys[-1], ys[0] + 1), left, left + w)
    y = scale(0, hi, top + h, top)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(desc)}" class="chart">']
    for t in ticks:
        out.append(f'<line x1="{left}" x2="{left + w}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{y(t) + 3.5:.1f}" text-anchor="end" class="tick">{tick_label(t, kind)}</text>')
    for yr in ys:
        if yr % 5 == 0 or yr == ys[0] or (yr == ys[-1] and yr % 5 >= 2):
            out.append(f'<text x="{x(yr):.1f}" y="{height - 8}" text-anchor="middle" class="tick">{yr}</text>')
    if partial in ys:
        out.append(f'<rect x="{x(partial - 0.5):.1f}" y="{top}" width="{x(partial) - x(partial - 0.5) + 6:.1f}" height="{h}" class="partial"/>')
    labels = []
    for s in sorted(series, key=lambda s: s.get("emph", 0)):
        cls = {0: "s-g", 1: "s-1", 2: "s-2", 3: "s-3", 4: "s-4"}.get(s.get("emph", 0), "s-g")
        pts = [(yr, s["values"].get(yr)) for yr in ys if s["values"].get(yr) is not None]
        full = [(a, b) for a, b in pts if a != partial]
        if len(full) > 1:
            d = " ".join(f"{'M' if i == 0 else 'L'}{x(a):.1f},{y(b):.1f}" for i, (a, b) in enumerate(full))
            out.append(f'<path d="{d}" class="ln {cls}"><title>{escape(s["name"])}</title></path>')
        if partial and full and pts[-1][0] == partial:
            a, b = full[-1]
            out.append(f'<path d="M{x(a):.1f},{y(b):.1f} L{x(partial):.1f},{y(pts[-1][1]):.1f}" class="ln dash {cls}"/>'
                       f'<circle cx="{x(partial):.1f}" cy="{y(pts[-1][1]):.1f}" r="2.6" class="pt-open {cls}"/>')
        if full and s.get("label", True):
            a, b = full[-1]
            labels.append([y(b), s["name"], fmt(b, kind), cls, x(ys[-1])])  # labels clear of the partial year
    labels.sort()
    for i in range(1, len(labels)):  # nudge overlapping end labels apart
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 13)
    for ly, name, val, cls, lx in labels:
        out.append(f'<text x="{lx + 6:.1f}" y="{ly + 3.5:.1f}" class="end {cls}-t">{escape(name)} <tspan class="v">{val}</tspan></text>')
    if partial in ys:
        out.append(f'<text x="{x(partial):.1f}" y="{top - 3}" text-anchor="end" class="note">{partial} partial</text>')
    out.append("</svg>")
    return "".join(out)


def bar_chart(values, years, *, partial=None, kind="n", width=640, height=200, desc="", lagged=()):
    top, bottom, left, right = 14, 26, 44, 12
    w, h = width - left - right, height - top - bottom
    hi = nice_ticks(0, max([v for v in values.values() if v] or [1]))[-1]
    bw = w / len(years)
    y = scale(0, hi, top + h, top)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(desc)}" class="chart">']
    for t in nice_ticks(0, hi):
        out.append(f'<line x1="{left}" x2="{left + w}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{y(t) + 3.5:.1f}" text-anchor="end" class="tick">{tick_label(t, kind)}</text>')
    for i, yr in enumerate(years):
        v = values.get(yr) or 0
        cls = "bar part" if yr == partial or yr in lagged else "bar"
        out.append(f'<rect x="{left + i * bw + 1:.1f}" y="{y(v):.1f}" width="{bw - 2:.1f}" height="{top + h - y(v):.1f}" class="{cls}">'
                   f'<title>{yr}: {fmt(v, kind)}</title></rect>')
        if yr % 5 == 0 or (yr == years[-1] and yr % 5 >= 2):
            out.append(f'<text x="{left + (i + .5) * bw:.1f}" y="{height - 8}" text-anchor="middle" class="tick">{yr}</text>')
    out.append("</svg>")
    return "".join(out)


def spark(values, years, *, partial=None, width=150, height=34, cls="s-1"):
    pts = [(yr, values.get(yr)) for yr in years if values.get(yr) is not None and yr != partial]
    if len(pts) < 2:
        return '<svg class="spark" viewBox="0 0 150 34"></svg>'
    hi = max(v for _, v in pts) or 1
    x = scale(years[0], years[-1], 2, width - 2)
    y = scale(0, hi, height - 3, 5)
    d = " ".join(f"{'M' if i == 0 else 'L'}{x(a):.1f},{y(b):.1f}" for i, (a, b) in enumerate(pts))
    a, b = pts[-1]
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" aria-hidden="true"><path d="{d}" class="ln {cls}"/>'
            f'<circle cx="{x(a):.1f}" cy="{y(b):.1f}" r="2.4" class="pt {cls}"/></svg>')


def scatter(points, *, width=900, height=520, desc="", char_w=6.8):
    """points: [{"label", "href", "x" (>0, log), "y" (fraction), "r_value", "color_value" (0-1)}]"""
    top, bottom, left, right = 30, 46, 58, 70
    w, h = width - left - right, height - top - bottom
    xs = [p["x"] for p in points if p["x"]]
    lx0, lx1 = math.floor(math.log10(min(xs))), math.ceil(math.log10(max(xs)))
    yv = [p["y"] for p in points if p["y"] is not None]
    span = max(yv) - min(0, min(yv))
    ticks = nice_ticks(min(0, min(yv)) - 0.12 * span, max(yv), 5)  # room under the low-growth cluster
    y0, y1 = ticks[0], ticks[-1]
    lin_x = scale(lx0, lx1, left, left + w)

    def x(v):
        return lin_x(math.log10(v))
    y = scale(y0, y1, top + h, top)
    rmax = max(p["r_value"] or 0 for p in points) or 1

    def r(v):
        return 5 + 18 * math.sqrt((v or 0) / rmax)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(desc)}" class="chart scatter">']
    for t in ticks:
        out.append(f'<line x1="{left}" x2="{left + w}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid{" zero" if t == 0 else ""}"/>'
                   f'<text x="{left - 8}" y="{y(t) + 3.5:.1f}" text-anchor="end" class="tick">{t:+.0%}</text>')
    for e in range(lx0, lx1 + 1):
        out.append(f'<line x1="{x(10 ** e):.1f}" x2="{x(10 ** e):.1f}" y1="{top}" y2="{top + h}" class="grid"/>'
                   f'<text x="{x(10 ** e):.1f}" y="{top + h + 16}" text-anchor="middle" class="tick">{fmt(10 ** e)}</text>')
    out.append(f'<text x="{left + w}" y="{height - 4}" text-anchor="end" class="axis">Papers in the latest full year (log scale) →</text>'
               f'<text x="{left}" y="{top - 8}" class="axis">↑ Annual growth, 5 years</text>')
    placed, dots, labels = [], [], []
    for p in points:
        if p["x"] and p["y"] is not None:
            placed.append((x(p["x"]) - r(p["r_value"]), y(p["y"]) - r(p["r_value"]),
                           x(p["x"]) + r(p["r_value"]), y(p["y"]) + r(p["r_value"])))
    for p in sorted(points, key=lambda p: -(p["r_value"] or 0)):
        if not p["x"] or p["y"] is None:
            continue
        cx, cy, rr = x(p["x"]), y(p["y"]), r(p["r_value"])
        level = min(4, int((p["color_value"] or 0) * 5))
        dots.append(f'<a href="{p["href"]}"><circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rr:.1f}" class="c{level}">'
                    f'<title>{escape(p["tip"])}</title></circle></a>')
        tw = char_w * len(p["label"])  # approximate label width (6.8 px a character at 12.5px)
        fs = char_w / 0.54  # font size implied by char_w, for the label's height
        # right, left, above, below, then further out; first spot that overlaps no circle or label wins
        cands = [(cx + rr + 5, cy + 4, "start"), (cx - rr - 5, cy + 4, "end"), (cx, cy - rr - 6, "middle"),
                 (cx, cy + rr + 15, "middle"), (cx + rr + 5, cy - rr - 4, "start"), (cx + rr + 5, cy + rr + 12, "start"),
                 (cx - rr - 5, cy - rr - 4, "end"), (cx - rr - 5, cy + rr + 12, "end")]
        own = (cx - rr, cy - rr, cx + rr, cy + rr)

        def cost(c):
            """(outside the plot, overlap area with other circles and labels): lowest wins, first on ties."""
            tx, ty, anchor = c
            x0 = tx if anchor == "start" else tx - tw if anchor == "end" else tx - tw / 2
            box = (x0, ty - 0.85 * fs, x0 + tw, ty + 0.3 * fs)
            outside = not (box[0] >= left - 30 and box[2] <= width and box[1] >= 0 and box[3] <= top + h)
            area = sum(max(0, min(box[2], b[2]) - max(box[0], b[0])) * max(0, min(box[3], b[3]) - max(box[1], b[1]))
                       for b in placed if b != own)
            return (outside, area), box
        tx, ty, anchor = min(cands, key=lambda c: cost(c)[0])
        box = cost((tx, ty, anchor))[1]
        placed.append(box)
        labels.append(f'<a href="{p["href"]}"><text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" class="lab">{escape(p["label"])}</text></a>')
    out += dots + labels
    out.append("</svg>")
    return "".join(out)
