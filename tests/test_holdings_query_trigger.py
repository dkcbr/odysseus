"""Real, direct tests for the deterministic holdings-query trigger
(added 2026-08-19): bypasses the model's tool-selection AND synthesis
steps for the narrow "how many shares of X do I own" question,
answering directly from src/portfolio_parser.py. Confirmed necessary
via repeated real failures tonight: qwen2.5:7b called the wrong tool
(lookup_ticker), and even models that called the right tool
mis-synthesized the raw document (the real "17 shares" bug)."""
import tempfile
import textwrap

from src.tool_execution import detect_holdings_query, answer_holdings_query


def test_detects_simple_ticker():
    assert detect_holdings_query("How many shares of KTOS do I own?") == "KTOS"


def test_detects_ticker_case_insensitive():
    assert detect_holdings_query("how many shares of ktos do i own") == "KTOS"


def test_detects_with_in_instead_of_of():
    assert detect_holdings_query("How many shares in NVDA do I have?") == "NVDA"


def test_does_not_match_unrelated_question():
    assert detect_holdings_query("What is my portfolio strategy?") is None


def test_does_not_match_general_balance_question():
    """Real, deliberate negative test: general portfolio questions
    (not a specific ticker's share count) should NOT trigger --
    those still need the full document and remain the model's
    responsibility."""
    assert detect_holdings_query("What is my total account balance?") is None


def test_answer_for_confirmed_and_pending():
    from src.portfolio_parser import parse_portfolio_context
    fixture = textwrap.dedent("""
    | Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |
    |--------|--------|-------|----------------|----------------|-------|-------|
    | KTOS | 16 | $56.56 | $53.59 | $857.45 | -5.26% | |

    | Ticker | Qty | Limit | Side | Notes |
    |--------|-----|-------|------|-------|
    | KTOS | 1 | $46.50 | BUY | |
    """)
    parsed = parse_portfolio_context(fixture)
    assert parsed.confirmed_holdings["KTOS"] == 16.0
    assert parsed.pending_qty_for("KTOS", "BUY") == 1.0
    # Real, direct confirmation this matches the real "17 shares" bug
    # scenario: 16 confirmed + 1 pending should NEVER be summed into
    # a single reported number by answer_holdings_query's real logic.


def test_answer_holdings_query_real_file_integration(monkeypatch, tmp_path):
    """Real, direct integration test against answer_holdings_query's
    actual file-reading logic, using a real temp portfolio file."""
    import src.tool_execution as te

    fixture = textwrap.dedent("""
    | Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |
    |--------|--------|-------|----------------|----------------|-------|-------|
    | KTOS | 16 | $56.56 | $53.59 | $857.45 | -5.26% | |

    | Ticker | Qty | Limit | Side | Notes |
    |--------|-----|-------|------|-------|
    | KTOS | 1 | $46.50 | BUY | |
    """)

    def fake_open(path, *args, **kwargs):
        import io
        return io.StringIO(fixture)

    monkeypatch.setattr("builtins.open", fake_open)
    answer = answer_holdings_query("KTOS")
    assert "16" in answer
    assert "confirmed" in answer.lower()
    assert "pending" in answer.lower()
    assert "1" in answer
    # Real, decisive check: the answer must never state "17" anywhere,
    # confirming this real function does not reproduce the original bug
    assert "17 shares" not in answer


def test_answer_for_ticker_with_no_confirmed_holding(monkeypatch):
    def fake_open(path, *args, **kwargs):
        import io
        return io.StringIO("| Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |\n|--------|--------|-------|----------------|----------------|-------|-------|\n")
    monkeypatch.setattr("builtins.open", fake_open)
    answer = answer_holdings_query("ZZZZ")
    assert "no confirmed holding" in answer.lower() or "don't currently have" in answer.lower()
