"""
chart_view.py
=============
Draws a chart_engine.Chart with Altair, in the school theme's accent: lines and areas for progression,
bars (grouped, horizontal or stacked) for comparisons, a donut for shares, dots for two stats against
each other, a heatmap for two groupings, and histograms and box plots for how a stat is spread.
Shared by the Charts tab, the Ask page and the assistant's panel.
"""

from __future__ import annotations

import altair as alt
import streamlit as st

from branding import current_theme, readable_accent
from chart_engine import Chart

_SCHEME = "tableau10"


def altair_chart(chart: Chart, height: int = 320) -> alt.Chart:
    """The Altair chart for `chart` (which has rows)."""
    spec = chart.spec
    accent = readable_accent(current_theme()["primary"])
    data = alt.Data(values=chart.rows)
    fmt = chart.y_format
    y = alt.Y("y:Q", title=chart.y_label, axis=alt.Axis(format=fmt))
    series = bool(chart.series_label)
    color = alt.Color("series:N", title=chart.series_label, scale=alt.Scale(scheme=_SCHEME)) if series else alt.value(accent)
    tip_y = alt.Tooltip("y:Q", title=chart.y_label, format=fmt)
    base = alt.Chart(data)

    if spec.kind in ("line", "area"):
        temporal = spec.x == "match"
        x = alt.X("x:T" if temporal else "x:N", title=chart.x_label, sort=None,
                  axis=alt.Axis(labelAngle=-35, labelLimit=160, format="%b %d" if temporal else alt.Undefined))
        tips = [alt.Tooltip("label:N", title="Match" if temporal else chart.x_label), tip_y]
        if series:
            tips.insert(0, alt.Tooltip("series:N", title=chart.series_label))
        enc = base.encode(x=x, y=y, color=color, tooltip=tips)
        drawn = enc.mark_area(opacity=0.35, line=True) if spec.kind == "area" else enc.mark_line(point=True)
    elif spec.kind == "hbar":
        drawn = base.mark_bar().encode(
            y=alt.Y("x:N", title=None, sort="-x" if not series else None, axis=alt.Axis(labelLimit=200)),
            x=alt.X("y:Q", title=chart.y_label, axis=alt.Axis(format=fmt)),
            color=color, yOffset="series:N" if series else alt.Undefined,
            tooltip=[alt.Tooltip("x:N", title=chart.x_label), tip_y] + ([alt.Tooltip("series:N")] if series else []))
        height = max(height, 26 * len({r["x"] for r in chart.rows}) * (1 + series))
    elif spec.kind in ("bar", "stacked"):
        x = alt.X("x:N", title=None, sort=None, axis=alt.Axis(labelAngle=-35, labelLimit=180))
        enc = dict(x=x, y=y, color=color,
                   tooltip=[alt.Tooltip("x:N", title=chart.x_label), tip_y] + ([alt.Tooltip("series:N")] if series else []))
        if series and spec.kind == "bar":
            enc["xOffset"] = "series:N"
        drawn = base.mark_bar().encode(**enc)
    elif spec.kind == "pie":
        drawn = base.mark_arc(innerRadius=60, stroke="#101d2c").encode(
            theta=alt.Theta("y:Q"), color=alt.Color("x:N", title=chart.x_label, scale=alt.Scale(scheme=_SCHEME),
                                                    sort=None),
            tooltip=[alt.Tooltip("x:N", title=chart.x_label), tip_y])
    elif spec.kind == "scatter":
        points = base.mark_circle(opacity=0.8, color=accent).encode(
            x=alt.X("x:Q", title=chart.x_label, scale=alt.Scale(zero=False)),
            y=alt.Y("y:Q", title=chart.y_label, scale=alt.Scale(zero=False), axis=alt.Axis(format=fmt)),
            size=alt.Size("size:Q", title="Sample", legend=None, scale=alt.Scale(range=[60, 400])),
            tooltip=[alt.Tooltip("series:N", title=chart.series_label), alt.Tooltip("x:Q", title=chart.x_label),
                     alt.Tooltip("y:Q", title=chart.y_label), alt.Tooltip("size:Q", title="Sample")])
        labels = base.mark_text(align="left", dx=8, dy=-6, fontSize=11, color="#a8b8ca").encode(
            x="x:Q", y="y:Q", text="series:N")
        drawn = points + labels
    elif spec.kind == "heatmap":
        rect = base.mark_rect().encode(
            x=alt.X("x:N", title=chart.x_label, sort=None, axis=alt.Axis(labelAngle=-35)),
            y=alt.Y("x2:N", title=chart.series_label, sort=None),
            color=alt.Color("y:Q", title=chart.y_label, scale=alt.Scale(scheme="blues")),
            tooltip=[alt.Tooltip("x:N", title=chart.x_label), alt.Tooltip("x2:N", title=chart.series_label), tip_y])
        values = [r["y"] for r in chart.rows]
        middle = (min(values) + max(values)) / 2  # dark text on light cells, light on dark
        text = base.mark_text(fontSize=11).encode(
            x=alt.X("x:N", sort=None), y=alt.Y("x2:N", sort=None), text=alt.Text("y:Q", format=fmt or ".0f"),
            color=alt.condition(alt.datum.y > middle, alt.value("#f2f6fb"), alt.value("#080e19")))
        drawn = rect + text
    elif spec.kind == "histogram":
        drawn = base.mark_bar(opacity=0.85).encode(
            x=alt.X("y:Q", bin=alt.Bin(maxbins=15), title=chart.y_label),
            y=alt.Y("count():Q", title="Matches", stack=None),
            color=color, tooltip=[alt.Tooltip("count():Q", title="Matches")])
    elif spec.kind == "box":
        drawn = base.mark_boxplot(extent="min-max", color=accent).encode(
            x=alt.X("x:N", title=chart.x_label, sort=None), y=y,
            color=alt.Color("x:N", legend=None, scale=alt.Scale(scheme=_SCHEME)))
    else:
        raise ValueError(spec.kind)
    return drawn.properties(height=height, title=chart.title)


def draw(chart: Chart, height: int = 320) -> None:
    """The chart on the page, or why there's nothing to draw."""
    if not chart.ok:
        st.info(" ".join(chart.notes) or "There's nothing to chart for that.", icon=":material/insights:")
        return
    st.altair_chart(altair_chart(chart, height), width="stretch")
    for note in chart.notes:
        st.caption(note)
