"""Light presentation theme with architectural backgrounds and green accents."""
from __future__ import annotations

import base64
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st


@lru_cache(maxsize=1)
def _building_image() -> str:
    path = Path(__file__).resolve().parent / 'assets' / 'building_background.svg'
    if not path.is_file():
        return ''
    return 'data:image/svg+xml;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def _text(value) -> str:
    return escape(str(value or ''), quote=True)


def apply_theme() -> None:
    """Style cards and brand elements; native data grids use config.toml."""
    image = _building_image()
    image_css = f"url('{image}')" if image else 'none'
    st.markdown(f"""
<style>
:root {{
  --k-ink: #000000;
  --k-muted: #202820;
  --k-line: #d6e1dc;
  --k-sage: #426f60;
  --k-deep: #315749;
  --k-paper: #ffffff;
  --k-soft: #eaf1ec;
}}
.stApp {{background:#f5f8f5;color:var(--k-ink);color-scheme:light;}}
[data-testid="stHeader"] {{background:rgba(245,248,245,.96);}}
[data-testid="stSidebar"] {{background:#eaf1ec;color:var(--k-ink);}}
[data-testid="stExpander"], [data-testid="stForm"],
[data-testid="stMetric"], [data-testid="stVerticalBlockBorderWrapper"] > div,
.k-side-account, .k-empty {{
  background-image:linear-gradient(100deg,rgba(255,255,255,.99),rgba(255,255,255,.94)),{image_css};
  background-position:center,right center;background-size:cover,auto 100%;background-repeat:no-repeat;
  border:1px solid var(--k-line);border-radius:14px;color:var(--k-ink);
}}
[data-testid="stMetric"] {{padding:1rem;border-top:3px solid var(--k-sage);}}
[data-testid="stExpander"] details > summary {{background:rgba(234,241,236,.8);color:#111;}}
[data-testid="stExpander"] details > summary:hover {{background:#dce9df;}}
[data-testid="stWidgetLabel"], [data-testid="stCaptionContainer"],
[data-testid="stMarkdownContainer"] {{color:var(--k-ink);}}
[data-testid="stWidgetLabel"] p, [data-testid="stExpander"] summary p {{
  color:#000;font-size:1rem;font-weight:650;line-height:1.6;
}}
[data-testid="stCaptionContainer"] p {{color:#202820;font-size:.95rem;line-height:1.65;font-weight:500;}}
[data-testid="stMarkdownContainer"] p {{line-height:1.65;}}
input, textarea, [data-baseweb="select"] {{color:#000;font-size:1rem;font-weight:500;}}
input::placeholder, textarea::placeholder {{color:#535b55;opacity:1;}}
button p {{font-weight:650;}}
button[kind="secondary"], button[kind="secondaryFormSubmit"] {{
  background:#f4f8f4;color:#111;border:1px solid #426f60;
}}
button[kind="primary"], button[kind="primaryFormSubmit"] {{
  background:#c62828;color:#fff;border:1px solid #c62828;
}}
button[kind="primary"] p, button[kind="primaryFormSubmit"] p {{color:#fff;}}
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover {{background:#a61e1e;border-color:#a61e1e;}}
button:disabled {{opacity:.55;}}
[data-baseweb="tab"][aria-selected="true"] {{color:var(--k-deep);font-weight:750;}}
[data-baseweb="tab-highlight"] {{background:var(--k-sage);}}
.k-brand {{display:flex;align-items:center;gap:.7rem;margin:.1rem 0 1.3rem;}}
.k-brand-mark {{display:grid;place-items:center;flex-shrink:0;width:38px;height:38px;
  border-radius:12px;background:#dce9dd;color:var(--k-deep);font-size:1.18rem;font-weight:800;}}
.k-brand-name {{font-weight:760;letter-spacing:-.035em;font-size:1.15rem;color:var(--k-ink);}}
.k-brand-sub {{font-size:.9rem;color:var(--k-muted);margin-top:1px;}}
.k-side-label {{font-size:.85rem;letter-spacing:.04em;text-transform:uppercase;
  color:var(--k-muted);font-weight:750;margin:1rem 0 .2rem;}}
.k-side-account {{border:1px solid #dfe8e1;border-radius:12px;padding:.8rem .9rem;}}
.k-side-account strong {{display:block;font-size:.9rem;color:var(--k-ink);overflow-wrap:anywhere;}}
.k-side-account small {{display:block;color:var(--k-muted);margin-top:.25rem;overflow-wrap:anywhere;}}
.k-hero {{position:relative;min-height:260px;border-radius:20px;overflow:hidden;
  background:linear-gradient(98deg,#e9f1e9 0%,#e9f1e9 37%,rgba(233,241,233,.96) 50%,rgba(233,241,233,.3) 100%),
    {image_css} right center/auto 100% no-repeat;
  border:1px solid #dce7dc;display:flex;align-items:center;padding:2.2rem 2.6rem;margin-bottom:1.5rem;}}
.k-hero-content {{position:relative;z-index:1;max-width:560px;}}
.k-eyebrow {{font-size:.76rem;letter-spacing:.1em;text-transform:uppercase;font-weight:800;
  color:var(--k-deep);margin-bottom:.65rem;}}
.k-hero h1 {{font-size:clamp(1.85rem,3vw,2.65rem);line-height:1.14;margin:0 0 .8rem;
  max-width:560px;color:var(--k-ink);font-weight:700;letter-spacing:-.035em;}}
.k-hero p {{font-size:.96rem;line-height:1.7;color:var(--k-muted);max-width:480px;margin:0;}}
.k-page-head {{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;margin:.4rem 0 1.4rem;}}
.k-page-head h1 {{font-size:1.95rem;line-height:1.2;margin:.25rem 0 .4rem;
  color:var(--k-ink);font-weight:700;letter-spacing:-.025em;}}
.k-page-head p {{color:var(--k-muted);margin:0;line-height:1.6;}}
.k-pill {{display:inline-block;border-radius:999px;background:#e4eee6;color:#315b45;
  padding:.3rem .7rem;font-size:.74rem;font-weight:700;}}
.k-section-title {{font-size:1.15rem;font-weight:700;letter-spacing:-.02em;
  color:var(--k-ink);margin:1.4rem 0 .25rem;}}
.k-section-sub {{font-size:1rem;color:var(--k-muted);margin:0 0 1rem;line-height:1.65;}}
.k-empty {{border:1px dashed #cbdacf;border-radius:14px;padding:2.25rem;text-align:center;}}
.k-empty-icon {{font-size:1.9rem;color:var(--k-sage);margin-bottom:.65rem;}}
.k-empty h3 {{margin:0 0 .5rem;color:var(--k-ink);}}
.k-empty p {{color:var(--k-muted);margin:0 auto;max-width:440px;line-height:1.65;}}
.k-login-title {{font-size:2.35rem;letter-spacing:-.045em;line-height:1.15;
  margin:.5rem 0 1rem;color:var(--k-ink);font-weight:700;}}
.k-login-copy {{color:var(--k-muted);line-height:1.75;margin-bottom:1.5rem;max-width:430px;}}
.k-login-scene {{border-radius:22px;min-height:430px;
  background:linear-gradient(120deg,rgba(237,244,237,.4),rgba(237,244,237,.05)),
    {image_css} center center/cover no-repeat;border:1px solid #dfe9df;}}
.k-login-foot {{font-size:.95rem;color:var(--k-muted);margin-top:1rem;line-height:1.65;}}
@media(max-width:900px) {{
 .k-hero {{min-height:230px;padding:1.5rem;
   background:linear-gradient(90deg,rgba(233,241,233,.98),rgba(233,241,233,.86)),{image_css} center/cover;}}
 .k-hero-content {{max-width:100%;}} .k-hero h1 {{font-size:1.8rem;}}
 .k-login-scene {{min-height:220px;}} .k-page-head h1 {{font-size:1.55rem;}}
}}
</style>
""", unsafe_allow_html=True)


def brand(subtitle: str = 'Εταιρικός χώρος') -> None:
    st.markdown(f'<div class="k-brand"><div class="k-brand-mark">⌂</div><div>'
                f'<div class="k-brand-name">Koinoxrista</div><div class="k-brand-sub">{_text(subtitle)}</div>'
                '</div></div>', unsafe_allow_html=True)


def hero(title: str, subtitle: str, eyebrow: str = 'Ο χώρος σας') -> None:
    st.markdown(f'<div class="k-hero"><div class="k-hero-content">'
                f'<div class="k-eyebrow">{_text(eyebrow)}</div><h1>{_text(title)}</h1>'
                f'<p>{_text(subtitle)}</p></div></div>', unsafe_allow_html=True)


def page_header(title: str, subtitle: str = '', eyebrow: str = '') -> None:
    tag = f'<span class="k-pill">{_text(eyebrow)}</span>' if eyebrow else ''
    st.markdown(f'<div class="k-page-head"><div>{tag}<h1>{_text(title)}</h1>'
                f'<p>{_text(subtitle)}</p></div></div>', unsafe_allow_html=True)


def section_title(title: str, subtitle: str = '') -> None:
    st.markdown(f'<div class="k-section-title">{_text(title)}</div>'
                f'<p class="k-section-sub">{_text(subtitle)}</p>', unsafe_allow_html=True)


def empty_state(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="k-empty"><div class="k-empty-icon">⌂</div>'
                f'<h3>{_text(title)}</h3><p>{_text(subtitle)}</p></div>', unsafe_allow_html=True)


def account(name: str, email: str = '') -> None:
    st.markdown(f'<div class="k-side-account"><strong>{_text(name)}</strong>'
                f'<small>{_text(email)}</small></div>', unsafe_allow_html=True)
