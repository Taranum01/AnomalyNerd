"""Local HTML report for a CSV analysis."""

import html
import json
from pathlib import Path


def render_report(table, flags, caption=None):
    def esc(value):
        return html.escape(str(value), quote=True)

    controls = (
        Path(__file__).with_name("report_controls.html").read_text(encoding="utf-8")
    )
    cards = []
    for flag in flags:
        coords = ", ".join(f"{key}={value}" for key, value in flag.coords.items())
        cards.append(
            f'<article class="finding" data-priority="{esc(flag.priority)}">'
            f'<div class="card-top"><span class="priority {esc(flag.priority.lower())}">{esc(flag.priority)}</span>'
            f'<h2>{esc(flag.type.replace("_", " "))}</h2></div>'
            f'<p class="context">{esc(flag.axis or "Table")}{(" · " + esc(coords)) if coords else ""}</p>'
            f"<p>{esc(flag.suggestion)}</p>"
            f'<details><summary>Computed evidence</summary><div class="code-wrapper"><pre>{esc(json.dumps(flag.detail, indent=2, sort_keys=True))}</pre><button class="copy-btn" type="button" aria-label="Copy computed evidence">Copy evidence</button></div></details></article>'
        )
    counts = {
        priority: sum(flag.priority == priority for flag in flags)
        for priority in ("HIGH", "MEDIUM", "LOW")
    }
    summary = "".join(
        f'<div class="stat"><b>{counts[p]}</b><span>{p.title()} priority</span></div>'
        for p in counts
    )
    caption_html = (
        f'<section class="caption"><h2>Grounded caption</h2><p>{esc(caption)}</p></section>'
        if caption
        else ""
    )
    findings = (
        "".join(cards) or '<p class="empty">No findings flagged for this table.</p>'
    )
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AnomalyNerd report</title><style>
:root{--font-scale:1;--bg:#f3f6f8;--text:#172c3e;--surface:#fff;--line:#d3dfe6;--muted:#4e6373;--caption:#e7f0f2;color-scheme:light;font:calc(16px * var(--font-scale))/1.6 Verdana,sans-serif;color:var(--text);background:var(--bg)}*{box-sizing:border-box}body{margin:0}main{max-width:1100px;margin:0 auto;padding:40px 24px 64px}header{padding-top:32px;border-bottom:1px solid #cbd7df;padding-bottom:24px}.eyebrow{font-size:calc(12px * var(--font-scale));font-weight:700;letter-spacing:.12em;color:#276573}h1{font-family:Georgia,serif;font-size:calc(32px * var(--font-scale));line-height:1.2;margin:8px 0;overflow-wrap:anywhere}h2{font-size:calc(19px * var(--font-scale));line-height:1.35;margin:0}p{margin:12px 0}.muted,.context{color:var(--muted)}.stats{display:flex;gap:12px;margin:24px 0}.stat{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;flex:1}.stat b{display:block;font-size:calc(28px * var(--font-scale))}.stat span{font-size:calc(13px * var(--font-scale));color:var(--muted)}.caption{border-left:4px solid #276573;background:var(--caption);padding:20px 24px;border-radius:0 12px 12px 0;margin:24px 0}.toolbar{display:flex;align-items:center;gap:12px;margin:28px 0 16px;flex-wrap:wrap}select{min-height:44px;font:inherit;padding:8px 12px;border:1px solid #8195a3;border-radius:6px;background:var(--surface)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.finding{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:22px;overflow-wrap:anywhere}.card-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.priority{font-size:calc(11px * var(--font-scale));font-weight:750;padding:4px 8px;border-radius:5px;letter-spacing:.05em}.high{background:#fce6e5;color:#8e2821}.medium{background:#fff1d7;color:#77550b}.low{background:#e7eef5;color:#33536e}.context{font-size:calc(13px * var(--font-scale))}summary{cursor:pointer;color:#225f71;font-size:calc(14px * var(--font-scale))}pre{font:calc(13px * var(--font-scale))/1.5 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--bg);padding:12px;border-radius:6px}.empty{padding:24px;border:1px dashed #a3b6c3;border-radius:10px}.finding[hidden]{display:none}footer{font-size:calc(13px * var(--font-scale));color:var(--muted);margin-top:32px}@media(max-width:620px){main{padding:24px 16px}.grid{grid-template-columns:1fr}.stat{padding:12px}.stats{gap:8px}}@media print{body{background:var(--surface)}main{padding:0}.toolbar{display:none}.grid{display:block}.finding{break-inside:avoid;margin-bottom:12px}.finding[hidden]{display:block}details pre{display:block}}
select:focus-visible,summary:focus-visible,button:focus-visible{outline:2px solid #225f71;outline-offset:4px}summary{min-height:44px;padding:8px 0}@media(max-width:620px){h1{font-size:calc(25px * var(--font-scale))}}@media print{#ui-toolbar,#ui-edit-bar,#ui-editor-modal,.copy-btn{display:none!important}}
[data-theme="dark"]{--bg:#162631;--text:#e5eff4;--surface:#233847;--line:#4d6674;--muted:#b6c8d3;--caption:#28424d} .code-wrapper{position:relative}.copy-btn{font:inherit;min-height:44px;padding:8px 16px;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:4px}button:hover{filter:brightness(.92)}#ui-toolbar{color:var(--text);max-width:calc(100vw - 32px)}#ui-toolbar button{min-width:44px;min-height:44px}#ui-edit-bar{max-height:55vh;overflow:auto}.caption{border-left:none;border-radius:4px}.grid{grid-template-columns:1fr}.finding{border-radius:4px}.stat{border-radius:4px}footer{padding-bottom:64px}@media print{*{color:#172c3e!important}body{background:#fff!important}button{display:none!important}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style><noscript><style>#ui-toolbar,.copy-btn,#priority-filter{display:none}</style></noscript></head><body><main id="ui-content"><header><div class="eyebrow">ANOMALYNERD / TABLE REVIEW <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#276573" stroke-width="1.5"><path d="M3 3v18h18"/><path d="M5 15l4-5 4 3 5-8"/><circle cx="13" cy="13" r="2"/></svg></div>"""
        + f"""
<h1>{esc(table.name)}</h1><p class="muted">Metric: {esc(table.metric_name)} · {len(table.rows)} rows</p>
<p>Findings point to patterns worth reviewing. They do not diagnose errors or establish their cause.</p></header>
<section class="stats" aria-label="Priority counts">{summary}</section>{caption_html}
<div class="toolbar"><h2>Findings</h2><label for="priority-filter">Show</label><select id="priority-filter"><option value="all">All priorities</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select><span id="shown" role="status">{len(flags)} shown</span></div>
<section class="grid">{findings}</section><footer><span id="report-edit-status" role="status"></span> Local report. No model calls, remote assets or uploaded data. Computed evidence is available on each card.</footer>
</main><script>
document.addEventListener('change', function(event) {{
if (event.target.id !== 'priority-filter') return; const value = event.target.value;
let count = 0; document.querySelectorAll('.finding').forEach(card => {{card.hidden = value !== 'all' && card.dataset.priority !== value; if (!card.hidden) count++;}});
document.getElementById('shown').textContent = count + ' shown';
}});
document.addEventListener('click', async function(event) {{
const btn = event.target.closest('.copy-btn'); if (!btn) return;
try {{ await navigator.clipboard.writeText(btn.previousElementSibling.textContent); btn.textContent = 'Copied'; }}
catch (error) {{ btn.textContent = 'Select evidence to copy'; }}
}});
</script>{controls}</body></html>"""
    )
