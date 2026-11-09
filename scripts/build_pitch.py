"""Build dist/energyos-pitch.html: the dashboard with the in-browser engine inlined, so it runs
as a single static file (hosted link, GitHub Pages, or double-click) with no backend."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
page = (root / "web/index.html").read_text()
engine = (root / "web/engine.js").read_text()
for head in ('<!doctype html>\n', '<meta charset="utf-8">\n',
             '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'):
    page = page.replace(head, "", 1)
marker = "<script>\n(() => {"
assert marker in page
page = page.replace(marker, f"<script>\n{engine}\n</script>\n{marker}", 1)
out = root / "dist/energyos-pitch.html"
out.write_text(page)
print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
