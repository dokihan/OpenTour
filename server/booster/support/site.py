from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from booster.support import news, texts

ROOT = Path(__file__).resolve().parents[2]
NEWS_DIR = Path(__file__).resolve().parent / "news"
OPINIONS = ROOT / "data" / "opinions.jsonl"
GAME_AGENT = "unity-webview"
COOKIE = "opentour_player"

Reply = tuple[int, dict[str, str], bytes]

_LOCALE = re.compile(r"^/(?P<locale>[a-z]{2}-[A-Z]{2})(?:/(?P<page>.*))?$")
_PLAYER = re.compile(r"^\[?([0-9a-f]{16})\]?$")
_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
          ".svg": "image/svg+xml", ".webp": "image/webp"}
_BOX = re.compile(r"^(?:rarity_box_lotteries/[^/]+/box_contents"
                  r"|.*?(?:gift_box_contents|continuation_box_contents|quickstart_box_contents)/[^/]+)$")
_WEEKDAYS_FR = ("Lu", "Ma", "Me", "Je", "Ve", "Sa", "Di")
_BLOCK = re.compile(r"^\s*<(h[1-6]|hr|p|ul|ol|div|table)\b", re.I)
_PAGER = ('<div class="announcements-page-nation"><ui class="announcements-page-nation-items">'
          '<li><span class="announcements-page-nation-first link-disabled">first</span></li>'
          '<li><span class="announcements-page-nation-prev link-disabled">prev</span></li>'
          '<li><span class="announcements-page-nation-next link-disabled">next</span></li></ui></div>')

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Paris")
except Exception:
    _TZ = timezone(timedelta(hours=2))

def _signature(player_id: str) -> str:
    key = hashlib.sha256(b"opentour-webview-player:" + os.environ.get("INTERNAL_API_TOKEN", "").encode()).digest()
    return hmac.new(key, player_id.encode(), hashlib.sha256).hexdigest()[:32]

def player_from_cookie(cookie: str) -> str | None:
    if not cookie or not os.environ.get("INTERNAL_API_TOKEN"):
        return None
    try:
        morsel = SimpleCookie(cookie).get(COOKIE)
    except Exception:
        return None
    player_id, _, signature = (morsel.value if morsel else "").partition(".")
    if _PLAYER.match(player_id) and hmac.compare_digest(signature, _signature(player_id)):
        return player_id
    return None

def handle(method: str, path: str, query: str = "", raw: bytes = b"", accept_language: str = "",
           user_agent: str = "", players: dict | None = None, cookie: str = "") -> Reply | None:
    if path == "/support" or path.startswith("/support/"):
        path = path[len("/support"):] or "/"
    if path.startswith("/news/"):
        return _static(NEWS_DIR, path[len("/news/"):])
    is_root = path == "/" and method.upper() == "GET"
    m = _LOCALE.match(path)
    if path != "/term_chooser/faq" and not is_root and m is None:
        return None
    if GAME_AGENT not in user_agent.lower():
        print(f"    [support 403] {method} {path} ({user_agent[:60] or 'sans User-Agent'})")
        return 403, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}, b"Forbidden"
    if path == "/term_chooser/faq":
        return 302, {"Location": f"/support/{_locale_from(accept_language)}/zendesk_faqs#compte"}, b""
    if is_root:
        return 302, {"Location": f"/support/{_locale_from(accept_language)}/customer_support"}, b""
    locale, page = m.group("locale"), (m.group("page") or "").strip("/")
    if page.endswith("index.html"):
        page = page[:-len("index.html")].strip("/")
    params = dict(parse_qsl(query, keep_blank_values=True))
    reply = _html(_page(method.upper(), locale, page, params, raw, cookie))
    found = _PLAYER.match(params.get("PlayerID") or "")
    seen = found.group(1) if found else None
    if seen and players is not None and seen in players and os.environ.get("INTERNAL_API_TOKEN"):
        reply[1]["Set-Cookie"] = (f"{COOKIE}={seen}.{_signature(seen)}; Path=/support; Max-Age=31536000; "
                                  "HttpOnly; Secure; SameSite=Lax")
    return reply

def _page(method: str, locale: str, page: str, params: dict[str, str], raw: bytes, cookie: str) -> str:
    lang = news.lang_of(locale)
    L = texts.LABELS[lang]
    if page in ("", "customer_support", "abouts"):
        name = "abouts" if page == "abouts" else "customer_support"
        return _menu(locale, L[name], texts.menu(lang, name))
    if page in ("zendesk_faqs", "faq") or page.startswith("zendesk_faqs/"):
        return _article(locale, L["faq"], texts.page(lang, "faq", locale))
    if page in ("inquiries", "inquiries/new"):
        return _article(locale, L["contact"], texts.page(lang, "contact", locale))
    if page == "about_opentour":
        return _article(locale, L["about_opentour"], texts.page(lang, "about_opentour", locale))
    if page == "terms_of_service":
        return _doc(locale, L["terms"], f'<section class="page-terms-of-service">{texts.page(lang, "terms", locale)}</section>', header=False)
    if page in ("privacy_policy", "collection_and_use_of_personal_data"):
        return _doc(locale, L["privacy"], f'<section class="page-privacy-policy">{texts.page(lang, "privacy", locale)}</section>', header=False)
    if page.startswith("update_instructions"):
        title, body = texts.update_page(locale)
        return _doc(locale, title, f'<section class="page-update-instructions">{body}</section>')
    if page in ("health_attention", "license", "tips") or page.startswith("tips/"):
        is_tips = page.startswith("tips")
        title = L["tips"] if is_tips else L["health"] if page == "health_attention" else L["license"]
        return _notice(locale, title, lang, "tips" if is_tips else "not_found")
    if page == "opinions/new":
        return _opinion_form(locale, lang)
    if page == "opinions/confirm" and method == "POST":
        return _opinion_save(locale, lang, raw, cookie)
    if page == "public_announcements":
        return _news_list(locale, lang, params)
    if page.startswith("public_announcements/"):
        return _news_detail(locale, lang, page.split("/", 1)[1])
    if page == "private_announcements":
        return _doc(locale, L["notifications"],
                    f'<section class="page-announcements">{_tabs(locale, lang, params, "you")}'
                    f'<div class="announcements-no-message">{_e(L["no_message"])}</div>{_PAGER}</section>')
    if _BOX.match(page):
        return _notice(locale, L["box_contents"], lang, "box")
    if page == "wallets":
        return _notice(locale, L["wallets"], lang, "wallets")
    if page == "service_closed_menus":
        return _text_menu(locale, L["closed"], texts.notice(lang, "closed"),
                          [(f"/support/{locale}/{p}", label) for p, label in (("zendesk_faqs", L["faq"]),
                                                                             ("inquiries", L["contact"]))])
    if page in ("act_on_settlement", "specified_commercial_transactions_law"):
        return _notice(locale, L["unavailable"], lang, "payments")
    if page == "my_nintendo_missions":
        return _notice(locale, L["unavailable"], lang, "my_nintendo_missions")
    print(f"    [support ?] {method} /{locale}/{page}")
    return _article(locale, L["not_found"], f'<p class="text">{texts.notice(lang, "not_found")}</p><br>'
                                            f'<p class="text">{_a(locale, "customer_support", L["customer_support"])}</p>')

def _notice(locale: str, title: str, lang: str, name: str) -> str:
    return _article(locale, title, f'<p class="text">{texts.notice(lang, name)}</p>')

def _menu(locale: str, title: str, items: list[tuple[str, str]]) -> str:
    lis = "".join(f'<li><a class="customer-support-item" data-btn="" href="/support/{locale}/{p}">{_e(label)}</a></li>'
                  for p, label in items)
    return _doc(locale, title, f'<section class="page-customer-support"><ul class="customer-support-items">{lis}</ul></section>')

def _article(locale: str, title: str, body: str, header: bool = True) -> str:
    return _doc(locale, title, f'<section class="page-announcements-show {locale}">'
                               f'<div class="announcements-show-content">{body}</div></section>', header=header)

def _text_menu(locale: str, title: str, text: str, items: list[tuple[str, str]]) -> str:
    lis = "".join(f'<li><a class="temporary-menus-item" data-btn="" href="{_e(href)}">{_e(label)}</a></li>'
                  for href, label in items)
    return _doc(locale, title, f'<section class="page-temporary-menus"><div class="temporary-menus-text {locale}">'
                               f'<div style="font-weight:bold; text-align: center">{text}</div></div>'
                               f'<ul class="temporary-menus-items">{lis}</ul></section>')

def _tabs(locale: str, lang: str, params: dict[str, str], active: str) -> str:
    L = texts.LABELS[lang]
    q = _query(params, ("country", "market"))
    if active == "all":
        left = f'<li><snap class="js-announcements-tab-left announcements-tab-active-left">{_e(L["tab_all"])}</snap></li>'
        right = (f'<li class="js-announcements-tab-right"><a class="announcements-tab-right" '
                 f'href="/support/{locale}/private_announcements{q}">{_e(L["tab_you"])}</a></li>')
    else:
        left = (f'<li class="js-announcements-tab-left"><a class="announcements-tab-left" '
                f'href="/support/{locale}/public_announcements{q}">{_e(L["tab_all"])}</a></li>')
        right = f'<li class="js-announcements-tab-right"><span class="announcements-tab-active-right">{_e(L["tab_you"])}</span></li>'
    return f'<div class="announcements-tabs-wrap"><ul class="announcements-tabs">{left}{right}</ul></div>'

def _news_list(locale: str, lang: str, params: dict[str, str]) -> str:
    L = texts.LABELS[lang]
    q = _query(params, ("country",))
    items = []
    for n in news.STORE.visible():
        image = _web_banner(n, lang)
        img = f'<div class="announcements-item-image"><img src="{image}" /></div>' if image else ""
        items.append(
            f'<li class="announcements-item"><a href="/support/{locale}/public_announcements/{_e(n.key)}{q}">'
            f'<div class="announcements-item-date">{_date(n.opened_at, lang)}</div>'
            f'<div class="public-announcements-item-wrap">{img}'
            f'<div class="announcements-item-headings">{_e(n.text(n.title, lang))}</div>'
            f'<div class="announcements-item-more"><span class="announcements-item-more-btn">{_e(L["more"])}</span></div>'
            f'</div></a></li>')
    listing = (f'<ul class="announcements-items">{"".join(items)}</ul>' if items
               else f'<div class="announcements-no-message">{_e(L["no_news"])}</div>')
    return _doc(locale, L["notifications"],
                f'<section class="page-announcements">{_tabs(locale, lang, params, "all")}{listing}{_PAGER}</section>')

def _news_detail(locale: str, lang: str, key: str) -> str:
    L = texts.LABELS[lang]
    n = news.STORE.get(key)
    if n is None:
        body = f'<p class="text">{_e(L["shop_offer"] if key in news.BANNERS.keys() else L["news_gone"])}</p>'
    else:
        image = _web_banner(n, lang)
        body = (f'<img src="{_e(image)}" class="banner">' if image else "") + _paragraphs(n.text(n.body, lang))
    return _article(locale, L["notifications"], body, header=False)

def _paragraphs(paragraphs: list[str]) -> str:
    out, run = [], []
    for para in paragraphs:
        if _BLOCK.match(para):
            if run:
                out.append('<p class="text">' + "<br><br>".join(run) + "</p><br>")
                run = []
            out.append(para)
        else:
            run.append(para)
    if run:
        out.append('<p class="text">' + "<br><br>".join(run) + "</p>")
    return "".join(out)

def _web_banner(n: news.News, lang: str) -> str:
    name = n.text(n.web_banner, lang) if n.web_banner else ""
    if name.startswith("https://"):
        return name
    return f"/support/news/{name}" if name else ""

def _opinion_form(locale: str, lang: str) -> str:
    F = texts.OPINION_FORM[lang]
    options = "".join(f'<option value="{v}">{_e(label)}</option>' for v, label in texts.OPINION_TYPES[lang])
    form = (
        f'<section class="page-opinions-form"><div class="opinions-form-title"><p>{_e(F["intro"])}</p></div>'
        f'<form class="new_opinion" id="new_opinion" action="/support/{locale}/opinions/confirm" accept-charset="UTF-8" method="post">'
        f'<div class="opinions-form-box"><div class="field"><p class="c-title-h2">{_e(F["subject"])}</p>'
        f'<div class="opinions-form-select-1-wrap"><select class="opinions-form-select-1 required" required="required" '
        f'name="opinion[opinion_type_key]" id="opinion_opinion_type_key"><option value="">{_e(F["choose"])}</option>{options}</select></div></div>'
        f'<div class="field"><p class="c-title-h2">{_e(F["content"])}</p><div class="opinions-form-textarea-wrap">'
        f'<textarea class="opinions-form-textarea required" placeholder="{_e(F["placeholder"])}" required="required" maxlength="1000" '
        f'name="opinion[content]" id="opinion_content"></textarea></div><p class="opinions-form-sub">{_e(F["max"])}</p></div>'
        f'<div class="opinions-form-attention"><p>{_e(F["note"])}</p></div></div>'
        f'<div class="c-form-button-wrap"><input type="submit" value="{_e(F["send"])}" class="opinions-form-button-submit" data-btn="" style="margin:0 auto 4px" /></div>'
        f'</form></section>')
    return _doc(locale, texts.LABELS[lang]["feedback"], form)

def _opinion_save(locale: str, lang: str, raw: bytes, cookie: str) -> str:
    form = dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))
    kind = form.get("opinion[opinion_type_key]", "")
    content = form.get("opinion[content]", "").strip()[:1000]
    L = texts.LABELS[lang]
    if not kind or not content:
        return _article(locale, L["feedback"], f'<p class="text">{texts.notice(lang, "opinion_invalid")}</p><br>'
                                               f'<p class="text">{_a(locale, "opinions/new", L["feedback"])}</p>')
    OPINIONS.parent.mkdir(parents=True, exist_ok=True)
    with OPINIONS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": int(time.time()), "locale": locale, "type": kind, "content": content,
                             "player_id": player_from_cookie(cookie)}, ensure_ascii=False) + "\n")
    print(f"    [support] avis gardé dans {OPINIONS.name} ({kind}, {len(content)} caractères)")
    return _doc(locale, L["feedback"], f'<section class="page-opinions-form-complete"><div class="opinions-form-title">'
                                       f'<p class="section-title">{_e(L["thanks"])}</p>'
                                       f'<p class="section-text-01">{_e(texts.notice(lang, "opinion_saved"))}</p>'
                                       f'</div></section>')

def _doc(locale: str, title: str, content: str, *, header: bool = True) -> str:
    head = f'<header class="c-title-h1">{_e(title)}</header>' if header else ""
    body_class = "bg-android lang-jp" if locale == "ja-JP" else "bg-android lang-global"
    return ('<!DOCTYPE html><html><head><meta charset="utf-8" />'
            '<meta content="width=device-width,initial-scale=1.0,minimum-scale=1.0,maximum-scale=1.0,user-scalable=no" name="viewport" />'
            f'<title>{_e(title)}</title></head>'
            f'<body class="{body_class}" data-current-locale="{locale}">{head}{content}</body></html>')

def _date(epoch: int, lang: str) -> str:
    d = datetime.fromtimestamp(epoch, _TZ)
    if lang == "fr":
        return f"{_WEEKDAYS_FR[d.weekday()]} {d:%d/%m/%Y %H:%M}"
    hour = d.hour % 12 or 12
    return f"{d:%m/%d/%Y} ({d:%a}) {hour:02d}:{d:%M} {'a.m.' if d.hour < 12 else 'p.m.'}"

def _query(params: dict[str, str], keys: tuple[str, ...]) -> str:
    kept = {k: params[k] for k in keys if params.get(k)}
    return "?" + html.escape(urlencode(kept)) if kept else ""

def _locale_from(accept_language: str) -> str:
    return "fr-FR" if (accept_language or "fr").lower().startswith("fr") else "en-US"

def _a(locale: str, page: str, label: str) -> str:
    return f'<a href="/support/{locale}/{page}">{_e(label)}</a>'

def _e(text: str) -> str:
    return html.escape(text, quote=True)

def _html(doc: str) -> Reply:
    return 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache"}, doc.encode("utf-8")

def _static(root: Path, rel: str) -> Reply:
    path = (root / rel).resolve()
    if root.resolve() not in path.parents or not path.is_file() or path.suffix.lower() not in _TYPES:
        return 404, {"Content-Type": "text/plain"}, b"not found"
    return 200, {"Content-Type": _TYPES[path.suffix.lower()], "Cache-Control": "public, max-age=86400"}, path.read_bytes()
