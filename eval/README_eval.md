# Eval set: `questions.jsonl` (36 questions, known answers)

Goal: measure hit@5 (and MRR) for BM25 vs hybrid retrieval over the Greek-law corpus.
I built it **without the search index**. Every gold document was found by reading the source
data directly (index.jsonl + `text/`, caselaw jsonl files, NSK OCR text). Each record's
`evidence` string was then checked by script: after collapsing whitespace, it is an exact
substring of the source text (36/36 pass). For the ΟλΑΠ group in q30 the phrase was checked in all 6 decisions.

## Record format
```
{"id","lang","question","has_citation",
 "gold":[{source, ..., "doc_id"}],   # any gold doc in the top-k counts as a hit
 "answer","evidence","path"}
```
- `fek_a`: `law_key` ("ν:4427/2016", "νδ:86/1969", "πδ:18/1989"), `article` (string), `fek_stem`, `doc_id` = `fekA:<stem>`.
  At chunk level a hit should ideally require `law`+`article` to match. At doc level, `doc_id` is enough.
- `areios_pagos`: `number`, `year`, `chamber`, `doc_id` = `ap:N/YYYY`.
- `nsk`: `number`, `year`, `doc_id` = `nsk:N/YYYY`.
- `elsyn`: `number` (aaApof without leading zeros), `year`, `formation`, `record_id` (the jsonl `id`), `doc_id` = `elsyn:<record_id>`.
- `ste`: `prefix` ("Α", Greek capital alpha), `number` (digits), `year`, `doc_id` = `ste:Α4690/2015`.
- `path`: a source file, or `<jsonl>#<id>` for records inside a jsonl file.

## Mix
| source | n | notes |
|---|---|---|
| ΦΕΚ Α΄ legislation | 15 | 3 pre-1975 OCR (νδ 3026/1954, νδ 3370/1955, νδ 86/1969), 2 more OCR pre-2000 (πδ 18/1989, ν 2690/1999), 10 from 2007-2021 |
| ΝΣΚ opinions | 9 | 7 answered by the subject + summary in `opinions.jsonl` (1990s-2019), 2 from OCR text (260/1969, 276/1974) |
| Άρειος Πάγος | 6 | civil + criminal; q30 has 6 acceptable gold docs (ΟλΑΠ 22, 23, 25, 26, 28, 29/2009, same holding) |
| Ελεγκτικό Συνέδριο | 4 | Ολομέλεια, Β΄ Τμήμα, 2× Κλιμάκιο Προληπτικού Ελέγχου VII |
| ΣτΕ | 2 | both 2015 (Ε΄ Τμήμα) |

- 8 English questions (q01, q03, q07, q08, q14, q16, q28, q36). The other 28 are in Greek.
- 2 questions carry an explicit citation (`has_citation: true`): q10 "άρθρο 5 του ν. 4427/2016" and q15 "άρθρο 46 του π.δ. 18/1989".
- The other questions are paraphrased the way a citizen or lawyer would ask. Title words are avoided where possible.
  Caselaw questions describe the fact pattern, not the decision number.

## Caveats
- **Single gold ≠ only right answer.** Other documents may also answer a question, for example a later amending law
  or another decision with the same holding. A miss can therefore be a false negative. Inspect misses before
  blaming the retriever. Known cases:
  - q04: ΚΔΔ 60 days is the *original* 1999 text. It was later cut to 50 days by ν. 3242/2004, which is not a gold doc.
  - q09: the probation rule also exists in later civil-service laws.
- **The ΚΔΔ article is nested.** For q04 the article is "4" of the Code, which is ratified inside "Άρθρο πρώτο" of
  ν. 2690/1999. A chunker that labels everything "άρθρο πρώτο" will fail article-level matching.
- **OCR quirks in the gold text (deliberately included):** polytonic leftovers and spaces inside words
  (1950s-60s), "χ" read for "κ" (πδ 18/1989: "αχυρώσεως"), dropped ι (ν 2690/1999), and two-column text interleaved
  line by line, even in some born-digital 2010-2016 PDFs. Some 2010 ΦΕΚ text uses ∆ (U+2206) and µ (U+00B5)
  instead of Δ/μ, so `fold()` should map them.
- **ΦΕΚ 1920 OCR is unusable** (ν. 2190/1920 is garbage), so there is no 1920s question. Also, `text/2010/20100100130.txt`
  (ν. 3869/2010) holds only ~12 KB, so it is not used here.
- **ΝΣΚ:** for opinions after about 1990 the answer is only in the `provisions` field. That field mixes the
  provisions list, the rapporteur and the summary (after "Κατάσταση : ..."), plus web-page junk. The caselaw
  loader must keep that summary text, or q16-q22 become unanswerable. NSK OCR covers only 1951-1974 so far.
- **AP texts are anonymised** ("...", initials). The Mati fire (q28) never names "Μάτι" in the text. The English
  question has to match on 102 dead / July 2018 / Attica fire, which is a good test for dense retrieval.
- **ΣτΕ corpus is mostly 1929-1942.** Only 51 decisions are from 2015, and both questions come from those.
- **AP `id`s** (`N_YYYY`) were checked to be unique across the whole file (0 duplicates).
- English questions over Greek-only text: BM25 should score ~0 on these unless it matches numbers or Latin
  tokens (EFKA, IKE). Report hit@5 split by `lang` as well as overall.
