# src/deep_research.py
"""
IterResearch-style deep research engine.

Implements an iterative Think→Search→Extract→Synthesize loop where the LLM
drives every decision: what to search, what's relevant, what's missing, and
when to stop.  Inspired by Alibaba's IterResearch approach.
"""
import asyncio
import json
import logging
import re
from urllib.parse import urlparse
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set

from src.research_utils import strip_thinking, is_low_quality

from src.goal_based_extractor import EXTRACTOR_SYSTEM
from src.prompt_security import untrusted_context_message

logger = logging.getLogger(__name__)


def current_date_context() -> str:
    """Preamble that grounds query-generation/planning LLMs in the real current
    date. Without it the model falls back to its training-cutoff year and emits
    queries like "best Python tutorials 2025" when the year is actually 2026.
    System TZ-local so it matches what the user sees. Portable strftime only."""
    now = datetime.now().astimezone()
    return (
        f"Today's date is {now.strftime('%B %d, %Y')} ({now.strftime('%Y-%m-%d')}). "
        f"When a search query needs a year or refers to 'latest'/'current'/"
        f"'this year', use {now.strftime('%Y')} or relative wording — never a "
        f"year inferred from training data.\n\n"
    )

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
RESEARCH_PLAN_PROMPT = """\
You are a research strategist. Before searching, analyze this question and create a research plan.

**Question:** {question}

Break this question down:
1. What are the key sub-topics that need to be covered for a comprehensive answer?
2. What specific data points, facts, or perspectives should we look for?
3. What would a complete, high-quality answer include?

Return a JSON object with:
- "sub_questions": Array of 3-6 specific sub-questions to investigate
- "key_topics": Array of key topics/angles to cover
- "success_criteria": One sentence describing what a complete answer looks like

Example:
{{
  "sub_questions": ["What is the cost of living in X?", "How is the healthcare system?"],
  "key_topics": ["economy", "healthcare", "safety", "culture"],
  "success_criteria": "A balanced comparison covering cost, quality of life, and practical considerations."
}}
"""

QUERY_GEN_PROMPT = """\
You are a research assistant planning web searches.

**Original question:** {question}

**Research plan:**
{research_plan}

**What we know so far:**
{report}

**Round:** {round_num}

Generate {num_queries} focused search queries that will help answer the question.
{round_instruction}

Write each query as a short, concrete search-engine keyword phrase -- the way \
a person would actually type it into a search box (e.g. "LM Studio local LLM \
hardware requirements" or "cloud API LLM pricing 2026"), NOT a full \
grammatical question. Avoid leading words like "What", "How", "Why", or \
"Can you" -- extract the core concept and keywords instead.

Return ONLY a JSON array of query strings, nothing else.
Example: ["query one", "query two", "query three"]
"""

SYNTHESIZE_PROMPT = """\
You are updating an evolving research report.

**Original question:** {question}

**Current report:**
{report}

**New findings from this round:**
{new_findings}

Integrate the new findings into the existing report. Produce an updated, well-organized \
report that answers the original question as completely as possible given all evidence so far. \
Remove redundancy, resolve contradictions, and maintain logical flow.

CITATION REQUIREMENT: each finding above already has its own real citation tag, \
<cite id="N" url="...">title</cite> -- reuse that exact tag, character for character, \
right after any specific fact, claim, statistic, or quote you take from that finding. \
Do not invent a new id, do not write the literal placeholder id="N" -- copy the real \
numeric id and url shown next to that specific finding. A sentence stating a fact from \
a finding with no citation attached is incomplete -- add the tag, don't drop it, even \
for facts that also sound like general knowledge.

Write only the updated report — no preamble or meta-commentary.
"""

STOP_PROMPT = """\
You are deciding whether a research report is comprehensive enough.

**Original question:** {question}

**Current report:**
{report}

**Rounds completed:** {round_num} of {max_rounds}

Based on the report so far, do we have enough information to answer the question \
comprehensively?  Consider:
- Are the key aspects of the question addressed?
- Are there obvious gaps or unanswered sub-questions?
- Is the evidence sufficient and from multiple sources?

If rounds completed is well below the target, prefer continuing unless the \
report is already exhaustive.

Reply with ONLY "YES" or "NO" followed by a brief one-sentence reason.
Example: "YES — The report covers all major aspects with evidence from multiple sources."
Example: "NO — We still lack information about the economic impact."
"""

FINAL_REPORT_PROMPT = """\
Write a research report answering this question:

**Question:** {question}

**All collected evidence and analysis:**
{report}

LENGTH: let the evidence above set the length, not a fixed target. If it's rich and \
detailed, a long 1500+ word magazine-quality article is appropriate. If it's thin -- only \
a few findings, or narrow in scope -- a shorter, honest report is correct. A concise report \
that sticks to what the evidence actually supports is better than a long one padded with \
invented detail to hit a word count.

ANTI-FABRICATION, non-negotiable: every claim, criterion, statistic, or section in this \
report must be directly supported by the evidence above. Do not introduce a topic, \
comparison criterion, or section (e.g. hardware, performance, pricing) that the evidence \
above does not actually discuss, even if it would make the report feel more complete. If \
the evidence doesn't cover something, leave it out entirely rather than filling the gap \
with plausible-sounding invented content.

CITATION REQUIREMENT, non-negotiable: the evidence above already contains each source's \
citation as a <cite id="N" url="...">title</cite> tag. Every specific fact, statistic, or claim \
you state in the report must keep its inline citation, right where the fact appears -- not \
collected in a references list at the end. When you cite a fact, reproduce that finding's exact \
<cite id="N" url="...">title</cite> tag verbatim, character for character -- do not rewrite it \
into [title](url) markdown, do not shorten it, do not paraphrase the title inside it. Treat the \
tag as a literal, atomic unit, like a citation you are copying, not text you are composing. A \
sentence stating a specific fact with no citation attached is a defect in the report, not an \
acceptable simplification. If the evidence above has no citation for something, don't state it \
as a sourced fact.

EVIDENCE WEIGHTING: each <cite> tag also has a domain_count attribute -- how many findings \
above came from that same real source domain. A higher domain_count means multiple, independent \
findings from that source corroborate the same information; domain_count="1" means only one \
finding came from that domain. Use this only as a real, mechanical signal for how much weight to \
give a claim -- domain_count does not measure whether a source is trustworthy or authoritative, \
only how often the same domain recurred in this specific research run. Never use domain_count to \
justify omitting a fact, and never state a claim more strongly than the evidence itself supports, \
regardless of domain_count. domain_count is metadata for YOUR OWN reasoning only -- it must never \
appear as visible text anywhere in the report itself (not as "(domain_count="2")", not as a \
footnote, not in a heading). If you keep a <cite> tag verbatim as instructed above, that tag's \
own domain_count attribute is fine to carry along inside the tag -- the reader never sees raw \
tag attributes, only the rendered citation. Writing domain_count out in your own prose is always \
wrong.

Do not add a separate "References", "Sources", or "Citations" section at the end of the report. \
Every citation belongs inline, as its <cite> tag, right where the fact it supports appears.

Other requirements:
- Use clear ## headings and ### subheadings to organize into logical sections
- Where the evidence supports it, use multiple detailed paragraphs, not just bullet points
- Synthesize and analyze the information — explain WHY things matter, draw
  comparisons, provide context, but only for what the evidence actually shows
- Include specific data points, numbers, and statistics from the evidence
- Note where sources agree and where they disagree
- Add a brief executive summary at the top
- End with a clear conclusion that directly answers the question, scoped to
  what the evidence actually supports
- Write in an engaging, informative style — not dry or robotic
"""

CATEGORY_PROMPTS = {
    "product": """IMPORTANT FORMAT OVERRIDE — this is a PRODUCT research report:
- Structure as a RANKED LIST of products/options (best first)
- For EACH product include: name as ### heading, approximate price, 2-3 sentence summary, **Pros:** bullet list, **Cons:** bullet list, **Where to buy:** URLs as links
- Start with a quick-compare markdown table of top picks (columns: Name, Price, Best For, Rating)
- End with a ## Verdict section picking Best Overall and Best Value
- REMINDER: every fact above had a source link next to it -- keep those
  [title](url) citations inline in this report, not just in the table""",

    "comparison": """IMPORTANT FORMAT OVERRIDE — this is a COMPARISON report:
- Create a ## Comparison Table as a markdown table comparing ALL options across key criteria (rows = criteria, columns = options)
- Use checkmarks, ratings, or short values in cells
- Write a ## section per option with its strengths, weaknesses, and ideal use case
- End with ## Best For verdicts (e.g., "**Best for small teams:** Option A because...")
- Include a ## Shared Considerations section for things that apply to all options
- REMINDER: every fact above had a source link next to it -- keep those
  [title](url) citations inline as you write each strength/weakness, not
  just in the table
- Real, added 2026-09-11 (evidence weighting, comparison tables): each
  citation's domain_count is a real, mechanical corroboration signal --
  how many findings above came from that same source domain. When
  ordering rows in the Comparison Table, criteria supported by a higher
  domain_count may be placed earlier or given more weight in the
  narrative; criteria supported by only domain_count="1" may be placed
  later. This is guidance only, not a rule: never invent a criterion to
  "balance" domain counts, never infer a detail no finding actually
  states, and never omit a criterion the evidence genuinely supports
  just because its domain_count is low.
- Real, added 2026-09-11 (criteria grouping): you may group related
  comparison-table criteria into thematic sections (e.g. Capabilities,
  Limitations, Operational Constraints, Performance, Cost Factors) --
  but only when multiple criteria naturally cluster based on what the
  evidence above actually discusses. domain_count may influence which
  criteria appear earlier within a group, but never decides whether
  something IS a group -- that comes only from the real evidence
  clustering that way on its own. Do not invent a group the evidence
  doesn't support, and do not fabricate a criterion just to fill one
  out. If the evidence doesn't naturally cluster, produce a flat,
  ungrouped table instead -- that is the correct, expected outcome for
  evidence that doesn't group cleanly, not a fallback to avoid.
- Real, added 2026-09-11 (conflict surfacing): if two or more findings
  above genuinely, explicitly disagree about the same criterion (e.g.
  one states a capability the other states as a limitation, or they
  give different specific values for the same measurement), you may
  mark that row as a conflict -- for example, "Connectivity
  (Conflicting evidence)" -- and show what each side actually says,
  rather than silently picking one or blending them into an averaged
  or vague answer. domain_count may be noted alongside each side (e.g.
  "supported by 2 domains" vs "1 domain") as corroboration context
  only, never to decide which side is "correct" or to discard the
  weaker side. Only surface a conflict that is explicitly present in
  the evidence's own wording -- never infer disagreement the evidence
  doesn't actually state, and never manufacture a conflict to make the
  table feel more thorough. If the evidence doesn't disagree with
  itself anywhere, produce a normal table with no conflict rows at
  all -- that is the correct, expected, common outcome.""",

    "howto": """IMPORTANT FORMAT OVERRIDE — this is a HOW-TO guide:
- Start with ## Quick Guide — a super concise numbered list (one line per step, no details, just the action). Example: 1. Install X  2. Run Y  3. Configure Z
- Then ## Prerequisites listing what's needed before starting
- Then the detailed steps: ## Step 1: ..., ## Step 2: ...
- Each step should have a clear heading and detailed instructions
- Use blockquotes (> ) for tips and warnings: > **Tip:** ... or > **Warning:** ...
- End with ## Common Mistakes section
- Add estimated time and difficulty level near the top
- REMINDER: every fact above had a source link next to it -- keep those
  [title](url) citations inline in the detailed steps""",

    "factcheck": """IMPORTANT FORMAT OVERRIDE — this is a FACT-CHECK report:
- Start with ## The Claim restating what's being checked
- Create ## Evidence For and ## Evidence Against sections
- Each piece of evidence should be a ### with source name, what it found, and how strong the evidence is
- Include a ## Verdict section with one of: **Supported**, **Mixed Evidence**, or **Unsupported**
- End with ## Nuance & Caveats for important context and limitations
- Be balanced and cite sources for every claim -- keep the [title](url)
  links from the evidence above inline, not just named in prose""",
}

# ---------------------------------------------------------------------------
# DeepResearcher
# ---------------------------------------------------------------------------
class DeepResearcher:
    """
    Iterative research engine following the IterResearch pattern.

    Each round: LLM generates queries → SearXNG search → LLM extracts from
    top pages → LLM synthesizes into evolving report → LLM decides continue/stop.
    """

    def __init__(
        self,
        llm_endpoint: str,
        llm_model: str,
        llm_headers: Optional[Dict] = None,
        max_rounds: int = 8,
        max_time: int = 300,
        max_urls_per_round: int = 3,
        max_content_chars: int = 15000,
        max_report_tokens: int = 8192,
        extraction_timeout: int = 90,
        planning_timeout: int = 90,
        query_timeout: int = 120,
        extraction_concurrency: int = 3,
        min_rounds: int = 2,
        max_empty_rounds: int = 2,
        synthesis_window: int = 10,
        progress_callback: Optional[Callable] = None,
        search_provider: Optional[str] = None,
        category: Optional[str] = None,
    ):
        self.llm_endpoint = llm_endpoint
        self.llm_model = llm_model
        self.llm_headers = llm_headers
        self.search_provider_override = search_provider
        self.category = category
        self.max_rounds = max_rounds
        self.max_time = max_time
        self.max_urls_per_round = max_urls_per_round
        self.max_content_chars = max_content_chars
        self.max_report_tokens = max_report_tokens
        self.extraction_timeout = min(3600, max(15, int(extraction_timeout or 90)))
        self.planning_timeout = min(3600, max(15, int(planning_timeout or 90)))
        self.query_timeout = min(3600, max(15, int(query_timeout or 120)))
        self.extraction_concurrency = min(12, max(1, int(extraction_concurrency or 3)))
        self.min_rounds = min_rounds
        self.max_empty_rounds = max_empty_rounds
        self.synthesis_window = synthesis_window
        self._progress = progress_callback
        self._cancelled = False
        self._start_time: float = 0
        self.queries_used: Set[str] = set()
        self.urls_fetched: Set[str] = set()
        self.analyzed_urls: List[Dict[str, str]] = []
        self.round_count: int = 0
        # Track which search providers actually returned results during the
        # run, in arrival order — surfaced in the visual report so users can
        # see whether searxng / brave / tavily etc. carried the work.
        self.providers_used: List[str] = []
        self.findings: List[Dict] = []
        self.evolving_report: str = ""
        self.research_plan: str = ""
        # Populated by _validate_citations() -- URLs the final report cited
        # that were never actually fetched during this run. Real, live-caught
        # failure mode: the model can fabricate a plausible-looking citation
        # for a fact it invented, apparently to fill category-template
        # sections the real evidence didn't cover.
        self.fabricated_citations: List[str] = []

    def cancel(self):
        """Request cooperative cancellation of the research loop."""
        self._cancelled = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def research(
        self,
        question: str,
        prior_report: str = "",
        prior_findings: Optional[List[Dict]] = None,
        prior_urls: Optional[Set[str]] = None,
    ) -> str:
        """Run iterative research and return a final report.

        Args:
            question: The research question.
            prior_report: Previous report to continue from (for follow-up research).
            prior_findings: Previous findings to build on.
            prior_urls: URLs already visited (won't be re-fetched).
        """
        self._start_time = time.time()
        findings: List[Dict] = list(prior_findings) if prior_findings else []
        report = prior_report or ""

        # PLAN: Analyze the question and create a research strategy
        if not prior_report:
            self._emit(phase="planning")
            self.research_plan = await self._create_plan(question)
            logger.info(f"Research plan: {self.research_plan[:200]}")
        else:
            # Continuation — plan around the follow-up
            self._emit(phase="planning")
            self.research_plan = await self._create_plan(question)
            logger.info(f"Continuation plan: {self.research_plan[:200]}")
        if not self.category and not prior_report:
            self.category = await self._classify_category(question)
            if self.category:
                logger.info(f"Auto-detected category: {self.category}")

        if prior_urls:
            self.urls_fetched.update(prior_urls)
        self.findings = findings  # expose for handler
        consecutive_empty_rounds = 0

        for round_num in range(1, self.max_rounds + 1):
            self.round_count = round_num
            if self._cancelled:
                logger.info(f"Research cancelled after {round_num - 1} rounds")
                break
            if self._time_exceeded():
                logger.info(f"Time limit reached after {round_num - 1} rounds")
                break

            logger.info(f"=== Research Round {round_num} ===")
            self._emit(phase="searching", round=round_num, total_sources=len(self.urls_fetched))

            # THINK: generate queries
            queries = await self._generate_queries(question, report, round_num)
            if not queries:
                logger.warning(f"Round {round_num}: no queries generated, stopping")
                break

            self._emit(phase="searching", round=round_num, queries=len(queries),
                       query_preview=queries[0] if queries else "",
                       total_sources=len(self.urls_fetched))

            # SEARCH + EXTRACT
            round_findings = await self._search_and_extract(queries, question)
            if round_findings:
                findings.extend(round_findings)
                consecutive_empty_rounds = 0
                logger.info(f"Round {round_num}: extracted {len(round_findings)} findings")
                self._emit(phase="reading", round=round_num,
                           new_sources=len(round_findings),
                           total_sources=len(self.urls_fetched),
                           total_findings=len(findings))
            else:
                consecutive_empty_rounds += 1
                logger.info(f"Round {round_num}: no new findings ({consecutive_empty_rounds} consecutive empty)")
                if consecutive_empty_rounds >= self.max_empty_rounds:
                    logger.warning(f"Search appears to be down — {self.max_empty_rounds} consecutive rounds with no results")
                    err_detail = getattr(self, '_last_search_error', 'unknown error')
                    self._emit(phase="error", message=f"Search engine unavailable: {err_detail}")
                    if not findings:
                        return (
                            f"**Search unavailable** — Web search failed after "
                            f"{round_num} rounds. Error: {err_detail}\n\n"
                            "Please check your search provider settings and ensure the service is running."
                        )
                    break

            # SYNTHESIZE
            if findings:
                self._emit(phase="analyzing", round=round_num,
                           total_sources=len(self.urls_fetched),
                           total_findings=len(findings))
                report = await self._synthesize(question, findings, report)

            # DECIDE
            if round_num >= self.min_rounds:
                should_stop = await self._should_stop(question, report, round_num)
                if should_stop:
                    logger.info(f"LLM decided to stop after round {round_num}")
                    break

        # FINAL REPORT
        self._emit(phase="writing", total_sources=len(self.urls_fetched),
                   total_findings=len(findings))
        if not report:
            # Synthesis can fail (e.g. the LLM timed out) even though the search
            # rounds did gather findings. Don't throw that work away — return the
            # gathered findings as a basic compiled report instead of claiming
            # nothing was found (#1551).
            if findings:
                logger.warning(
                    "Synthesis produced no report; returning %d gathered "
                    "finding(s) as a fallback", len(findings)
                )
                return self._fallback_report(question, findings)
            return "No information could be gathered for this question."

        self.evolving_report = report  # preserve pre-synthesis report
        final = await self._final_report(question, report)
        # Real, added 2026-09-11 (citation anchoring): reconcile against the
        # pre-rewrite `report` (which still has every real <cite> tag from
        # _format_findings) before stripping tags to plain markdown, since
        # reconciliation needs the tags to compare against.
        final = await self._reconcile_citations(question, report, final)
        # Real, added 2026-09-11 (evidence completeness, transparency-only):
        # runs on the tagged text, before stripping, so the real <cite id>
        # tags are still present to compare against. No retry, no pressure
        # to use more findings -- see _find_unused_citations's own comment
        # for why forcing this would be unsafe here specifically.
        unused_ids = self._find_unused_citations(report, final)
        final += self._format_unused_sources_section(unused_ids)
        final = self._strip_cite_tags(final)
        final = self._strip_domain_count_leaks(final)
        final = self._validate_citations(final)
        elapsed = time.time() - self._start_time
        logger.info(
            f"Research complete: {self.round_count} rounds, "
            f"{len(findings)} findings, {len(self.urls_fetched)} URLs, "
            f"{elapsed:.1f}s"
        )
        return final

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------
    async def _llm(self, messages: List[Dict], temperature: float = 0.3,
                   max_tokens: int = 4096, timeout: int = 60) -> str:
        """Call the LLM asynchronously and strip thinking tags."""
        from src.llm_core import llm_call_async
        response = await llm_call_async(
            url=self.llm_endpoint,
            model=self.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            headers=self.llm_headers,
            timeout=timeout,
        )
        return strip_thinking(response)

    # ------------------------------------------------------------------
    # PLAN: create research strategy
    # ------------------------------------------------------------------
    async def _create_plan(self, question: str) -> str:
        """LLM analyzes the question and creates a research plan."""
        prompt = current_date_context() + RESEARCH_PLAN_PROMPT.format(question=question)
        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
                timeout=getattr(self, "planning_timeout", 90),
            )
            # Try to parse as JSON for structured plan
            parsed = self._parse_json_object(response)
            if parsed:
                parts = []
                if parsed.get("sub_questions"):
                    parts.append("Sub-questions: " + "; ".join(parsed["sub_questions"]))
                if parsed.get("key_topics"):
                    parts.append("Key topics: " + ", ".join(parsed["key_topics"]))
                if parsed.get("success_criteria"):
                    parts.append("Success: " + parsed["success_criteria"])
                return "\n".join(parts) if parts else response
            return response
        except Exception as e:
            logger.warning(f"Research planning failed: {e}")
            self._emit(phase="warning", message="Planning step failed, proceeding with direct search")
            return ""

    async def _classify_category(self, question: str) -> Optional[str]:
        """Fast LLM call to classify the research question into a category."""
        valid = ", ".join(CATEGORY_PROMPTS.keys())
        prompt = (
            f"Classify this research question into exactly ONE category.\n"
            f"Categories: {valid}\n"
            f"If none fit well, respond with: general\n\n"
            f"Question: {question}\n\n"
            f"Respond with ONLY the category name, nothing else."
        )
        try:
            result = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0, max_tokens=20, timeout=15,
            )
            cat = (result or "").strip().lower()
            # Clean one-word answer first.
            parts = cat.split()
            first = parts[0].strip(".,\"'*:") if parts else ""
            if first in CATEGORY_PROMPTS:
                return first
            # Weak local models often wrap the label in preamble ("the category
            # is product") — scan the whole reply for any known category word
            # before giving up (which would default to the generic format).
            for c in CATEGORY_PROMPTS:
                if c in cat:
                    return c
            return None
        except Exception as e:
            logger.warning(f"Category classification failed: {e}")
            return None

    # ------------------------------------------------------------------
    # THINK: generate search queries
    # ------------------------------------------------------------------
    async def _generate_queries(self, question: str, report: str,
                                round_num: int) -> List[str]:
        if round_num == 1:
            num_queries = 4
            round_instruction = (
                "This is the first round — generate broad, diverse queries "
                "that explore the key facets of the question."
            )
        else:
            num_queries = 3
            round_instruction = (
                "We already have partial findings.  Generate targeted follow-up "
                "queries to fill gaps, verify claims, or explore specific aspects "
                "that the report doesn't yet cover well. Keep them as short "
                "keyword phrases, not full questions -- e.g. turn 'what are the "
                "maintenance tasks for X' into 'X maintenance tasks'."
            )

        prompt = current_date_context() + QUERY_GEN_PROMPT.format(
            question=question,
            research_plan=self.research_plan or "(No plan — search broadly.)",
            report=report or "(No findings yet.)",
            round_num=round_num,
            num_queries=num_queries,
            round_instruction=round_instruction,
        )

        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=4096,
                timeout=getattr(self, "query_timeout", 120),
            )
            queries = self._parse_json_array(response)
            # Deduplicate
            new_queries = [q for q in queries if q not in self.queries_used]
            self.queries_used.update(new_queries)
            logger.info(f"Round {round_num} queries: {new_queries}")
            return new_queries
        except Exception as e:
            logger.error(f"Query generation failed: {e}")
            self._emit(phase="warning", message=f"Query generation failed: {e}")
            return []

    # ------------------------------------------------------------------
    # SEARCH + EXTRACT
    # ------------------------------------------------------------------
    async def _search_and_extract(self, queries: List[str],
                                  question: str) -> List[Dict]:
        """Search each query and extract relevant info from top results."""
        all_findings: List[Dict] = []

        # Search all queries in parallel
        search_tasks = [self._search(q) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Collect URLs to fetch from all search results
        urls_to_fetch = []
        for result in search_results:
            if isinstance(result, Exception):
                logger.warning(f"Search error: {result}")
                continue
            if not result:
                continue
            for r in result:
                url = r.get("url", "")
                if url and url not in self.urls_fetched:
                    urls_to_fetch.append(r)
                    self.urls_fetched.add(url)
                    self.analyzed_urls.append({
                        "url": url,
                        "title": r.get("title", "") or url,
                    })
                if len(urls_to_fetch) >= self.max_urls_per_round * len(queries):
                    break

        if self._cancelled or self._time_exceeded():
            return all_findings

        # Fetch and extract URLs with backpressure. Local model servers often
        # serialize requests behind one GPU; flooding them makes every request
        # slower and can trip the extraction timeout.
        semaphore = asyncio.Semaphore(self.extraction_concurrency)

        async def _bounded_extract(result: Dict) -> Optional[Dict]:
            async with semaphore:
                return await self._fetch_and_extract(result["url"], question, result.get("title", ""))

        extract_tasks = [_bounded_extract(r) for r in urls_to_fetch]
        results_gathered = await asyncio.gather(*extract_tasks, return_exceptions=True)

        for result in results_gathered:
            if isinstance(result, Exception):
                logger.warning(f"Extraction error: {result}")
                continue
            if result:
                all_findings.append(result)

        return all_findings

    async def _search(self, query: str) -> List[Dict]:
        """Run a search query using the configured research search provider."""
        try:
            from src.search.providers import _get_search_settings
            from src.search.core import _call_provider, _build_provider_chain

            settings = _get_search_settings()
            provider = (self.search_provider_override or "").strip()
            if not provider:
                provider = (settings.get("research_search_provider") or "").strip()
            if not provider:
                provider = settings.get("search_provider", "searxng")

            if provider == "disabled":
                logger.info("Search is disabled for research")
                return []

            # Try primary provider, then fallbacks
            chain = _build_provider_chain(provider)
            raised = False
            for prov in chain:
                try:
                    results = await asyncio.to_thread(_call_provider, prov, query, 10)
                    if results:
                        logger.info(f"Research search: {prov} returned {len(results)} results")
                        if prov not in self.providers_used:
                            self.providers_used.append(prov)
                        return results
                except Exception as e:
                    raised = True
                    logger.warning(f"Research search: {prov} failed: {e}")
                    self._last_search_error = f"{prov}: {e}"
            # Every provider ran but none returned results. If none of them
            # raised, record an actionable reason here — otherwise this empty
            # path leaves `_last_search_error` unset and the caller surfaces a
            # bare "unknown error" (issue #344). This is exactly the SearXNG
            # case where the service is reachable but all its engines fail, so
            # each provider returns [] without throwing.
            if not raised:
                self._last_search_error = (
                    f"no results from search provider(s): "
                    f"{', '.join(chain) if chain else provider}"
                )
            return []
        except Exception as e:
            logger.error(f"Search failed for '{query}': {e}")
            self._last_search_error = str(e)
            return []

    async def _fetch_and_extract(self, url: str, question: str,
                                 title: str) -> Optional[Dict]:
        """Fetch a URL's content and use LLM to extract relevant info."""
        display = title or url
        self._emit(phase="reading", url=url, title=display,
                   total_sources=len(self.urls_fetched))
        try:
            from src.search import fetch_webpage_content
            page = await asyncio.to_thread(fetch_webpage_content, url, 10)
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return None

        if not page.get("success") or not page.get("content"):
            return None

        content = page["content"]
        # Truncate to avoid blowing up context, preferring paragraph boundary
        if len(content) > self.max_content_chars:
            truncated = content[:self.max_content_chars]
            last_para = truncated.rfind('\n\n')
            if last_para > self.max_content_chars * 0.8:
                content = truncated[:last_para]
            else:
                content = truncated

        try:
            response = await self._llm(
                [
                    {"role": "user", "content": EXTRACTOR_SYSTEM.format(goal=question)},
                    untrusted_context_message("webpage", content),
                ],
                temperature=0.2,
                max_tokens=2048,
                timeout=self.extraction_timeout,
            )
            parsed = self._parse_json_object(response)
            if parsed:
                parsed["url"] = url
                parsed["title"] = title or page.get("title", "")
                parsed["og_image"] = page.get("og_image", "")
                # Skip findings where the LLM says the page is useless
                if is_low_quality(parsed.get("summary", "")):
                    logger.info(f"Skipping low-quality extraction from {url}")
                    return None
                return parsed
            # If JSON parsing fails, treat entire response as evidence
            return {
                "url": url,
                "title": title or page.get("title", ""),
                "og_image": page.get("og_image", ""),
                "rational": "LLM extraction (raw)",
                "evidence": response[:3000],
                "summary": response[:500],
            }
        except Exception as e:
            logger.warning(f"LLM extraction failed for {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # SYNTHESIZE
    # ------------------------------------------------------------------
    async def _synthesize(self, question: str, findings: List[Dict],
                          current_report: str) -> str:
        """LLM synthesizes all findings into an updated report."""
        # Format findings for the prompt
        window = findings[-self.synthesis_window:]
        if len(findings) > self.synthesis_window:
            logger.info(f"Synthesis using last {self.synthesis_window} of {len(findings)} findings")
        findings_text = self._format_findings(window)

        prompt = SYNTHESIZE_PROMPT.format(
            question=question,
            report=current_report or "(First round — no report yet.)",
            new_findings=findings_text,
        )

        try:
            return await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=self.max_report_tokens,
                # Synthesis is a heavy generation call like the final report
                # (which gets 180s); a slow local model (e.g. a 20B served from
                # LM Studio) routinely needs >60s for it. The old 60s cap timed
                # out mid-stream and discarded the round's findings (#1551).
                timeout=180,
            )
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            self._emit(phase="warning", message="Synthesis failed, keeping previous report")
            return current_report  # keep the old report on failure

    # ------------------------------------------------------------------
    # DECIDE
    # ------------------------------------------------------------------
    async def _should_stop(self, question: str, report: str,
                           round_num: int) -> bool:
        """Let the LLM decide whether the report is comprehensive enough."""
        prompt = STOP_PROMPT.format(
            question=question,
            report=report,
            round_num=round_num,
            max_rounds=self.max_rounds,
        )

        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=128,
            )
            # Reasoning models prepend a <think>...</think> block — strip it
            # before checking for YES/NO, otherwise the answer always looks
            # like it starts with "<THINK>" and the engine never stops.
            clean = strip_thinking(response).strip()
            # Tolerate "**YES**", "Yes.", quotes, etc.
            answer = re.sub(r'^[\s*_`"\'>#\-]+', '', clean).upper()
            should_stop = answer.startswith("YES")
            logger.info(f"Stop decision (round {round_num}): {clean[:120]}")
            return should_stop
        except Exception as e:
            logger.warning(f"Stop decision failed: {e}")
            return False  # continue on error

    # ------------------------------------------------------------------
    # FINAL REPORT
    # ------------------------------------------------------------------
    async def _final_report(self, question: str, report: str) -> str:
        """LLM writes a polished final report, retrying if too short."""
        prompt = FINAL_REPORT_PROMPT.format(
            question=question,
            report=report,
        )
        cat_extra = CATEGORY_PROMPTS.get(self.category or "", "")
        if cat_extra:
            prompt += "\n\n" + cat_extra
        # Real, live-caught bug, 2026-09-11: the CITATION REQUIREMENT earlier
        # in FINAL_REPORT_PROMPT was not enough on its own -- confirmed
        # directly, live: a synthesized report with real, correct inline
        # citations went in, and the "polished" final report came out with
        # every citation stripped, even though the CITATION REQUIREMENT
        # paragraph was right there. Added this closing reminder (after
        # everything else, including any category template -- the last
        # thing the model reads before writing), unconditionally rather than
        # just inside CATEGORY_PROMPTS, since self.category can be
        # None/"general" and get no category template appended at all.
        prompt += (
            "\n\nOne last reminder before you write: every <cite id=\"N\" "
            "url=\"...\">title</cite> tag from the evidence above must appear "
            "in your final report, verbatim, right next to the fact it "
            "supports. Copy each tag exactly as written -- do not convert it "
            "to [title](url) markdown, do not paraphrase or shorten it, do "
            "not move it to a references list. Do not write a polished "
            "version that drops or rewrites these tags. Also: do not add "
            "sections, criteria, or claims about topics the evidence above "
            "doesn't cover -- a shorter, accurate report beats a longer one "
            "with invented detail."
        )

        try:
            result = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=self.max_report_tokens,
                timeout=180,
            )

            # If the report is short, expanding it can genuinely help -- but
            # only when there was enough real evidence to justify more words.
            # Real, live-caught tension, 2026-09-11: the old expansion prompt
            # below ("target at least 1000 words", "add specific data") is
            # exactly the same length/detail pressure that caused the
            # original fabricated-sections problem (Hardware, Performance)
            # in a live test -- forcing it on genuinely thin evidence would
            # just reintroduce that pressure through a second path. Skips
            # the forced expansion below min_findings_for_expansion; accepts
            # the shorter, evidence-faithful report instead. 4 findings is a
            # real, chosen threshold: less than roughly one full round's
            # worth of real results is treated as genuinely thin material.
            min_findings_for_expansion = 4
            if len(result.split()) < 400 and len(self.findings) < min_findings_for_expansion:
                logger.info(
                    f"Final report is short ({len(result.split())} words) but "
                    f"only {len(self.findings)} finding(s) were gathered -- "
                    f"accepting the shorter report rather than forcing an "
                    f"expansion that would risk inventing content."
                )
                return result

            if len(result.split()) < 400:
                logger.info(f"Final report too short ({len(result.split())} words), requesting expansion")
                self._emit(phase="writing", message="Expanding report...")
                expanded = await self._llm(
                    [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": result},
                        {"role": "user", "content":
                            "This report is too brief. Please expand it significantly:\n"
                            "- Add detailed paragraphs for each section (not just bullet points)\n"
                            "- Include specific data, numbers, and comparisons from the evidence\n"
                            "- Explain context and significance — don't just list facts\n"
                            "- Use ## headings and ### subheadings\n"
                            "- Target at least 1000 words\n"
                            "Write the full expanded report now."
                        },
                    ],
                    temperature=0.4,
                    max_tokens=self.max_report_tokens,
                    timeout=180,
                )
                if len(expanded.split()) > len(result.split()):
                    return expanded

            return result
        except Exception as e:
            logger.error(f"Final report generation failed: {e}")
            return report  # return the evolving report as-is

    # ------------------------------------------------------------------
    # RECONCILE: restore any <cite> tags the final rewrite dropped
    # ------------------------------------------------------------------
    # Real, updated 2026-09-11 (domain reinforcement): allows any additional
    # attributes (e.g. domain_count="N") between the url attribute and the
    # closing ">", via [^>]*, without capturing them as new groups --
    # reconciliation and completeness-checking code below still only reads
    # group(1)=id, group(2)=url, group(3)=title and must not break.
    # Real, live-caught bug, 2026-09-11: an earlier version used [^<]* for
    # the title group, on the assumption a citation title would never
    # contain a literal "<" character. Confirmed directly, live: a real
    # title read "...faster (<200ms)" -- the embedded "<" broke the match
    # entirely, letting the WHOLE raw <cite> tag (including a fabricated
    # URL, in that specific case) survive completely unprocessed into the
    # final output, bypassing both _strip_cite_tags() and
    # _validate_citations() -- a real safety-net gap, not a cosmetic one.
    # Fixed with a non-greedy (.*?) that matches any character up to the
    # first real </cite>, so an embedded "<" inside the title no longer
    # breaks the match.
    _CITE_TAG_RE = re.compile(r'<cite id="(\d+)" url="([^"]*)"[^>]*>(.*?)</cite>')

    async def _reconcile_citations(self, question: str, pre_rewrite: str, final: str) -> str:
        """Detect and repair citations dropped by the final-report rewrite.

        Real, added 2026-09-11 (citation anchoring): a prompt reminder to
        preserve citations is probabilistic -- the model can still drop or
        rewrite them. This is the mechanical backstop: compare the real set
        of cited URLs BEFORE the rewrite (pre_rewrite, i.e. the synthesized
        `report`) against what actually survived AFTER it (final). If any
        are missing, re-prompt exactly once with the specific, real URLs
        that were dropped, mirroring the existing "too short -> expand"
        retry pattern already used in _final_report -- a single, targeted
        correction, not an unbounded retry loop.

        A URL counts as "surviving" if it appears anywhere in the final
        text at all -- as a <cite> tag, inside a [title](url) markdown
        link (in case the model converted the tag back to markdown despite
        the instruction not to), or as a bare substring -- deliberately
        permissive here, since the real failure mode this guards against
        is the URL disappearing entirely, not its exact surrounding syntax.
        """
        pre_urls = {m.group(2) for m in self._CITE_TAG_RE.finditer(pre_rewrite)}
        if not pre_urls:
            return final

        missing = {url for url in pre_urls if url not in final}
        if not missing:
            return final

        logger.warning(
            "Citation anchoring: %d citation(s) present before the final "
            "rewrite are missing from the output, re-prompting once: %s",
            len(missing), missing,
        )

        missing_list = "\n".join(f"- {url}" for url in sorted(missing))
        try:
            restored = await self._llm(
                [
                    {"role": "user", "content": FINAL_REPORT_PROMPT.format(question=question, report=pre_rewrite)},
                    {"role": "assistant", "content": final},
                    {"role": "user", "content": (
                        "The following citations from the evidence were dropped from your "
                        "report above:\n" + missing_list + "\n\n"
                        "Rewrite the report, restoring each dropped citation as its exact "
                        '<cite id="N" url="...">title</cite> tag from the evidence, right next '
                        "to the fact it supports. Keep everything else in the report the same."
                    )},
                ],
                temperature=0.2,
                max_tokens=self.max_report_tokens,
                timeout=180,
            )
        except Exception as e:
            logger.error(f"Citation reconciliation retry failed: {e}")
            return final

        still_missing = {url for url in missing if url not in restored}
        if still_missing:
            logger.warning(
                "Citation anchoring: %d citation(s) still missing after the "
                "single reconciliation retry, leaving as-is rather than "
                "retrying indefinitely: %s",
                len(still_missing), still_missing,
            )
        return restored

    def _find_unused_citations(self, pre_rewrite: str, final_text: str) -> set:
        """Real, added 2026-09-11 (evidence completeness, transparency-only
        by deliberate design): which real finding IDs never appear in the
        final report at all. Reuses the same _CITE_TAG_RE already built
        and tested for citation anchoring, rather than a second regex.

        Real, fixed 2026-09-11 (caught by a direct regression test after
        the domain-reinforcement change, but a real, pre-existing bug from
        this function's own first version, not something that change
        introduced): originally checked only for a surviving <cite> TAG in
        final_text, but _reconcile_citations (built earlier the same night)
        deliberately treats a URL as "surviving" more permissively -- as a
        <cite> tag, as [title](url) markdown, or as a bare substring -- on
        the reasoning that the model converting the tag to markdown is
        still real, genuine survival, not a drop. The two functions had
        silently disagreed on this. Now takes the real pre-rewrite text
        directly (matching _reconcile_citations's own signature) and uses
        the same permissive "url in final_text" check, so a citation the
        reconciliation pass already accepted as present is never then
        re-flagged as "unused" by this separate check.

        Deliberately NOT a forcing check: this never triggers a retry or
        asks the model to "incorporate" anything. A real, dated comment
        elsewhere in this same file (_final_report's own expansion-retry
        logic, and _validate_citations's own fabricated-citation incident)
        already documents that pressuring the model to use more evidence
        than the question warrants is exactly what caused a real,
        live-caught fabrication bug. This check is read-only surfacing,
        never pressure.
        """
        id_to_url = {int(m.group(1)): m.group(2) for m in self._CITE_TAG_RE.finditer(pre_rewrite)}
        return {i for i, url in id_to_url.items() if url not in final_text}

    def _format_unused_sources_section(self, unused_ids: set) -> str:
        """Real, added 2026-09-11 (evidence completeness): a plain,
        non-coercive list of findings the report didn't directly cite --
        transparency, not a defect report. self.findings is 1-indexed by
        the same real IDs _format_findings() assigns (id N == findings[N-1]),
        confirmed directly against that real, existing function.
        """
        if not unused_ids:
            return ""
        source_lines = []
        for i in sorted(unused_ids):
            if i < 1 or i > len(self.findings):
                continue  # real, defensive guard against an out-of-range id
            finding = self.findings[i - 1]
            title = finding.get("title", "") or finding.get("url", "unknown source")
            url = finding.get("url", "")
            if url:
                source_lines.append(f"- [{title}]({url})")
        # Real, fixed 2026-09-11 (caught directly in isolated testing): don't
        # add the header at all if every real id turned out to be
        # out-of-range or urlless -- a header with nothing listed under it
        # would look genuinely broken to the real end user.
        if not source_lines:
            return ""
        return "\n\n## Sources Not Directly Cited\n" + "\n".join(source_lines)

    def _strip_cite_tags(self, text: str) -> str:
        """Convert any surviving <cite> tags to plain [title](url) markdown.

        Real, added 2026-09-11 (citation anchoring): the structured <cite>
        tag exists to survive the rewrite mechanically -- it was never
        meant to reach the user. Odysseus's real frontend (static/app.js,
        via js/markdown.js) renders plain markdown, not this custom tag, so
        every real, surviving tag is converted back to normal, renderable
        [title](url) form here, after reconciliation and before
        _validate_citations (which already expects that markdown form).
        """
        return self._CITE_TAG_RE.sub(lambda m: f"[{m.group(3)}]({m.group(2)})", text)

    # Matches a literal, visible "(domain_count="N")" (with or without the
    # parens/quotes exactly matching) that the model wrote into its own
    # prose instead of keeping it inside a <cite> tag's attributes, where
    # the reader never sees it. Tolerates minor real variations seen live
    # (curly vs straight quotes, missing parens) rather than only the one
    # exact string from the first observed case.
    _DOMAIN_COUNT_LEAK_RE = re.compile(
        r'\s*\(?\s*domain_count\s*=\s*["\u201c\u201d](\d+)["\u201c\u201d]\s*\)?'
    )

    def _strip_domain_count_leaks(self, text: str) -> str:
        """Remove any literal 'domain_count="N"' that leaked into visible
        prose, as a deterministic backstop to the prompt instruction above.

        Real, live-caught bug, 2026-09-11: domain_count is meant to be an
        internal weighting signal for the model's own reasoning, carried
        only inside <cite> tag attributes a real reader never sees directly
        (_strip_cite_tags above already drops it correctly when the tag
        format is used properly). Confirmed directly, live: the model wrote
        it out as its own visible prose instead, e.g. a table cell reading
        '**Performance** (domain_count="2")' -- a real user would see that
        raw attribute as part of the criteria label, which looks like a
        bug leaking through, not a feature. A prompt instruction alone is
        probabilistic, not a guarantee (the same lesson already learned
        from citation-dropping above) -- this is the deterministic,
        mechanical safety net.
        """
        return self._DOMAIN_COUNT_LEAK_RE.sub("", text)

    # ------------------------------------------------------------------
    # VALIDATE: strip citations to URLs that were never actually fetched
    # ------------------------------------------------------------------
    def _validate_citations(self, text: str) -> str:
        """Strip citations whose URL was never actually fetched this run.

        Real, live-caught failure mode, found while testing the citation
        fix above: even with the CITATION REQUIREMENT in place, the model
        can fabricate a plausible-looking citation for a fact it invented
        itself -- confirmed directly, live: a specific NVIDIA A100 claim
        and an OpenAI research claim, each with a real-looking URL, neither
        present anywhere in the actual findings. Apparently filling out
        category-template sections (e.g. Hardware, Performance) the real
        evidence didn't cover.

        A cited hallucination is worse than an uncited one -- the citation
        falsely signals real grounding. This is a deterministic, mechanical
        check (no LLM call): every [label](url) in the text is compared
        against the real set of URLs in self.findings. A citation to a URL
        outside that set has its link syntax stripped -- "as shown by
        [Some Page](https://fake.example)" becomes "as shown by Some Page"
        -- so the false claim of sourcing is gone without deleting the
        sentence itself. Findings without a resolvable URL are excluded
        from the report entirely; nothing to compare fabricated links to
        would be worse than nothing.
        """
        valid_urls = {f.get("url", "") for f in self.findings if f.get("url")}
        fabricated: List[str] = []

        def _check(match: "re.Match") -> str:
            url = match.group(2)
            if url in valid_urls:
                return match.group(0)
            fabricated.append(url)
            # Real, live-caught readability bug, 2026-09-11: an earlier
            # version returned `label` here (keeping the bare citation
            # title as dangling, unlinked text), on the reasoning that
            # removing the link syntax alone was enough to stop it looking
            # sourced. Confirmed directly, live, that this reads badly to a
            # real reader -- e.g. "...compatibility issues with different
            # systems Local LLM Deployment Guide" trails off with an
            # orphaned proper-noun-looking phrase that has no grammatical
            # connection to the sentence, since it was written as a link's
            # title, not as prose. Removing it entirely reads more cleanly
            # in practice; the sentence's real content rarely depended on
            # the citation title as a grammatical component. Whitespace
            # left behind by the removal is cleaned up below.
            return ""

        # Matches [label](url), tolerating one level of parentheses inside
        # the URL itself (e.g. Wikipedia's "...wiki/Foo_(bar)") so those
        # real, legitimate citations aren't mistaken for malformed links
        # and broken by a naive "stop at the first )" pattern.
        cleaned = re.sub(
            r'\[([^\]]*)\]\(([^()\s]*(?:\([^()]*\)[^()\s]*)*)\)',
            _check,
            text,
        )
        if fabricated:
            logger.warning(
                "Citation validator: stripped %d citation(s) linking to "
                "URLs never actually fetched this run: %s",
                len(fabricated), fabricated,
            )
            self.fabricated_citations = fabricated
            # Real, live-caught cleanup, 2026-09-11: removing a citation
            # entirely (above) can leave a run of doubled spaces where it
            # used to sit, or a stray space before the sentence's own
            # trailing punctuation -- collapse both so the surrounding
            # sentence still reads as one clean sentence, not one with a
            # visible gap where something was cut out.
            cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
            cleaned = re.sub(r'[ \t]+([.,;:!?])', r'\1', cleaned)
        return cleaned

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _emit(self, **kwargs):
        """Send a progress event via the callback, if one is registered."""
        if self._progress:
            try:
                self._progress(kwargs)
            except Exception:
                pass

    def _time_exceeded(self) -> bool:
        return (time.time() - self._start_time) > self.max_time

    # _strip_think_tags removed — use research_utils.strip_thinking()

    @staticmethod
    def _strip_code_block(text: str) -> str:
        """Strip markdown code-block fences (```json ... ```) if present."""
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
        return text.strip()

    def _parse_json_array(self, text: str) -> List[str]:
        """Extract a JSON array of strings from LLM output."""
        text = self._strip_code_block(text)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass

        # Handle truncated arrays — e.g. '["query one", "query two", "query thr'
        # Repair from the LAST array start so an echoed example array earlier
        # in the reply is not harvested into the real query set.
        last_start = text.rfind('[')
        truncated = last_start != -1 and ']' not in text[last_start:]
        if truncated:
            complete_items = re.findall(r'"([^"]*)"', text[last_start:])
            if complete_items:
                logger.info(f"Repaired truncated JSON array: recovered {len(complete_items)} items")
                return complete_items

        # Greedy match to capture the full outermost array
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                pass

        # Multiple complete arrays in one reply (e.g. the model echoes the
        # prompt's Example: [...] before the real array). The greedy match
        # above spans them all and fails to parse, so scan non-greedily and
        # keep the LAST parseable array, which is the model's actual answer.
        last_parsed = None
        for m in re.finditer(r'\[[\s\S]*?\]', text):
            try:
                parsed = json.loads(m.group())
                if isinstance(parsed, list):
                    last_parsed = parsed
            except json.JSONDecodeError:
                continue
        if last_parsed is not None:
            return [str(item) for item in last_parsed]

        # Last resort: harvest quoted strings from the first array start
        arr_start = text.find('[')
        if arr_start != -1:
            fragment = text[arr_start:]
            # Find the last complete quoted string
            complete_items = re.findall(r'"([^"]*)"', fragment)
            if complete_items:
                logger.info(f"Repaired truncated JSON array: recovered {len(complete_items)} items")
                return complete_items

        logger.warning(f"Could not parse JSON array from: {text[:200]}")
        return []

    def _parse_json_object(self, text: str) -> Optional[Dict]:
        """Extract a JSON object from LLM output."""
        text = self._strip_code_block(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Greedy match to capture the full outermost object
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _real_domain(url: str) -> str:
        """Extract a real, normalized domain from a URL for reinforcement
        counting -- strips a leading "www." so "www.example.com" and
        "example.com" count as the same real source, not two different ones.
        """
        try:
            netloc = urlparse(url).netloc.lower()
        except (ValueError, AttributeError):
            return ""
        return netloc[4:] if netloc.startswith("www.") else netloc

    def _format_findings(self, findings: List[Dict]) -> str:
        """Format findings list into readable text for synthesis prompt.

        Real, added 2026-09-11 (citation anchoring): each citation is a
        structured <cite id="N" url="...">title</cite> tag instead of a
        plain [title](url) markdown link. A real, live-caught bug (see
        _final_report's own comment) showed the final-report rewrite stage
        can silently drop plain markdown citations even with an explicit
        prompt reminder in place -- a reminder is probabilistic, not a
        guarantee. A structured tag is a literal, atomic artifact the model
        is asked to preserve rather than reflow as prose, and gives
        _reconcile_citations() below something mechanical to detect and
        compare, rather than trying to parse arbitrary markdown link
        mutations.

        Real, added 2026-09-11 (domain reinforcement, evidence weighting):
        also attaches a real, mechanical "domain_count" attribute -- how
        many of THIS run's findings share the same real domain. This is the
        one weighting signal from that design discussion that survived
        direct verification against the real data: not a subjective
        quality score, not an invented metadata field, just a real count.
        Deliberately not captured by _CITE_TAG_RE (which only needs
        id/url/title for anchoring, reconciliation, and completeness) --
        this attribute is informational to the synthesis model only and
        never parsed back out by any of that existing logic.
        """
        domains = [self._real_domain(f.get("url", "")) for f in findings]
        domain_counts = {d: domains.count(d) for d in domains if d}

        parts = []
        for i, f in enumerate(findings, 1):
            url = f.get("url", "unknown")
            title = f.get("title", "")
            summary = f.get("summary", "")
            evidence = f.get("evidence", "")
            domain_count = domain_counts.get(domains[i - 1], 1)
            # Use summary if available, fall back to truncated evidence
            content = summary if summary else (evidence[:1000] if evidence else "(no content)")
            parts.append(
                f'**Finding {i}** — <cite id="{i}" url="{url}" domain_count="{domain_count}">{title}</cite>'
                f'\n{content}'
            )
        return "\n\n".join(parts)

    def _fallback_report(self, question: str, findings: List[Dict]) -> str:
        """Compile gathered findings into a basic report.

        Used when the LLM synthesis step produced no report (e.g. it timed out)
        but the search rounds did collect findings — so the user still gets the
        material that was gathered instead of "No information could be gathered"
        (#1551).
        """
        return (
            f"# {question}\n\n"
            "_Automatic synthesis did not complete, so this report lists the "
            f"{len(findings)} finding(s) gathered during research._\n\n"
            f"{self._format_findings(findings)}"
        )

    def get_stats(self) -> Dict:
        """Return research statistics."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        stats = {
            "Duration": f"{elapsed:.1f}s",
            "Rounds": self.round_count,
            "Queries": len(self.queries_used),
            "URLs": len(self.urls_fetched),
            "Model": self.llm_model,
        }
        if self.providers_used:
            stats["Search"] = ", ".join(self.providers_used)
        if self.category:
            stats["Category"] = self.category.capitalize()
        if self.fabricated_citations:
            stats["Fabricated citations removed"] = len(self.fabricated_citations)
        return stats
