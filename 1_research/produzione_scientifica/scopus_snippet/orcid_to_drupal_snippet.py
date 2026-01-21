#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ORCID -> OpenAlex -> snippet HTML per Drupal 7

Caratteristiche:
- Legge membri da orcid_members.csv (ORCID + nome + profilo filtro)
- Profili filtro in ./filters/<profile>.json (es. none.json, atmo_soft.json, atmo_strict.json)
- Dedup globale (DOI oppure titolo+anno)
- Split in 3 snippet separati (con hero image):
  - snippet_journals.html      (Articoli / journal)
  - snippet_conferences.html   (Conferenze, include EGU)
  - snippet_books.html         (Libri e capitoli di libri)
- Output di controllo:
  - excluded.html (solo lavori scartati dai filtri, utile per tarare keyword/soglie)

Requisiti:
- Python 3.10+
- certifi (per SSL su macOS)

requirements.txt:
  certifi==2025.8.3
"""

import unicodedata
import argparse
import csv
import html
import json
import os
import re
import ssl
import time
from typing import Dict, List, Tuple
from urllib.request import Request, urlopen

import certifi

ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


# -----------------------------
# HTTP / OpenAlex helpers
# -----------------------------

def http_get_json(url: str) -> dict:
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = Request(url, headers={"User-Agent": "LabSnippetGenerator/1.0"})
    with urlopen(req, timeout=30, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def openalex_works_by_orcid(orcid: str, per_page: int = 200) -> List[dict]:
    """
    Scarica TUTTI i works collegati all'ORCID via cursor pagination.
    """
    base = "https://api.openalex.org/works"
    cursor = "*"
    works: List[dict] = []

    while True:
        url = (
            f"{base}?filter=authorships.author.orcid:https://orcid.org/{orcid}"
            f"&per-page={per_page}&cursor={cursor}"
        )
        data = http_get_json(url)
        results = data.get("results", [])
        works.extend(results)

        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor or not results:
            break

        time.sleep(0.2)  # delay gentile

    return works


# -----------------------------
# Input: members + filters
# -----------------------------

def read_members_csv(path: str) -> List[Dict[str, str]]:
    """
    orcid_members.csv esempio:
    orcid,name,filter_profile
    0000-0002-1825-0097,Mario Rossi,atmo_strict
    0000-0001-5109-3700,Luisa Bianchi,none

    Supporta anche header: apply_filter (se nel tuo CSV usi quello)
    """
    members: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            orcid = (row.get("orcid") or "").strip()
            name = (row.get("name") or "").strip() or orcid

            # supporta sia filter_profile sia apply_filter
            profile = (row.get("filter_profile") or row.get("apply_filter") or "none").strip() or "none"

            if not ORCID_RE.match(orcid):
                raise SystemExit(f"ORCID non valido: {orcid!r} (name={name})")

            members.append({"orcid": orcid, "name": name, "profile": profile})

    if not members:
        raise SystemExit("Nessun membro trovato in orcid_members.csv")

    return members


def load_filter_profile(filters_dir: str, profile_name: str) -> dict:
    """
    Legge ./filters/<profile_name>.json
    """
    path = os.path.join(filters_dir, f"{profile_name}.json")
    if not os.path.exists(path):
        raise SystemExit(f"Profilo filtro non trovato: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def concept_id_by_name(name: str) -> str | None:
    """
    Risolve un concept OpenAlex partendo dal nome (best effort).
    """
    q = name.replace(" ", "%20")
    data = http_get_json(f"https://api.openalex.org/concepts?search={q}&per-page=5")
    results = data.get("results", [])
    if not results:
        return None
    return results[0].get("id")  # es. https://openalex.org/Cxxxxxx


def resolve_concept_ids(names: List[str]) -> set:
    ids = set()
    for n in names:
        cid = concept_id_by_name(n)
        if cid:
            ids.add(cid)
        else:
            print(f"[!] Concept non trovato: {n}")
        time.sleep(0.12)
    return ids


# -----------------------------
# Filtering logic (robusto su trattini/spazi)
# -----------------------------

DASHES = "\u2010\u2011\u2012\u2013\u2014\u2212\u2043\uFE58\uFE63\uFF0D"


def norm_text(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()

    for d in DASHES:
        s = s.replace(d, "-")

    s = s.replace("_", " ").replace("/", " ")

    out = []
    for ch in s:
        if ch.isalnum() or ch in (" ", "-"):
            out.append(ch)
        else:
            out.append(" ")
    s = "".join(out)

    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"-{2,}", "-", s)

    return s


def title_matches(title: str, keywords: List[str]) -> bool:
    t = norm_text(title)
    for k in keywords:
        if not k:
            continue
        kk = norm_text(k)
        if kk and kk in t:
            return True
    return False


def doi_norm(doi: str) -> str:
    d = (doi or "").strip().lower()
    d = d.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return d


def work_passes_filters(work: dict, flt: dict, include_concept_ids: set) -> bool:
    """
    mode == "none": include tutto
    mode default "include_if_any": include se (keyword titolo) OR (concept>=soglia), con esclusioni
    """
    mode = flt.get("mode", "include_if_any")
    if mode == "none":
        return True

    title = work.get("title") or ""
    doi = doi_norm(work.get("doi") or "")

    exclude_dois = set(str(x).lower() for x in flt.get("exclude_dois", []))
    if doi and doi in exclude_dois:
        return False

    if title_matches(title, flt.get("exclude_title_keywords", [])):
        return False

    if title_matches(title, flt.get("include_title_keywords", [])):
        return True

    min_score = float(flt.get("min_concept_score", 0.35))
    for c in (work.get("concepts") or []):
        cid = c.get("id")
        score = c.get("score", 0.0)
        if cid in include_concept_ids and score >= min_score:
            return True

    return False


# -----------------------------
# Venue / conference / EGU / books
# -----------------------------

def get_venue_info(work: dict) -> Tuple[str, str]:
    """
    OpenAlex: venue principale in primary_location.source.
    Ritorna: (display_name, source_type)
    source_type: journal / conference / repository / etc.
    """
    pl = work.get("primary_location") or {}
    src = pl.get("source") or {}
    src_name = src.get("display_name") or ""
    src_type = src.get("type") or ""
    return src_name, src_type


def is_conference(work: dict) -> bool:
    wtype = (work.get("type") or "").lower()
    venue, src_type = get_venue_info(work)
    src_type = (src_type or "").lower()

    if "proceedings" in wtype or "conference" in wtype:
        return True
    if src_type == "conference":
        return True

    v = (venue or "").lower()
    if "conference" in v or "proceedings" in v:
        return True

    return False


def is_egu(work: dict) -> bool:
    venue, _src_type = get_venue_info(work)
    v = (venue or "").lower()
    return ("egu" in v) or ("european geosciences union" in v) or ("general assembly" in v)


def is_book_or_chapter(work: dict) -> bool:
    """
    Identifica libri e capitoli.
    OpenAlex work.type spesso è:
    - book
    - book-chapter
    - edited-book (a volte)
    """
    wtype = (work.get("type") or "").lower()
    if wtype in ("book", "book-chapter", "edited-book"):
        return True

    # fallback: se il venue/source type è 'book-series' o simili (non sempre presente)
    venue, src_type = get_venue_info(work)
    st = (src_type or "").lower()
    if "book" in st:
        return True

    # ultimo fallback (debole): se nel venue compare "book" e non è una rivista
    v = (venue or "").lower()
    if "book" in v and "journal" not in v:
        return True

    return False


# -----------------------------
# Wrapper stile pagina (titolo + immagine + intro + lista)
# -----------------------------

def wrap_snippet(title: str, hero_img_url: str, intro_text: str, inner_html: str) -> str:
    return f"""
<div class="scheda-wrap">

  <h1 class="scheda-title">{html.escape(title)}</h1>

  <figure class="scheda-hero">
    <img src="{html.escape(hero_img_url)}" alt="{html.escape(title)}">
  </figure>

  <p class="scheda-intro">
    {intro_text}
  </p>

  <div class="scheda-body">
    {inner_html}
  </div>

</div>
""".strip()


# -----------------------------
# Citation + HTML list
# -----------------------------

def format_citation(work: dict) -> str:
    title = work.get("title") or ""
    year = work.get("publication_year") or ""
    doi = work.get("doi") or ""

    venue, _src_type = get_venue_info(work)

    authors = []
    for a in (work.get("authorships") or []):
        au = ((a.get("author") or {}).get("display_name"))
        if au:
            authors.append(au)

    authors_h = html.escape(", ".join(authors))
    title_h = html.escape(title)
    venue_h = html.escape(venue)

    doi_link = ""
    if doi:
        doi_clean = doi_norm(doi)
        doi_link = (
            f' <a href="https://doi.org/{html.escape(doi_clean)}" '
            f'target="_blank" rel="noopener">https://doi.org/{html.escape(doi_clean)}</a>'
        )

    parts = []
    if authors_h:
        parts.append(f"{authors_h} ({year})" if year else authors_h)
    elif year:
        parts.append(f"({year})")

    if title_h:
        parts.append(f"<strong>{title_h}</strong>")

    if venue_h:
        parts.append(venue_h)

    base = " ".join([p for p in parts if p])
    if base and not base.endswith("."):
        base += "."

    return base + doi_link


def dedup_works(works: List[dict]) -> List[dict]:
    seen = set()
    unique = []
    for w in works:
        title = (w.get("title") or "").strip()
        if not title:
            continue
        doi = doi_norm(w.get("doi") or "")
        year = str(w.get("publication_year") or "").strip()
        key = doi if doi else (title.lower() + "|" + year)
        if key in seen:
            continue
        seen.add(key)
        unique.append(w)
    return unique


def sort_works(works: List[dict]) -> List[dict]:
    def sort_key(w: dict):
        y = w.get("publication_year")
        y = y if isinstance(y, int) else -1
        t = (w.get("title") or "").lower()
        return (-y, t)

    works.sort(key=sort_key)
    return works


def build_list_html(works: List[dict], max_items: int = 120) -> str:
    works = dedup_works(works)
    works = sort_works(works)
    works = works[:max_items]

    css = """
<style>
/* Contenitore */
.scheda-wrap{max-width:980px;margin:0 auto;}
.scheda-title{margin:0 0 12px;font-size:28px;font-weight:700;line-height:1.1;}
.scheda-hero{margin:0 0 16px;}
.scheda-hero img{display:block;width:100%;height:auto;border-radius:3px;}
.scheda-intro{margin:0 0 22px;font-size:13px;line-height:1.6;color:#333;}
.scheda-body{font-size:13px;line-height:1.75;color:#333;}

/* Lista */
.art-list{margin:0;padding:0;}
.art-item{display:grid;grid-template-columns:56px 1fr;gap:16px;align-items:start;padding:10px 0;border-bottom:1px solid #eee;}
.art-num{width:44px;height:44px;border-radius:50%;background:#4a4a4a;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;line-height:1;}
.art-cit{font-size:13px;line-height:1.5;color:#333;}
.art-cit a{color:#1a73e8;text-decoration:none;}
.art-cit a:hover{text-decoration:underline;}

@media (max-width:700px){
  .art-item{grid-template-columns:46px 1fr;}
  .art-num{width:38px;height:38px;font-size:13px;}
}
</style>
""".strip()

    items = []
    start_num = len(works)
    for i, w in enumerate(works):
        num = start_num - i
        items.append(f"""
  <div class="art-item">
    <div class="art-num">{num}</div>
    <div class="art-txt">
      <div class="art-cit">{format_citation(w)}</div>
    </div>
  </div>
""".rstrip())

    return css + "\n\n" + '<div class="art-list">\n' + "\n".join(items) + "\n</div>\n"


# -----------------------------
# Main
# -----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--members", default="orcid_members.csv", help="CSV con orcid,name,filter_profile (o apply_filter)")
    ap.add_argument("--filters-dir", default="filters", help="Cartella con profili JSON")
    ap.add_argument("--max", type=int, default=120, help="Max items per snippet")

    ap.add_argument("--out-journals", default="snippet_journals.html")
    ap.add_argument("--out-conferences", default="snippet_conferences.html")
    ap.add_argument("--out-books", default="snippet_books.html")
    ap.add_argument("--excluded-out", default="excluded.html")

    ap.add_argument("--hero-journals", default="/sites/st02/files/pubblicazioni-big.jpg")
    ap.add_argument("--hero-conferences", default="/sites/st02/files/conferenze-big.jpg")
    ap.add_argument("--hero-books", default="/sites/st02/files/libri-bg.jpg")

    args = ap.parse_args()

    members = read_members_csv(args.members)

    profile_cache: Dict[str, Tuple[dict, set]] = {}

    included_all: List[dict] = []
    excluded_all: List[dict] = []

    for m in members:
        profile = m["profile"]

        if profile not in profile_cache:
            flt = load_filter_profile(args.filters_dir, profile)
            if flt.get("mode", "include_if_any") == "none":
                concept_ids = set()
            else:
                concept_ids = resolve_concept_ids(flt.get("include_concepts", []))
            profile_cache[profile] = (flt, concept_ids)

        flt, concept_ids = profile_cache[profile]

        print(f"[+] {m['name']} ({m['orcid']}) profilo={profile}")
        works = openalex_works_by_orcid(m["orcid"])
        print(f"    trovati: {len(works)}")

        inc = []
        exc = []
        for w in works:
            if work_passes_filters(w, flt, concept_ids):
                inc.append(w)
            else:
                exc.append(w)

        print(f"    inclusi={len(inc)} esclusi={len(exc)}")
        included_all.extend(inc)
        excluded_all.extend(exc)

    included_all = dedup_works(included_all)
    included_all = sort_works(included_all)

    journals: List[dict] = []
    conferences: List[dict] = []
    books: List[dict] = []

    for w in included_all:
        if is_book_or_chapter(w):
            books.append(w)
        elif is_conference(w) or is_egu(w):
            conferences.append(w)
        else:
            journals.append(w)

    # Journals
    journals_inner = build_list_html(journals, max_items=args.max)
    journals_full = wrap_snippet(
        title="Articoli",
        hero_img_url=args.hero_journals,
        intro_text="Articoli su riviste internazionali indicizzati su SCOPUS e/o WoS.",
        inner_html=journals_inner,
    )
    with open(args.out_journals, "w", encoding="utf-8") as f:
        f.write(journals_full)

    # Conferences (EGU inclusa)
    conf_inner = build_list_html(conferences, max_items=args.max)
    conf_full = wrap_snippet(
        title="Conferenze",
        hero_img_url=args.hero_conferences,
        intro_text="Lavori presentati a conferenze nazionali e internazionali (incluse EGU).",
        inner_html=conf_inner,
    )
    with open(args.out_conferences, "w", encoding="utf-8") as f:
        f.write(conf_full)

    # Books / chapters
    books_inner = build_list_html(books, max_items=args.max)
    books_full = wrap_snippet(
        title="Libri e capitoli di libri",
        hero_img_url=args.hero_books,
        intro_text="Libri, capitoli e contributi editoriali associati ai membri del laboratorio.",
        inner_html=books_inner,
    )
    with open(args.out_books, "w", encoding="utf-8") as f:
        f.write(books_full)

    # Excluded (solo lista)
    with open(args.excluded_out, "w", encoding="utf-8") as f:
        f.write(build_list_html(excluded_all, max_items=300))

    print("\nOK: generati file:")
    print(f" - {args.out_journals}      (Articoli + immagine)")
    print(f" - {args.out_conferences}  (Conferenze + immagine, include EGU)")
    print(f" - {args.out_books}        (Libri/capitoli + immagine)")
    print(f" - {args.excluded_out}     (scartati per taratura filtri)")


if __name__ == "__main__":
    main()
