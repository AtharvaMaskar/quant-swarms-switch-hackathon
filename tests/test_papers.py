"""Paper intake, offline: httpx.MockTransport + small in-memory fixtures."""
import gzip
import io
import json
import tarfile

import httpx
import pytest

from qs import db, papers
from qs.orchestrator import Orchestrator
from tests.test_orchestrator import CTL, HUMAN, FakeAgent, StubRunner, bodies, msg

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>Intraday  Momentum
 on Index Futures</title><summary>We study momentum.</summary><published>2018-10-15T00:00:00Z</published>
<author><name>A. Author</name></author><author><name>B. Author</name></author></entry></feed>"""

MAIN = r"""\documentclass{article}
\newcommand{\E}{\mathbb{E}}
\newcommand{\half}[1]{\frac{#1}{2}}
\begin{document}
\title{ignored}
\begin{abstract}Abstract in tex.\end{abstract}
\section{Model}
Returns follow a drift. % a comment with $x$ that must vanish
\input{sections/body}
\end{document}
"""
BODY = r"""We assume the return $r_t$ is
\begin{equation}\label{eq:ret}
r_t = \mu + \sigma \varepsilon_t
\end{equation}
where $\varepsilon_t$ is noise. Expected value is
\begin{align*}
\E[r_t] &= \mu \\
\mathrm{Var}(r_t) &= \sigma^2
\end{align*}
and the Sharpe ratio is $$ SR = \frac{\mu}{\sigma} $$ which we test.
\subsection{Costs}
Costs are $c$ per trade, see \eqref{eq:ret}.
"""


def tgz(files: dict, extra=()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        for ti in extra:
            tf.addfile(ti, io.BytesIO(b"evil") if ti.isfile() else None)
    return buf.getvalue()


def tiny_pdf(lines: list[str]) -> bytes:
    """One-page PDF with Helvetica text lines; pypdf tolerates the approximate xref."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")   # noqa: E731
    stream = "BT /F1 11 Tf 14 TL 50 750 Td " + " ".join(f"({esc(x)}) Tj T*" for x in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()


def client(routes: dict) -> httpx.Client:
    def h(req):
        for k, v in routes.items():
            if k in str(req.url):
                return httpx.Response(v[0], content=v[1]) if isinstance(v, tuple) else httpx.Response(200, content=v)
        return httpx.Response(404, content=b"nope")
    return httpx.Client(transport=httpx.MockTransport(h), follow_redirects=True)


def arxiv(eprint, **more):
    return client({"export.arxiv.org": ATOM, "arxiv.org/pdf/": b"%PDF-1.4 fake", "arxiv.org/e-print/": eprint, **more})


def test_arxiv_tar_source_equations_labels_and_files(tmp_path):
    c = arxiv(tgz({"main.tex": MAIN, "sections/body.tex": BODY, "notes.tex": "no class here"}))
    p = papers.fetch("https://arxiv.org/abs/1810.06397v2", out_root=tmp_path, client=c)
    assert (p.lit_id, p.method, p.title) == ("arxiv-1810-06397", "latex", "Intraday Momentum on Index Futures")
    assert p.authors == ["A. Author", "B. Author"] and p.published == "2018-10-15"
    lx = [e["latex"] for e in p.equations]
    assert len(lx) == 3
    assert p.equations[0]["label"] == "eq:ret" and lx[0] == r"r_t = \mu + \sigma \varepsilon_t"
    assert lx[1].startswith(r"\begin{align*}") and r"\mathbb{E}[r_t] &= \mu" in lx[1]   # \E expanded
    assert lx[2] == r"SR = \frac{\mu}{\sigma}"
    assert "return $r_t$ is" in p.equations[0]["context"] and "noise" in p.equations[0]["context"]
    md = (tmp_path / p.lit_id / "paper.md").read_text()
    assert "not instructions" in md and "$\\varepsilon_t$" in md and "a comment" not in md
    assert "## Model" in md and "### Costs" in md and "(ref eq:ret)" in md
    eq = (tmp_path / p.lit_id / "equations.md").read_text()
    assert "```latex\nr_t = \\mu + \\sigma \\varepsilon_t\n```" in eq and "eq:ret" in eq
    assert p.pdf_path and (tmp_path / p.lit_id / "paper.pdf").exists()
    # cached: no network on the second call
    boom = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network on cached fetch")))
    assert papers.fetch("arxiv:1810.06397", out_root=tmp_path, client=boom).equations == p.equations


def test_tar_rejects_traversal_absolute_and_symlinks(tmp_path):
    bad = [tarfile.TarInfo(n) for n in ("../evil.tex", "/abs/evil.tex", "a/../../evil2.tex")]
    for ti in bad:
        ti.size = 4
    link = tarfile.TarInfo("link.tex")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    c = arxiv(tgz({"main.tex": MAIN.replace(r"\input{sections/body}", BODY)}, extra=[*bad, link]))
    p = papers.fetch("arxiv:1810.06397", out_root=tmp_path / "papers", client=c)
    assert len(p.equations) == 3
    assert sum("rejected archive entry" in n for n in p.notes) == 4
    assert not (tmp_path / "evil.tex").exists() and not (tmp_path / "papers" / "evil.tex").exists()
    assert not (tmp_path / "papers" / p.lit_id / "src" / "link.tex").exists()


def test_tar_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(papers, "MAX_BYTES", 100)
    with pytest.raises(papers.PaperError):
        papers._source_tex(tgz({"main.tex": "x" * 200}), tmp_path / "src", [])


def test_gzip_single_tex(tmp_path):
    c = arxiv(gzip.compress(MAIN.replace(r"\input{sections/body}", BODY).encode()))
    p = papers.fetch("1810.06397", out_root=tmp_path, client=c)
    assert p.method == "latex" and len(p.equations) == 3
    assert (tmp_path / p.lit_id / "src" / "main.tex").exists()


def test_html_fallback_when_eprint_is_pdf(tmp_path):
    html = b"""<html><body><h1>T</h1><h2>1 Intro</h2><p>Let <math alttext="x_t"><mi>x</mi></math> be a price.</p>
    <table class="ltx_equation ltx_eqn_table" id="S1.E1"><tr><td><math alttext="x_t = x_{t-1} + e_t" display="block">
    <mi>x</mi></math></td></tr></table></body></html>"""
    p = papers.fetch("1810.06397", out_root=tmp_path, client=arxiv(b"%PDF-1.4 x", **{"arxiv.org/html/": html}))
    assert p.method == "html" and p.equations[0]["latex"] == "x_t = x_{t-1} + e_t" and p.equations[0]["label"] == "S1.E1"
    assert "$x_t$ be a price" in p.sections[0][1]


def test_ssrn_captcha_is_blocked(tmp_path):
    captcha = b"<html><head><title>Just a moment...</title></head><body><div id='px-captcha'></div></body></html>"
    c = client({"papers.ssrn.com": (403, captcha)})
    p = papers.fetch("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4567890", out_root=tmp_path, client=c)
    assert papers.blocked(p) and "ssrn_blocked: ask the human to attach the PDF" in p.notes
    assert not (tmp_path / "ssrn-4567890" / "paper.pdf").exists()    # the HTML is never saved as a pdf
    assert papers.ssrn_blocked(200, captcha) and not papers.ssrn_blocked(200, b"%PDF-1.7")


def test_pdf_equation_lines_flagged(tmp_path):
    pdf = tiny_pdf(["We model the excess return of the index as follows below.",
                    "r_t = a + b * x_{t-1} + e_t     (3)",
                    "where the error term is assumed independent of the signal.",
                    "SR = mu / sigma * sqrt(252)"])
    p = papers.fetch_pdf(pdf, "ssrn-4567890", out_root=tmp_path)
    assert p.method == "pdf" and p.pdf_path
    flagged = [e["latex"] for e in p.equations]
    assert any(x.startswith("r_t = a + b") for x in flagged) and any(x.startswith("SR =") for x in flagged)
    assert not any("assumed independent" in x for x in flagged)
    assert p.equations[0]["page"] == 1 and any("pages [1]" in n for n in p.notes)
    assert "Page 1" in (tmp_path / "ssrn-4567890" / "paper.md").read_text()
    with pytest.raises(papers.PaperError):
        papers.fetch_pdf(b"<html>captcha</html>", "ssrn-4567890", out_root=tmp_path)


class AttachAgent(FakeAgent):
    def read_context(self, room_id, limit=50):
        self.calls.append(("read_context", room_id))
        return {"threads": [{"root": {"timestamp": 5, "attachments": [
            {"filename": "paper.pdf", "mimetype": "application/pdf", "mxc": "mxc://x/1"}]}, "replies": []}]}

    def download_media(self, room_id, mxc):
        return tiny_pdf(["r_t = a + b * x_{t-1} + e_t (1)"])


def orch(tmp_path, agent):
    cfg = {"humans": [HUMAN], "control_room": CTL, "root": tmp_path, "build_cap": 2, "gate": "demo"}
    return Orchestrator(db.connect(tmp_path / "r.db"), agent, cfg, runner=StubRunner())


def test_implement_posts_summary_with_latex_and_reader_kickoff(tmp_path, monkeypatch):
    seen = []

    def fake_fetch(ref, out_root="papers", client=None):
        seen.append((ref, out_root))
        return papers.Paper("arxiv-1810-06397", "arxiv", "1810.06397", "https://arxiv.org/abs/1810.06397",
                            title="Deep Momentum", authors=["A", "B"], abstract="x " * 600, published="2018-10-15",
                            equations=[{"n": 1, "label": "", "latex": "a=b", "context": ""},
                                       {"n": 2, "label": "eq:sr", "latex": r"SR = \frac{\mu}{\sigma}", "context": ""}],
                            pdf_path="p.pdf", method="latex")
    monkeypatch.setattr(papers, "fetch", fake_fetch)
    o = orch(tmp_path, FakeAgent())
    a = o.handle(msg("@orchestrator implement arxiv.org/abs/1810.06397", HUMAN))
    assert seen == [("arxiv.org/abs/1810.06397", tmp_path / "papers")]
    summary, kick = [b for b in bodies(a) if b.startswith("arxiv-1810-06397 round 1: paper")][0], bodies(a)[-1]
    assert "```latex" in summary and r"SR = \frac{\mu}{\sigma}" in summary and "Deep Momentum" in summary
    assert "Equations: 2" in summary and "papers/arxiv-1810-06397/paper.pdf" in summary and len(summary) < 2000
    assert kick == ("@reader arxiv-1810-06397 round 1: read papers/arxiv-1810-06397/paper.md and equations.md "
                    "(paper.pdf for figures), run the memory queries, then brainstorm with the human here.")
    assert ("post", "room-1", summary) in o.agent.calls          # into the lit room
    o.handle(msg("@orchestrator implement 1810.06397", HUMAN))
    assert len(seen) == 1                                       # parsed once; reuse posts no second card


def test_blocked_then_attach(tmp_path, monkeypatch):
    blocked = papers.Paper("ssrn-4567890", "ssrn", "4567890", "u", notes=["ssrn_blocked: ask the human to attach the PDF"])
    monkeypatch.setattr(papers, "fetch", lambda ref, out_root="papers", client=None: blocked)
    o = orch(tmp_path, AttachAgent())
    a = o.handle(msg("@orchestrator implement ssrn:4567890", HUMAN))
    assert "`@orchestrator attach ssrn-4567890`" in bodies(a)[-1] and not [b for b in bodies(a) if "@reader" in b]
    a = o.handle(msg("@orchestrator attach ssrn-4567890", HUMAN, room="room-1"))
    bs = bodies(a)
    assert "paper parsed (pdf)" in bs[0] and "approximate" in bs[0] and bs[-1].startswith("@reader ssrn-4567890 round 1")
    assert (tmp_path / "papers" / "ssrn-4567890" / "paper.pdf").read_bytes().startswith(b"%PDF")
    assert json.loads((tmp_path / "papers" / "ssrn-4567890" / "paper.json").read_text())["method"] == "pdf"
