# Third-party notices

Command Room bundles no third-party runtime code. The notices below cover
third-party work whose **knowledge** (heuristics, design discipline) was
adapted into Command Room's own text and code, with attribution under the
source's license.

## AntV chart-visualization skills (MIT)

The chart-type selection heuristics, "one message per chart" rule, and
axis/label discipline in `shared/CHART_SELECTION.md` (SPEC OUT3), and the
fixed-template-library metaphor behind the infographic layout registry
`shared/INFOGRAPHIC_LAYOUTS.md` (SPEC OUT4 — "constrain structure, let content
vary": a closed set of curated layouts chosen from a table, so output is
consistent by construction), are adapted from AntV's chart-visualization
skills (https://github.com/antvis/chart-visualization-skills), released under
the MIT License. No AntV source code, renderer, or verbatim text is bundled —
`shared/scripts/charts.py` and `shared/scripts/infographic.py` are independent
stdlib implementations and the 8 launch layouts are Command Room's own; the
adaptation is the selection / template-library knowledge itself, credited here
per the license.

MIT License

Copyright (c) AntV

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
