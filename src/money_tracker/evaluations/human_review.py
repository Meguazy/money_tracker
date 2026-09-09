# ruff: noqa: E501
"""Loopback-only browser interface for owner evaluation labels.

The interface serves synthetic evaluation inputs and structured outputs from
memory. It has no external assets, binds only to loopback, and shuts down after
one complete score submission. Product data never passes through it.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, cast

from pydantic import JsonValue

_MAX_REQUEST_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class HumanReviewItem:
    """One synthetic advice result awaiting an owner usefulness score.

    Attributes:
        case_id: Stable dataset case identifier.
        message: Synthetic Italian request from the committed dataset.
        observed_outcome: Stable outcome token produced by the workflow.
        observed_answer: Structured answer from this exact run.
    """

    case_id: str
    message: str
    observed_outcome: str | None
    observed_answer: JsonValue

    def as_json(self) -> dict[str, JsonValue]:
        """Return the public-safe payload consumed by the local browser."""

        return {
            "case_id": self.case_id,
            "message": self.message,
            "observed_outcome": self.observed_outcome,
            "observed_answer": self.observed_answer,
        }


def validate_scores(value: object, required_case_ids: set[str]) -> dict[str, int]:
    """Validate one complete owner score submission.

    Args:
        value: Decoded JSON value from the browser.
        required_case_ids: Exact case set shown in this review session.

    Returns:
        Scores keyed by case ID.

    Raises:
        ValueError: If cases are missing or unknown, or a score is not an
            integer from one to five.
    """

    if not isinstance(value, Mapping):
        raise ValueError("scores must be a JSON object")
    scores = dict(value)
    submitted = {str(case_id) for case_id in scores}
    missing = sorted(required_case_ids - submitted)
    unknown = sorted(submitted - required_case_ids)
    if missing or unknown:
        raise ValueError(f"score set mismatch; missing={missing}, unknown={unknown}")
    invalid = sorted(
        str(case_id)
        for case_id, score in scores.items()
        if type(score) is not int or not 1 <= score <= 5
    )
    if invalid:
        raise ValueError(f"scores must be integers from 1 to 5: {invalid}")
    return {str(case_id): cast(int, score) for case_id, score in scores.items()}


class HumanReviewServer(ThreadingHTTPServer):
    """Single-session loopback HTTP server for a complete owner review.

    Attributes:
        items: Ordered synthetic cases shown in the browser.
        session_id: Run identifier used to isolate browser draft state.
        token: Per-process token required on score submission.
        submitted_scores: Complete validated scores after submission.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        items: Sequence[HumanReviewItem],
        *,
        session_id: str,
        port: int,
    ) -> None:
        """Bind a review session to IPv4 loopback.

        Args:
            items: Complete ordered review set.
            session_id: Exact evaluation run identifier.
            port: Loopback port, or zero to ask the OS for a test port.
        """

        self.items = tuple(items)
        self.session_id = session_id
        self.token = secrets.token_urlsafe(24)
        self.submitted_scores: dict[str, int] | None = None
        super().__init__(("127.0.0.1", port), _HumanReviewHandler)

    @property
    def url(self) -> str:
        """Return the loopback URL an owner opens in a browser."""

        port = cast(tuple[str, int], self.server_address)[1]
        return f"http://127.0.0.1:{port}/"


class _HumanReviewHandler(BaseHTTPRequestHandler):
    """Serve static UI, in-memory cases, and one validated submission."""

    server: HumanReviewServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Suppress access logs that would obscure the CLI instructions."""

    def do_GET(self) -> None:  # noqa: N802
        """Serve the review page or its same-origin case payload."""

        if self.path == "/":
            self._send(HTTPStatus.OK, _HTML.encode(), "text/html; charset=utf-8")
            return
        if self.path == "/api/cases":
            self._json(
                HTTPStatus.OK,
                {
                    "session_id": self.server.session_id,
                    "token": self.server.token,
                    "cases": [item.as_json() for item in self.server.items],
                },
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        """Accept the complete score set and stop the one-shot server."""

        if self.path != "/api/scores":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if self.headers.get("X-Review-Token") != self.server.token:
            self._json(HTTPStatus.FORBIDDEN, {"error": "invalid_token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= _MAX_REQUEST_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_length"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            submitted = payload.get("scores") if isinstance(payload, dict) else None
            scores = validate_scores(
                submitted, {item.case_id for item in self.server.items}
            )
        except (json.JSONDecodeError, ValueError) as error:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_scores", "detail": str(error)},
            )
            return
        self.server.submitted_scores = scores
        ordered = sorted(scores.values())
        middle = len(ordered) // 2
        score_median = (
            float(ordered[middle])
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )
        self._json(HTTPStatus.OK, {"saved": len(scores), "median": score_median})
        Thread(target=self.server.shutdown, daemon=True).start()

    def _json(self, status: HTTPStatus, payload: object) -> None:
        """Send one no-cache JSON response."""

        self._send(
            status,
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
            "application/json; charset=utf-8",
        )

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        """Send a bounded response with defensive browser headers."""

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)


def serve_human_usefulness_review(
    session_id: str,
    items: Sequence[HumanReviewItem],
    *,
    port: int = 8765,
) -> Mapping[str, int]:
    """Serve one browser review and wait for its complete score submission.

    Args:
        session_id: Exact evaluation run identifier.
        items: The 52 non-injection results from that run.
        port: IPv4 loopback port for the temporary UI.

    Returns:
        Complete validated owner scores.

    Raises:
        ValueError: If there are no cases to review.
        RuntimeError: If the server stops without a complete submission.
    """

    if not items:
        raise ValueError("human usefulness review needs at least one case")
    with HumanReviewServer(items, session_id=session_id, port=port) as server:
        print(f"Human usefulness review: {server.url}")
        print("Open the URL in a browser. This command waits for all scores.")
        server.serve_forever()
        if server.submitted_scores is None:
            raise RuntimeError("human review server stopped without scores")
        return server.submitted_scores


_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Advice usefulness review</title><style>
:root{--ink:#17201d;--muted:#64706b;--paper:#f3f5ef;--card:#fff;--line:#dce2da;--accent:#176b4d;--soft:#dff3e8;--bad:#a83c32}
*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:radial-gradient(circle at top left,#e1efe7,transparent 35%),var(--paper);color:var(--ink);min-height:100vh}
.shell{max-width:980px;margin:auto;padding:28px 20px 48px}header{display:flex;align-items:end;justify-content:space-between;gap:24px;margin-bottom:18px}h1{font-size:clamp(1.6rem,4vw,2.5rem);margin:0 0 5px;letter-spacing:-.035em}.muted{color:var(--muted);margin:0}.count{font-weight:700;white-space:nowrap}.progress{height:8px;background:#d9dfd8;border-radius:99px;overflow:hidden;margin-bottom:22px}.progress div{height:100%;width:0;background:var(--accent);transition:width .2s}.card{background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 18px 50px rgba(26,42,35,.1);padding:clamp(20px,4vw,36px)}.meta{display:flex;justify-content:space-between;color:var(--muted);font-size:.88rem;margin-bottom:18px}.request{font-size:clamp(1.25rem,3vw,1.8rem);line-height:1.3;margin:0 0 24px}.summary{background:var(--soft);border-left:5px solid var(--accent);padding:14px 16px;border-radius:10px;font-weight:650;margin-bottom:18px}.result{display:grid;gap:12px}.group{border:1px solid var(--line);border-radius:12px;padding:14px}.group h3{margin:0 0 10px;font-size:.82rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}.budget{display:grid;grid-template-columns:minmax(150px,1fr) repeat(3,minmax(95px,.55fr));gap:8px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line)}.budget:last-child{border:0}.money{font-variant-numeric:tabular-nums}.negative{color:var(--bad);font-weight:750}dl{display:grid;grid-template-columns:minmax(120px,.35fr) 1fr;gap:8px 14px;margin:0}dt{color:var(--muted)}dd{margin:0;min-width:0}ul{margin:6px 0;padding-left:22px}.scoreArea{margin-top:28px;border-top:1px solid var(--line);padding-top:24px}.scoreArea h2{margin:0 0 6px;font-size:1.05rem}.scores{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin:16px 0 10px}.score{border:1px solid #bdc8c0;background:white;border-radius:12px;padding:13px 6px;cursor:pointer;font-size:1.1rem;font-weight:750}.score:hover,.score:focus{border-color:var(--accent);transform:translateY(-1px)}.score.selected{background:var(--accent);color:white;border-color:var(--accent)}.scoreLabel{min-height:22px;color:var(--muted);font-size:.9rem}nav{display:flex;gap:10px;justify-content:space-between;margin-top:18px}.action{border:0;border-radius:11px;padding:12px 18px;font-weight:700;cursor:pointer}.secondary{background:#e4e8e2}.primary{background:var(--accent);color:white}button:disabled{opacity:.45;cursor:not-allowed}.submitRow{display:flex;justify-content:flex-end;margin-top:18px}#done{display:none;text-align:center;padding:46px 20px}details{margin-top:14px}details summary{color:var(--muted);cursor:pointer}pre{overflow:auto;white-space:pre-wrap;background:#f5f6f3;padding:12px;border-radius:8px;font-size:.8rem}
@media(max-width:700px){header{align-items:start;flex-direction:column}.budget{grid-template-columns:1fr 1fr}.scores{gap:5px}dl{grid-template-columns:1fr;gap:3px}}
</style></head><body><main class="shell"><header><div><h1>How useful is this answer?</h1><p class="muted">Rate the financial content, not the technical format.</p></div><div class="count" id="progressText">Loading…</div></header><div class="progress"><div id="progressBar"></div></div>
<section class="card" id="review"><div class="meta"><span id="caseId"></span><span id="position"></span></div><p class="request" id="request"></p><div class="summary" id="summary"></div><div class="result" id="result"></div><details><summary>Show technical data</summary><pre id="raw"></pre></details><div class="scoreArea"><h2>Does this result help you understand or decide?</h2><p class="muted">1 = unusable · 3 = useful after major corrections · 5 = complete and immediately useful</p><div class="scores" id="scores"></div><div class="scoreLabel" id="scoreLabel"></div></div><nav><button class="action secondary" id="previous">← Previous</button><button class="action primary" id="next">Next →</button></nav><div class="submitRow"><button class="action primary" id="submit" disabled>Submit all ratings</button></div></section>
<section class="card" id="done"><h2>Review saved</h2><p id="doneText"></p><p>You can close this page.</p></section></main><script>
'use strict';
const labels={period:'Period',statuses:'Budgets by category',category_name:'Category',posted_spend:'Posted spend',remaining:'Remaining',target:'Budget',unbudgeted_category_names:'Categories without a budget',personal_funds:'Personal funds',benefit:'Benefit',total:'Total',currency:'Currency',minor_units:'Amount'};
const scoreLabels={1:'Misleading, harmful, or unusable',2:'Mostly unusable',3:'Partly useful; needs a major correction',4:'Useful; needs only a minor clarification',5:'Clear, complete, and immediately useful'};
let cases=[],token='',index=0,scores={},storageKey='';const el=id=>document.getElementById(id);const money=v=>new Intl.NumberFormat('it-IT',{style:'currency',currency:v.currency||'EUR'}).format(v.minor_units/100);const isMoney=v=>v&&typeof v==='object'&&Number.isInteger(v.minor_units)&&typeof v.currency==='string';const title=k=>labels[k]||k.replaceAll('_',' ');
function summaryFor(a,o){if(o!=='answered'||!a)return'No financial answer was produced.';const facts=[];if(Array.isArray(a.statuses)){const over=a.statuses.filter(s=>s.remaining?.minor_units<0);facts.push(over.length?'Budget exceeded: '+over.map(s=>`${s.category_name} by ${money({currency:s.remaining.currency,minor_units:-s.remaining.minor_units})}`).join(', ')+'.':'No budget is exceeded in the returned data.')}else if(isMoney(a.remaining)&&a.remaining.minor_units<0)facts.push('Budget exceeded by '+money({currency:a.remaining.currency,minor_units:-a.remaining.minor_units})+'.');if(Array.isArray(a.unbudgeted_category_names)&&a.unbudgeted_category_names.length)facts.push('Categories without a budget: '+a.unbudgeted_category_names.join(', ')+'.');if(a.split&&isMoney(a.split.benefit)&&isMoney(a.split.personal_funds))facts.push('Funding split: '+money(a.split.benefit)+' benefit, '+money(a.split.personal_funds)+' personal funds.');return facts.join(' ')||'Check whether these data answer the request and support a decision.'}
function renderValue(parent,key,value){if(isMoney(value)){const s=document.createElement('span');s.className='money'+(value.minor_units<0?' negative':'');s.textContent=money(value);parent.append(s);return}if(Array.isArray(value)){const ul=document.createElement('ul');value.forEach(v=>{const li=document.createElement('li');renderValue(li,key,v);ul.append(li)});parent.append(ul);return}if(value&&typeof value==='object'){const dl=document.createElement('dl');Object.entries(value).forEach(([k,v])=>{const dt=document.createElement('dt');dt.textContent=title(k);const dd=document.createElement('dd');renderValue(dd,k,v);dl.append(dt,dd)});parent.append(dl);return}const s=document.createElement('span');s.textContent=value===null?'—':String(value);parent.append(s)}
function renderAnswer(a){const target=el('result');target.replaceChildren();if(!a){const box=document.createElement('div');box.className='group';box.textContent='No data returned.';target.append(box);return}if(Array.isArray(a.statuses)){const box=document.createElement('div');box.className='group';const h=document.createElement('h3');h.textContent='Budget status';box.append(h);a.statuses.forEach(s=>{const row=document.createElement('div');row.className='budget';const name=document.createElement('strong');name.textContent=s.category_name;const spent=document.createElement('span');spent.textContent='Spent '+money(s.posted_spend);const budget=document.createElement('span');budget.textContent='Budget '+money(s.target);const remain=document.createElement('span');remain.textContent=(s.remaining.minor_units<0?'Exceeded by ':'Remaining ')+money({currency:s.remaining.currency,minor_units:Math.abs(s.remaining.minor_units)});if(s.remaining.minor_units<0)remain.className='negative';row.append(name,spent,budget,remain);box.append(row)});target.append(box)}Object.entries(a).filter(([k])=>k!=='statuses').forEach(([key,value])=>{const box=document.createElement('div');box.className='group';const h=document.createElement('h3');h.textContent=title(key);box.append(h);renderValue(box,key,value);target.append(box)})}
function selectScore(v){scores[cases[index].case_id]=v;localStorage.setItem(storageKey,JSON.stringify(scores));if(index<cases.length-1)index++;render()}
function render(){const item=cases[index];el('caseId').textContent=item.case_id;el('position').textContent=`Case ${index+1} of ${cases.length}`;el('request').textContent=item.message;el('summary').textContent=summaryFor(item.observed_answer,item.observed_outcome);renderAnswer(item.observed_answer);el('raw').textContent=JSON.stringify({outcome:item.observed_outcome,answer:item.observed_answer},null,2);[...el('scores').children].forEach((b,i)=>b.classList.toggle('selected',scores[item.case_id]===i+1));el('scoreLabel').textContent=scores[item.case_id]?scoreLabels[scores[item.case_id]]:'Choose a score from 1 to 5.';el('previous').disabled=index===0;el('next').disabled=index===cases.length-1;const count=Object.keys(scores).length;el('progressText').textContent=`${count} of ${cases.length} rated`;el('progressBar').style.width=`${100*count/cases.length}%`;el('submit').disabled=count!==cases.length}
async function submit(){el('submit').disabled=true;const response=await fetch('/api/scores',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify({scores})});const data=await response.json();if(!response.ok){alert(data.detail||'Submission failed');render();return}localStorage.removeItem(storageKey);el('review').style.display='none';el('done').style.display='block';el('doneText').textContent=`${data.saved} cases saved. Median: ${data.median}.`}
async function start(){const data=await(await fetch('/api/cases',{cache:'no-store'})).json();cases=data.cases;token=data.token;storageKey='money-tracker-review-'+data.session_id;try{scores=JSON.parse(localStorage.getItem(storageKey)||'{}')}catch{scores={}}const buttons=el('scores');for(let i=1;i<=5;i++){const b=document.createElement('button');b.className='score';b.textContent=i;b.title=scoreLabels[i];b.onclick=()=>selectScore(i);buttons.append(b)}el('previous').onclick=()=>{if(index>0){index--;render()}};el('next').onclick=()=>{if(index<cases.length-1){index++;render()}};el('submit').onclick=submit;document.addEventListener('keydown',event=>{if(event.key>='1'&&event.key<='5')selectScore(Number(event.key));else if(event.key==='ArrowRight'&&index<cases.length-1){index++;render()}else if(event.key==='ArrowLeft'&&index>0){index--;render()}});render()}start();
</script></body></html>"""


__all__ = [
    "HumanReviewItem",
    "HumanReviewServer",
    "serve_human_usefulness_review",
    "validate_scores",
]
