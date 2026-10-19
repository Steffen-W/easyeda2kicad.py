"""Tests for the JLCPCB parts commands (--search/--stock/--datasheet) — no network required."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from easyeda2kicad.__main__ import main
from easyeda2kicad.easyeda.easyeda_api import (
    EasyedaApi,
    JlcpcbConnectionError,
    JlcpcbError,
    _is_placeholder,
    normalize_lcsc_id,
)
from easyeda2kicad.jlcpcb_cli import (
    MAX_SEARCH_PAGES,
    SearchFilters,
    download_datasheets,
    fee_type,
    run_parts,
    search_parts,
    stock_parts,
)


def _result(lcsc: str, **overrides: Any) -> dict[str, Any]:
    """One entry as returned by EasyedaApi.search_jlcpcb_components()['results']."""
    result: dict[str, Any] = {
        "lcsc": lcsc,
        "model": f"MPN-{lcsc}",
        "brand": "Brand",
        "package": "0402",
        "stock": 5000,
        "type": "Extended",
        "preferred": False,
        "setup_fee": True,
        "placeholder": False,
        "price": 0.01,
        "description": "test part",
        "datasheet": "https://www.lcsc.com/datasheet/x.pdf",
    }
    result.update(overrides)
    return result


class FakeApi(EasyedaApi):
    """EasyedaApi with canned, paged search results instead of HTTP requests."""

    def __init__(self, results: list[dict[str, Any]], error: str = "") -> None:
        super().__init__(use_cache=False)
        self.results = results
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def search_jlcpcb_components(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 10,
        part_type: str | None = None,
        packages: list[str] | None = None,
        in_stock: bool = False,
        sort: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "keyword": keyword,
                "page": page,
                "page_size": page_size,
                "part_type": part_type,
                "packages": packages,
                "in_stock": in_stock,
                "sort": sort,
            }
        )
        if self.error:
            return {"total": 0, "results": [], "error": self.error}
        start = (page - 1) * page_size
        return {
            "total": len(self.results),
            "results": self.results[start : start + page_size],
        }


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


class TestApiHelpers:
    def test_placeholder_detection(self) -> None:
        assert _is_placeholder({"componentCode": "C9900013966"})
        assert _is_placeholder(
            {"componentCode": "C123", "componentBrandEn": "JLCPCB Assembly"}
        )
        assert not _is_placeholder(
            {"componentCode": "C15220", "componentBrandEn": "Texas Instruments"}
        )

    def test_normalize_lcsc_id(self) -> None:
        assert normalize_lcsc_id(" c15220 ") == "C15220"
        assert normalize_lcsc_id("15220") == "C15220"
        assert normalize_lcsc_id("BQ24073") == "BQ24073"

    def test_get_component_requires_exact_match(self) -> None:
        api = FakeApi([_result("C152200"), _result("C15220")])
        for lcsc_id in ("c15220", "15220"):
            part = api.get_jlcpcb_component(lcsc_id)
            assert part is not None and part["lcsc"] == "C15220"
        assert api.get_jlcpcb_component("C1") is None

    def test_get_component_raises_when_jlcpcb_unreachable(self) -> None:
        with pytest.raises(JlcpcbConnectionError):
            FakeApi([], error="JLCPCB not reachable").get_jlcpcb_component("C1")

    def test_download_follows_lcsc_viewer_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = FakeApi([_result("C15220", model="BQ24073/RGTR")])
        viewer = (
            b"<html><script>window.top.location.href='"
            b"https://datasheet.lcsc.com/datasheet/pdf/abc123.pdf?productCode=C15220'"
            b"</script></html>"
        )
        pages = {
            "https://www.lcsc.com/datasheet/x.pdf": viewer,
            "https://datasheet.lcsc.com/datasheet/pdf/abc123.pdf": b"%PDF-1.4 data",
        }
        monkeypatch.setattr(api, "_http_get", lambda url, accept: pages[url])

        path = api.download_datasheet("C15220", tmp_path / "ds")

        assert path == tmp_path / "ds" / "BQ24073_RGTR_C15220.pdf"
        assert path.read_bytes() == b"%PDF-1.4 data"

    def test_download_without_pdf(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = FakeApi([_result("C15220")])
        monkeypatch.setattr(api, "_http_get", lambda url, accept: b"<html></html>")
        with pytest.raises(JlcpcbError, match="no PDF"):
            api.download_datasheet("C15220", tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_download_unknown_part(self, tmp_path: Path) -> None:
        with pytest.raises(JlcpcbError, match="not listed"):
            FakeApi([]).download_datasheet("C15220", tmp_path)

    def test_download_write_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = FakeApi([_result("C15220")])
        monkeypatch.setattr(api, "_http_get", lambda url, accept: b"%PDF-1.4 data")
        blocker = tmp_path / "file"
        blocker.write_text("not a folder")
        with pytest.raises(JlcpcbError, match="could not write"):
            api.download_datasheet("C15220", blocker / "ds")

    def test_http_get_broken_gzip_is_a_connection_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Response(io.BytesIO):
            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                pass

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout, context: Response(b"\x1f\x8b broken"),
        )
        with pytest.raises(JlcpcbConnectionError):
            FakeApi([])._http_get("https://example.com/x.pdf", "*/*")


# ---------------------------------------------------------------------------
# CLI functions
# ---------------------------------------------------------------------------


class TestSearchParts:
    def test_filters_placeholders_and_low_stock(self) -> None:
        api = FakeApi(
            [
                _result("C1", stock=50),
                _result("C2", stock=5000),
                _result("C9900001", placeholder=True),
            ]
        )
        report = search_parts(api, ["x"], filters=SearchFilters(min_stock=1000))
        assert [r["lcsc"] for r in report[0]["results"]] == ["C2"]
        assert report[0]["shown"] == 1 and report[0]["total"] == 3

    def test_server_side_filters_are_passed_to_api(self) -> None:
        api = FakeApi([])
        filters = SearchFilters(
            basic_only=True, packages=["0402"], min_stock=1, sort="price"
        )
        search_parts(api, ["10k"], limit=3, filters=filters)
        assert api.calls == [
            {
                "keyword": "10k",
                "page": 1,
                "page_size": 3,
                "part_type": "base",
                "packages": ["0402"],
                "in_stock": True,
                "sort": "price",
            }
        ]

    def test_fetches_more_pages_until_limit_after_filtering(self) -> None:
        api = FakeApi(
            [_result(f"C99000{i}", placeholder=True) for i in range(3)]
            + [_result(f"C{i}") for i in range(1, 6)]
        )
        report = search_parts(api, ["x"], limit=3)
        assert [r["lcsc"] for r in report[0]["results"]] == ["C1", "C2", "C3"]
        assert [call["page"] for call in api.calls] == [1, 2]

    def test_paging_stops_at_last_page(self) -> None:
        api = FakeApi([_result("C1", stock=5)] * 25)
        report = search_parts(
            api, ["x"], limit=10, filters=SearchFilters(min_stock=100)
        )
        assert report[0]["results"] == [] and report[0]["total"] == 25
        assert report[0]["truncated"] is False
        assert [call["page"] for call in api.calls] == [1, 2, 3]

    def test_page_limit_is_reported(self) -> None:
        api = FakeApi(
            [_result(f"C99000{i}", placeholder=True) for i in range(10)]
            + [_result("C1")]
        )
        out = io.StringIO()
        run_parts(api, search=["x"], limit=2, out=out)
        assert len(api.calls) == MAX_SEARCH_PAGES
        assert "(0 of 11 matches shown, stopped after 5 pages," in out.getvalue()
        assert search_parts(api, ["x"], limit=2)[0]["truncated"] is True

    def test_connection_error_is_reported(self) -> None:
        report = search_parts(FakeApi([], error="JLCPCB not reachable"), ["x"])
        assert report[0]["status"] == "error"
        assert report[0]["error"] == "JLCPCB not reachable"
        assert report[0]["results"] == []


class TestStockAndDatasheets:
    def test_stock_status(self) -> None:
        report = stock_parts(FakeApi([_result("C15220")]), ["c15220", "C1"])
        assert report[0]["status"] == "listed" and report[0]["lcsc"] == "C15220"
        assert report[1] == {"lcsc": "C1", "status": "not_listed"}

    def test_stock_connection_error(self) -> None:
        report = stock_parts(FakeApi([], error="JLCPCB not reachable"), ["C1"])
        assert report == [
            {"lcsc": "C1", "status": "error", "error": "JLCPCB not reachable"}
        ]

    def test_datasheet_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fetch(lcsc: str, out_dir: str) -> Path:
            if lcsc == "C2":
                raise JlcpcbError("no PDF found")
            if lcsc == "C3":
                raise JlcpcbConnectionError("LCSC not reachable")
            return Path(out_dir) / "a.pdf"

        api = FakeApi([])
        monkeypatch.setattr(api, "download_datasheet", fetch)
        report = download_datasheets(api, ["C1", "C2", "C3"], str(tmp_path))
        assert report[0]["status"] == "saved" and report[0]["file"].endswith("a.pdf")
        assert report[1] == {
            "lcsc": "C2",
            "status": "unavailable",
            "file": None,
            "error": "no PDF found",
        }
        assert report[2]["status"] == "error"


class TestRunParts:
    def test_one_json_object_for_several_commands(self) -> None:
        out = io.StringIO()
        code = run_parts(
            FakeApi([_result("C15220")]),
            search=["bq"],
            stock=["C15220"],
            as_json=True,
            out=out,
        )
        assert code == 0
        report = json.loads(out.getvalue())  # must be a single JSON document
        assert set(report) == {"search", "stock"}
        assert report["search"][0]["shown"] == 1
        assert report["stock"][0]["status"] == "listed"

    def test_exit_codes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def unavailable(lcsc: str, out_dir: str) -> Path:
            raise JlcpcbError("no PDF")

        api = FakeApi([])
        monkeypatch.setattr(api, "download_datasheet", unavailable)
        assert run_parts(api, search=["ghost"], out=io.StringIO()) == 0
        assert run_parts(api, stock=["C1"], out=io.StringIO()) == 1
        assert run_parts(api, datasheet=["C1"], out=io.StringIO()) == 1

        offline = FakeApi([], error="JLCPCB not reachable")
        assert run_parts(offline, search=["x"], out=io.StringIO()) == 3
        assert run_parts(offline, stock=["C1"], out=io.StringIO()) == 3

    def test_table_output(self) -> None:
        out = io.StringIO()
        run_parts(
            FakeApi([_result("C25744", type="Basic")]),
            search=["10k"],
            stock=["C1"],
            out=out,
        )
        text = out.getvalue()
        assert "### 10k  (1 of 1 matches shown)" in text
        assert "C25744" in text and "Basic" in text
        assert "NOT LISTED AT JLCPCB" in text
        assert text.rstrip().endswith("import into KiCad")

    def test_table_shows_filters_and_more_hint(self) -> None:
        out = io.StringIO()
        run_parts(
            FakeApi([_result(f"C{i}") for i in range(1, 6)]),
            search=["10k"],
            limit=2,
            filters=SearchFilters(packages=["0402"], basic_only=True),
            out=out,
        )
        assert (
            "### 10k  [package 0402, Basic only]  (2 of 5 matches shown,"
            " raise --limit for more)" in out.getvalue()
        )

    def test_no_results_hints(self) -> None:
        out = io.StringIO()
        run_parts(FakeApi([]), search=["ghost"], out=out)
        assert "### ghost  (no matches)" in out.getvalue()
        assert "Next:" not in out.getvalue()

        out = io.StringIO()
        run_parts(
            FakeApi([]),
            search=["ghost"],
            filters=SearchFilters(packages=["sot-23"]),
            out=out,
        )
        assert "case-sensitive" in out.getvalue()

    def test_connection_error_output(self) -> None:
        out = io.StringIO()
        run_parts(
            FakeApi([], error="JLCPCB not reachable: refused"),
            search=["x"],
            stock=["C1"],
            out=out,
        )
        text = out.getvalue()
        assert "ERROR: JLCPCB not reachable: refused" in text
        assert "CHECK FAILED" in text and "NOT LISTED" not in text


def test_fee_type() -> None:
    assert fee_type({"type": "Basic"}) == "Basic"
    assert fee_type({"type": "Extended", "preferred": True}) == "Ext-pref"
    assert fee_type({"type": "Extended"}) == "Ext"


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------


class TestMain:
    def test_search_json_keeps_stdout_clean(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            EasyedaApi,
            "search_jlcpcb_components",
            lambda self, keyword, **kwargs: {
                "total": 1,
                "results": [_result("C15220")],
            },
        )
        assert main(["--search", "BQ24073", "--json"]) == 0
        report = json.loads(capsys.readouterr().out)  # banner must be on stderr
        assert report["search"][0]["query"] == "BQ24073"
        assert report["search"][0]["results"][0]["lcsc"] == "C15220"

    def test_search_options_reach_the_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []

        def search(self: EasyedaApi, keyword: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"total": 0, "results": []}

        monkeypatch.setattr(EasyedaApi, "search_jlcpcb_components", search)
        argv = ["--search", "10k", "--package", "0402", "0603", "--basic-only"]
        assert main([*argv, "--min-stock", "1", "--sort", "stock", "--limit", "5"]) == 0
        assert calls[0] == {
            "page": 1,
            "page_size": 5,
            "part_type": "base",
            "packages": ["0402", "0603"],
            "in_stock": True,
            "sort": "stock",
        }

    def test_stock_without_lcsc_id_needs_no_action_flags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(EasyedaApi, "get_jlcpcb_component", lambda self, i: None)
        assert main(["--stock", "C1"]) == 1

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (["--search", "x", "--limit", "0"], "--limit: must be at least 1"),
            (["--search", "x", "--min-stock", "-5"], "--min-stock: must be at least 0"),
            (["--search", " "], "--search: empty keyword"),
            (["--stock", "BQ24073"], "not an LCSC number"),
            (["--stock", "C1", "--limit", "5"], "--limit only works together"),
            (["--stock", "C1", "--basic-only"], "--basic-only only works"),
            (["--stock", "C1", "--datasheet-dir", "ds"], "--datasheet-dir only"),
            (["--lcsc_id", "C1", "--full", "--json"], "--json only works"),
            (["--search", "x", "--sort", "cheap"], "invalid choice"),
        ],
    )
    def test_invalid_arguments_are_rejected(
        self, argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(argv) == 2
        assert message in capsys.readouterr().err

    def test_nothing_to_do_mentions_both_modes(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        assert main([]) == 1
        assert "--lcsc_id" in caplog.text and "--search" in caplog.text

    def test_help_lists_parts_examples(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--help"]) == 0
        help_text = capsys.readouterr().out
        assert "--search 10k --package 0402 --basic-only" in help_text
        assert "Exit codes: 0 ok" in help_text
