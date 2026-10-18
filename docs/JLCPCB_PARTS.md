# JLCPCB parts library

`easyeda2kicad` can search the JLCPCB assembly parts library, check stock and download datasheets. No login is needed.

```bash
easyeda2kicad --search BQ24073RGTR "100nF 0603"          # one table per keyword
easyeda2kicad --search 10k --package 0402 --basic-only   # 10k in 0402, no setup fee
easyeda2kicad --search RT9080 --min-stock 1 --sort stock # in stock, most stock first
easyeda2kicad --stock C15220 C841192                     # exact match by LCSC number
easyeda2kicad --datasheet C15220 --datasheet-dir ds      # saves ds/BQ24073RGTR_C15220.pdf
```

Commands can be combined, also with a library import (`--lcsc_id C15220 --full`).

## Search options

| Option | Effect |
|---|---|
| `--package PKG [PKG ...]` | only these packages; exact and case-sensitive (`0402`, `SOT-23-5`) |
| `--basic-only` | only Basic parts |
| `--min-stock N` | only parts with at least N in stock (`1` = in stock) |
| `--sort stock\|price` | most stock / cheapest first (default: relevance) |
| `--limit N` | up to N parts per keyword (default 10) |

Package, Basic, sort and "in stock" are filtered by JLCPCB, and `total` counts those matches. Placeholders and a `--min-stock` above 1 are filtered locally: further result pages are fetched to fill `--limit`, but at most 5 pages; the output then says "stopped after 5 pages" (JSON: `"truncated": true`).

Placeholder entries (`C9900…`, brand "JLCPCB Assembly", no stock) are always hidden; `--stock C9900…` still finds them. Search options without `--search` are rejected. `--stock` and `--datasheet` accept `C15220`, `c15220` or `15220`.

`--stock` searches JLCPCB for the code and accepts only an exact match among the first 50 results. There is no detail endpoint; in practice the exact code is ranked first.

Tips:

- Exact manufacturer part numbers give the best results.
- Keywords match loosely: `"10k 0402"` also finds 0603 parts. Use `--package` instead.
- Package names must match exactly: `SOT-23-5` does not include `TSOT-23-5`. Search without `--package` first to see the names, and give several if needed.
- `--sort price` also lists parts with very little stock; combine it with `--min-stock`.

## Output

```
### RT9080  [stock ≥ 1, sorted by stock]  (4 of 22 matches shown, raise --limit for more)
LCSC        TYPE      STOCK     PRICE     MPN                          BRAND            PACKAGE            DESCRIPTION
C426476     Ext       31216     $0.1014   RT9080-18GJ5                 Richtek Tech     TSOT-23-5          -40℃~+85℃@(Ta) 1 1.8V ...
```

- `TYPE`: `Basic` and `Ext-pref` (Preferred Extended) have no setup fee, `Ext` has a one-time fee per part type and order.
- `PRICE`: unit price of the first price break in USD.
- `--stock` shows `NOT LISTED AT JLCPCB` for unknown numbers; `--datasheet` prints `LCSC<TAB>file` or `LCSC<TAB>FAILED: reason`.

With `--json`, stdout contains exactly one JSON object with a key per command that was run. Messages go to stderr.

```json
{
  "search": [{"query": "RT9080", "status": "ok", "total": 22, "shown": 2, "truncated": false, "results": [{...}]}],
  "stock": [{"lcsc": "C15220", "status": "listed", ...}, {"lcsc": "C1", "status": "not_listed"}],
  "datasheet": [{"lcsc": "C15220", "status": "saved", "file": "ds/BQ24073RGTR_C15220.pdf"}]
}
```

`status` is `ok`/`listed`/`saved`, `not_listed`/`unavailable` (no datasheet link, no PDF, write error), or `error` for a JLCPCB/LCSC error (with an `error` message). A part has the fields `lcsc`, `model` (MPN), `brand`, `package`, `category`, `stock`, `type` (`Basic`/`Extended`), `preferred`, `setup_fee`, `placeholder`, `price`, `price_breaks`, `min_qty`, `reel_qty`, `description`, `attributes`, `datasheet` and `url`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok (also a search without matches) |
| 1 | a part is not listed, or a datasheet is unavailable (no link, no PDF, write error) |
| 2 | invalid arguments |
| 3 | JLCPCB/LCSC error: not reachable or unexpected response |

## Python API

```python
from easyeda2kicad.easyeda.easyeda_api import EasyedaApi

api = EasyedaApi()
api.search_jlcpcb_components("10k", packages=["0402"], part_type="base", sort="stock")
api.get_jlcpcb_component("C15220")  # part dict or None
api.download_datasheet("C15220", "ds")  # Path; raises JlcpcbError
```

Search, `--stock` and datasheet download use the anonymous JLCPCB endpoint `selectSmtComponentList`. The datasheet URL usually returns an LCSC viewer page; the PDF link inside it is followed and the file is saved only if it is a PDF.
