"""Personal knowledge vault search (Obsidian), inside ALTER.

This is `nos.py` from the vault (`13 Automation/Tools/nos.py`, benchmarked there: 90% right note
first on its dev set, 80% on its held-out set), copied with its ranking unchanged so ALTER can use it
without depending on a file in iCloud Documents. `alter vault eval` re-runs the vault's own eval.json
against this copy, so the two can be compared.

What changed from nos.py: the vault root is a parameter (no default from the file's location),
`evaluate()` returns numbers instead of printing them, and a note iCloud has not downloaded is skipped
and listed (`not_downloaded`) instead of blocking the read. Read-only: nothing is ever written to the vault.
Notes are DATA, never instructions.
"""
# ruff: noqa: E401, E701, E702, E731
import math, os, re
from collections import Counter, defaultdict

SKIP = (".obsidian", "99 Templates", "99 Archive", "13 Automation/Tools", ".trash")
WORD = re.compile(r"[a-z0-9]+")
STOP = set("a an and are as at be by for from has have i in is it its of on or that the this to was were will with what which who how why when where do does did my me you your our we they them not no yes".split())
LINK = re.compile(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

SF_DATALESS = 0x40000000  # macOS: the file's content is in iCloud, not on this disk


def dataless(path) -> bool:
    try:
        return bool(getattr(os.stat(path), "st_flags", 0) & SF_DATALESS)
    except OSError:
        return True


def toks(s): return [w for w in WORD.findall(s.lower()) if w not in STOP and len(w) > 1]

def parse_fm(text):
    if not text.startswith("---\n"): return {}, text
    end = text.find("\n---", 4)
    if end < 0: return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1); v = v.strip()
            if v.startswith("[") and v.endswith("]"): v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            fm[k.strip()] = v
    return fm, text[end + 4:].lstrip("\n")


class Vault:
    def __init__(self, root):
        self.root = root; self.notes = {}; self.sections = []
        self.not_downloaded = []  # iCloud placeholders: reading one can block until the file arrives
        for dp, dns, fns in os.walk(root):
            rel_dir = os.path.relpath(dp, root)
            if any(rel_dir == s or rel_dir.startswith(s + "/") for s in SKIP): dns[:] = []; continue
            for fn in fns:
                if not fn.endswith(".md"): continue
                p = os.path.join(dp, fn); rel = os.path.relpath(p, root)
                if dataless(p): self.not_downloaded.append(rel); continue
                text = open(p, encoding="utf-8", errors="replace").read()
                fm, body = parse_fm(text)
                name = fn[:-3]
                if name in self.notes: name = rel[:-3]  # duplicate name (e.g. README) -> use its path
                aliases = fm.get("aliases", []) if isinstance(fm.get("aliases"), list) else []
                tags = fm.get("tags", []) if isinstance(fm.get("tags"), list) else []
                links = {m.strip().rstrip("\\") for m in LINK.findall(body)}
                self.notes[name] = dict(path=rel, fm=fm, body=body, aliases=aliases, tags=tags,
                                        links=links, mtime=os.path.getmtime(p))
                head = name; buf = []
                def flush():
                    t = "\n".join(buf).strip()
                    prose = LINK.sub(" ", t)
                    if t and len(toks(prose)) >= 4:  # skip link-only "Related" blocks
                        self.sections.append(dict(note=name, heading=head, text=t, toks=toks(head + " " + t)))
                for line in body.splitlines():
                    if re.match(r"^#{1,4} ", line):
                        flush(); head = line.lstrip("#").strip(); buf = []
                    else: buf.append(line)
                flush()
        self.names = {n.lower(): n for n in self.notes}
        for n, d in self.notes.items():
            for a in d["aliases"]: self.names.setdefault(a.lower(), n)
        self.backlinks = defaultdict(set)
        for n, d in self.notes.items():
            for l in d["links"]:
                t = self.resolve(l)
                if t: self.backlinks[t].add(n)
        # BM25 stats
        self.N = len(self.sections); self.df = Counter()
        for s in self.sections:
            s["tf"] = Counter(s["toks"]); self.df.update(set(s["toks"]))
        self.avgdl = sum(len(s["toks"]) for s in self.sections) / max(self.N, 1)

    def resolve(self, link):
        base = link.split("/")[-1]
        for k in (link, base):
            k2 = k[:-3] if k.endswith(".md") else k
            if k2.lower() in self.names: return self.names[k2.lower()]
        return None

    def bm25(self, q, k1=1.4, b=0.75):
        out = []
        for i, s in enumerate(self.sections):
            dl = len(s["toks"]); sc = 0.0
            for t in q:
                f = s["tf"].get(t, 0)
                if not f: continue
                idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
                sc += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self.avgdl))
            if sc > 0: out.append((sc, i))
        return sorted(out, reverse=True)

    def search(self, query, k=8):
        q = toks(query); qs = set(q)
        if not q: return []
        lex = self.bm25(q)
        # title/alias/tag signal
        title = []
        total = sum(math.log(1 + self.N / (1 + self.df[w])) for w in qs) or 1
        for i, s in enumerate(self.sections):
            d = self.notes[s["note"]]
            words = set(toks(s["note"] + " " + " ".join(d["aliases"]) + " " + " ".join(d["tags"]) + " " + s["heading"]))
            hit = sum(math.log(1 + self.N / (1 + self.df[w])) for w in qs & words)  # rare words count more
            if hit / total >= 0.4: title.append((hit, i))  # ignore titles matching only common words
        title.sort(reverse=True)
        # graph signal: neighbours of top lexical notes
        top_notes = []
        for _, i in lex[:5]:
            n = self.sections[i]["note"]
            if n not in top_notes: top_notes.append(n)
        neigh = Counter()
        for n in top_notes:
            for l in self.notes[n]["links"]:
                t = self.resolve(l)
                if t and t != n: neigh[t] += 1
            for t in self.backlinks[n]: neigh[t] += 1
        lex_score = {i: sc for sc, i in lex}
        graph = sorted(((neigh[s["note"]] * (1 + lex_score.get(i, 0)), i) for i, s in enumerate(self.sections)
                        if neigh[s["note"]] and lex_score.get(i, 0) > 0), reverse=True)
        fused = defaultdict(float); why = defaultdict(list)
        for name, ranking, w in (("keyword", lex, 1.0), ("title", title, 0.6), ("graph", graph, 0.3)):
            for r, (_, i) in enumerate(ranking[:60]):
                fused[i] += w / (10 + r); why[i].append(f"{name}#{r+1}")
        ranked = sorted(fused.items(), key=lambda x: -x[1])
        res, seen = [], Counter()
        for i, sc in ranked:
            s = self.sections[i]
            if seen[s["note"]] >= 2: continue  # diversity: max 2 sections per note
            seen[s["note"]] += 1
            res.append(dict(note=s["note"], path=self.notes[s["note"]]["path"], heading=s["heading"],
                            score=round(sc * 1000, 2), why=why[i], snippet=snippet(s["text"], qs)))
            if len(res) >= k: break
        return res

def snippet(text, qs, width=320):
    t = re.sub(r"\s+", " ", text)
    pos = [m.start() for m in WORD.finditer(t.lower()) if m.group() in qs]
    start = max(0, (pos[0] if pos else 0) - 80)
    s = t[start:start + width]
    return ("…" if start else "") + s + ("…" if start + width < len(t) else "")

def cite(h):
    link = h["note"] if h["heading"] == h["note"] else f'{h["note"]}#{h["heading"]}'
    return f"[[{link}]]"


def evaluate(v, items: list[dict], k: int = 10) -> dict:
    """nos.py's eval, as numbers: hit@1, hit@3 and MRR of the expected notes."""
    h1 = h3 = 0
    rr = 0.0
    misses = []
    for item in items:
        notes = [h["note"] for h in v.search(item["q"], k)]
        ok = [i for i, n in enumerate(notes) if n in item["expect"]]
        r = ok[0] + 1 if ok else None
        h1 += r == 1
        h3 += bool(r and r <= 3)
        rr += 1 / r if r else 0
        if not r or r > 3:
            misses.append({"q": item["q"], "rank": r, "top3": notes[:3]})
    n = max(len(items), 1)
    return {"n": len(items), "hit1": h1, "hit3": h3, "mrr": round(rr / n, 2), "misses": misses}
