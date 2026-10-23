# xref — Greek legal citation + amendment extraction

Module: `search/lib/xref.py` (pure stdlib, no models). Index-time use: run `extract()` on every chunk and
store `(doc_id, chunk seq, key, article, relation)` rows to build "law X amended by Y" / "article A of
law X cited by doc D".

## API
| call | returns |
|---|---|
| `extract(text, self_law=None, doc_year=None, include_self=False)` | list of `{kind, number, year, key, article, paragraph, raw, start, end, relation, fek, date}` — one dict per cited article (an act cited without an article gives `article=None`) |
| `law_key(kind, number, year)` / `law_key(kind, date="1953-06-03")` | `"ν:4412/2016"`, `"βδ:1953-06-03"`, `"κωδ:ΑΚ"`, `"Σ"` |
| `act_key(key)` | join key: `κν:2190/1920` → `ν:2190/1920` (same act) |
| `parse_nsk_provision("Ν 3463/2006Α146")` | `{kind, key, number, year, article:"146", paragraph, refs:[(art,par)…], date}` or None |
| `extract_nsk(provisions)` | citations for an NSK `provisions` list (compact form first, free-text fallback) |
| `resolve_fek(key)` | `[{label:"Α 147/2016", stem:"20160100147", date, title}]` |
| `build_tables()` | (re)builds `work/law_to_fek.json.gz` (6.8 MB; 66,927 numbered keys + 18,100 date-keyed issues) and `work/law_ranges.json` from `../index.jsonl` (15 s) |

* kinds: `ν κν πδ νδ αν βδ νπδ πνπ` (acts), `κωδ` (codes), `Σ` (Constitution), `eu`, `fek`.
* keys: numbered acts `kind:number/year`; date-identified acts `kind:YYYY-MM-DD` (signing date: "Β.Δ. της
  3/5.6.1953" → `βδ:1953-06-03`, "ΠΝΠ της 24.12.2012" → `πνπ:2012-12-24`, "το από 2.10.52 Β.Δ."); codes
  `κωδ:ΑΚ ΚΠολΔ ΠΚ ΚΠΔ ΚΔΔ ΚΔΔιαδ ΚΕΔΕ ΚΦΕ ΚΦΔ ΚΔΚ ΚΟΚ ΥΚ ΕισΝΑΚ ΕισΝΚΠολΔ ΕμπΝ ΕΣΔΑ`
  (NSK-only codes keep their own name, e.g. `κωδ:ΑγρΚ`); Constitution `Σ` (1975) / `Σ:1952`; EU
  `eu:dir:2014/24`, `eu:reg:2016/679` (always year/number, old "1408/71" regulations swapped),
  `eu:dec:…`, treaties `eu:ΣΛΕΕ`, `eu:ΣΕΕ`; standalone ΦΕΚ refs `fekA:2016/188`, `fekB:…`.
* `fek`: "του ν. 4412/2016 (Α΄ 147)" → the act carries `fek="fekA:2016/147"`; such a parenthesis is NOT
  emitted as a separate citation. Standalone "ΦΕΚ Α΄ 188/2016" is (only when a year/date is present).
* relation (decided per clause, quotes «…» excluded): `replaces` αντικαθίσταται/-ανται,
  `amends` τροποποιείται/-ούνται/Τροποποιούμε/αναριθμείται, `repeals` καταργείται/-ούνται,
  `adds` προστίθεται/-ενται/συμπληρώνεται/συμπληρούται/παρεμβάλλεται; heading nouns at a line/clause
  start ("Τροποποίηση του άρθρου 5 του ν. …", "- Προσθήκη παρ. 8 στο άρθρο 20 …", "Περί τροποποιήσεως
  …") give the same relation. Everything else is `cites`: quoted text, historical clauses ("όπως
  τροποποιήθηκε/αντικαταστάθηκε/ισχύει/κυρώθηκε/ως ετροποποιήθη/κωδικοποιήθηκε …" up to the next comma /
  paren / verb), instruments after the verb ("αντικαθίσταται από παραπομπή στο άρθρο 291 ΣΛΕΕ").
  OCR-tolerant verbs (θ→9/δ/0, κ→χ), line-end hyphens are joined before matching.
* self: `self_law` key → those citations are dropped (or `relation="self"` with `include_self=True`);
  "του παρόντος" never produces a citation.
* `doc_year`: pass the document's publication year; 2-digit years are expanded with per-year law-number
  ranges from the ΦΕΚ index ("ν. 3943/11" → 1911 or 2011 by number range, never after `doc_year`).
* `resolve_fek`: exact (kind, number, year); for continuously numbered acts (ν, κν, ν.δ., α.ν.) also
  year±1 (laws signed in late December / published in January, NSK typos like "Ν 5945/1932" = Α 388/1933);
  π.δ./β.δ. restart numbering every year so no ±1 there. Date keys → issues of that kind published 0–45
  days after the signing date (nearest first, ≤3). Loading the table costs ~1.4 s and ~200 MB RSS once
  per process. Coverage on 6,000 sample chunks: 99.0% of extracted act citations resolve (misses are
  source typos like "ν. 700/2020" for 4700/2020 and acts missing from the index).

## Patterns covered
`ν. 4412/2016`, `ν.4412/16`, `ν. 4412/ 2016`, `Ν 3463/2006`, `N.` (Latin), `του νόμου 4412/2016`,
`4412/2016 νόμου`, `του υπ' αριθ. 1430)1938 Α.Ν.`, OCR `)` for `/` ("Ν.Δ. 957)71"), `Ν.Δ.`, `ΝΔ`,
`ν.δ/τος`, `Ν.Δ. 276 από 10-1-1974`, `π.δ.`, `Π.Δ/τος`, `π.δ/γμα`, `π.δ/μα`, `α.ν.`, `Α.Ν.`, `ΑΝ.`,
`Αναγκ. Νόμου`, `β.δ.`, `Β.Δ. της 3/5.6.1953`, `Β.Δ. 20/5/1939`, `το από 24.4.1951 Β.Δ.`, `κ.ν.`,
`ν.π.δ.` (not `ν.π.δ.δ.`), `ΠΝΠ`, `της από 11.3.2020 Π.Ν.Π.`, lists "των α.ν. 1676/39 και 2452/40",
"π.δ. 166/2000 (και ήδη 169/2007)"; articles `άρθρο/άρθρου/άρθρα/άρθρων/άρθρον/άρθρ./αρθρ./αρ.`
(+ OCR `άρ9ρου`, `δρθρου`), ordinals ("άρθρο πρώτο", "Άρθρου Δευτέρου"), suffixes `5Α`, `4θ`, `23 Α΄`,
ranges "άρθρα 5 έως 8" → `5-8`, mixed lists "των άρθρων 2 παρ. 1 εδ.α', 3, 4 παρ.1, 6, …",
"της παρ. 2 του άρθρου 5", "άρθρου 37 παρ. 1 εδ. β΄", "(παραγρ. 1)", "8 επ.", chains "άρθρου 1 παρ. 2,
των άρθρων 3 και 4 του ν. …", "άρθρο 202 του κυρωθέντος με το άρθρο πρώτο του ν. 3463/2006 Κώδικα" →
art 202, "άρθρο 32 παρ. 6 του Κώδικα Νόμων για το Ελεγκτικό Συνέδριο (ν. 4129/2013" → art 32 of the
law, bare lists ", 20 παρ.1 ν. 1806/1988", "914, 922, 334 Α.Κ.", "(258 ΠΚ)", "ΚΠολΔ 559",
anaphora "του ίδιου/ιδίου/ανωτέρω/ως άνω νόμου|κώδικα|διατάγματος" (only with an article); codes by
abbreviation or full name (also OCR "Κόδικα"); Constitution "του Συντάγματος", "Σ.", "Συντ.";
EU "Οδηγίας 2014/24/ΕΕ", "της 92/13/ΕΚ", "Κανονισμού (ΕΕ) 2016/679", "(ΕΚ) αριθμ. 1101/2014",
"(ΕΟΚ) αριθ. 1408/71", "απόφαση 2008/615/ΔΕΥ"; ΦΕΚ "ΦΕΚ Α΄ 188/2016", "ΦΕΚ 114/Α/2006",
"ΦΕΚ 147 Α΄/…", "Φ.Ε.Κ. Α' 137/26.7.85". NSK compact provisions: `Ν 3463/2006Α146`, `ΠΔ 410/1995Α187Π2`,
`ΚΝ`, `ΝΔ 17-10-1923`, `ΠΔ 31-8/18-9-1935Α8`, `ΒΔ 10/20-11-1939Α51`, `ΠΝΠ 28/28-7/1978`, `ΑΚΑ1710`,
`ΚΠΟΛΔΑ467Π2`, `ΚΠΟΙΝΔΑ`, `ΠΟΙΝΚΑ`, `Σ1968Α14Π1`, `ΚανΕΕ/ΚανΕΟΚ n/y`, `Οδηγ yy/n/date`, `ΕΣΔΑ8`.

Guards (precision): acts need number+year (or a date); codes / Constitution / treaties need an article;
short abbreviations must be capitals in the original (ΑΚ, ΠΚ, bare `ΑΝ`); kind/year sanity from the
index (no β.δ. after 1975, no π.δ. in 1950, …); `Α.Β.Δ. 6471/2022` (case-book numbers) and other
dotted-abbreviation tails rejected; future dates "14.12.26" → 1926; 2-digit year at the chunk end
skipped; court decisions (ΑΠ, ΣτΕ, ΝΣΚ numbers), ministerial decisions (ΥΑ/ΚΥΑ) are not extracted.

## Measured quality (`tests/test_xref.py`, gold `tests/xref_gold.jsonl`)
Gold = 121 hand-labelled real snippets (204 labels), sampled with fixed seeds by
`tests/make_xref_samples.py` from ΦΕΚ OCR 1920–1999, ΦΕΚ text 2000–2026 (de-columned), Άρειος Πάγος,
Ελ.Συν., NSK provisions, plus a targeted tranche of hard patterns (old β.δ./ν.δ./α.ν./κ.ν., ΠΝΠ, EU,
katharevousa verbs, ΦΕΚ refs). Labels: `tests/_gold_labels.py` → `tests/_build_gold.py`. Set-based per
snippet: keys {key}, articles {(key, article)}, relations {(key, article, relation)}.

| split | snippets / labels | keys P / R | articles P / R | relations P / R |
|---|---|---|---|---|
| dev (iterated on) | 83 / 159 | 1.000 / 0.917 | 0.986 / 0.912 | 0.980 / 0.906 |
| holdout, frozen extractor (first blind run) | 38 / 45 | 0.970 / 0.821 | 0.881 / 0.822 | 0.881 / 0.822 |
| holdout after generic fixes (no longer blind) | 38 / 45 | 1.000 / 0.923 | 0.976 / 0.911 | 0.976 / 0.911 |
| all | 121 / 204 | 1.000 / 0.918 | 0.984 / 0.912 | 0.979 / 0.907 |

Targets (keys P ≥ 0.95, R ≥ 0.90) are met on the full set. The honest blind number is the frozen holdout
run (R 0.82): the fixes it prompted (π.δ/μα, ΣΛΕΕ key, "(και ήδη 169/2007)", overlapping "άρθρου 559
αρ. 10", "του Οργανισμού … (π.δ/μα 774/1980") are general, but expect real-corpus recall nearer 0.85–0.9
on OCR-heavy text. Extra eyeball audits of ~300 random extractions (modern text, OCR text, amendment
clauses) found no wrong keys after the last round; the remaining article ambiguity is lists like
"άρθρο 1 παρ. 1, 2 και 8" (8 read as an article).

## Speed
`nice -n 19`, 1 thread, ~1 kB chunks (Ελ.Συν. + ΦΕΚ 2010 + OCR 1965): **~2,000–2,150 chunks/s** (varies with the other build agents’ load)
(`venv/bin/python tests/test_xref.py --speed`), ~1 citation per chunk. Main tricks: one same-length
`str.translate` fold; anchors found by an all-literal prefix alternation (sre skips positions in C;
equivalence with the full regex is tested); clause/verb scans only behind cheap literal pre-checks.
Expect ~25 min for 3M chunks single-threaded.

## Known misses
* pdftotext `-layout` two-column interleaving and OCR column interleave split "του άρθρου 17 του / (other
  column) / Β.Δ. 40)1972" — the chunker should de-column; nothing in xref can fix it.
* heavy OCR damage: "1.Δ. 243/84" (Π→1), "Β.3" outside the "από DATE" form, "άροου", "Σεπτεμ6ρίου",
  "23)24.12,55".
* acts cited without a year ("ν. 3968", "νόμου 4879, ως ετροποποιήθη…") or by Greek-numeral numbers.
* code-name + law number ("ΚΝΝ 3459/2006"), plural keyword after a list ("125/1970 (ΦΕΚ …) Β.Δ/των").
* relations: only operative present-tense verbs + headings; "παρατείνεται / επαναφέρεται / αναστέλλεται"
  are `cites`; a verb cut by the chunk end is lost; repeals lists separated from their verb by page
  furniture are `cites`.
* ΦΕΚ refs without series or year ("ΦΕΚ 925/25-10-1991", "(ΦΕΚ 1242 Β΄)") are skipped.
