"""Third private judge fixture: a Dutch JavaScript app with a JSON API (unseen by the team when written). stdlib only.

Run:    python3 -I fixture_nl.py 8094
Check:  python3 -I fixture_nl.py check tenders.json      (tenders.json = GET /portals/{id}/tenders)

  /aanbestedingen                      HTML shell; /static/app.js renders the list from the API
  /aanbesteding/<uuid>                 same shell; app.js renders one tender from the API
  /api/v1/aanbestedingen?pagina=N      5 per page, {"inhoud": [...], "totaal": 11, "pagina": N}
  /api/v1/aanbestedingen/<uuid>        one tender with "documenten": [{"naam", "href", "grootte"}]
  /api/v1/documenten/<uuid>/<n>        file bytes
  Deadlines are local Dutch time without an offset; the page footer says so in words.
  /_v2 (API moves to /api/v2 with renamed keys)  /_captcha  /_login (files need a login)  /_reset
"""
import hashlib, io, json, sys, uuid, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# kenmerk, titel, dienst, publicatie, sluiting (local NL), cpv, soort, procedure, status
ROWS = [
    ("2026-0193", "Onderhoud openbare verlichting 2027-2030", "Gemeente Waterland-Oost", "2026-11-04", "2026-12-15T12:00", "50232100-1", "Diensten", "Openbaar", "Gepubliceerd"),
    ("2026-0201", "Levering elektrische dienstauto's", "Provincie Noordkust", "2026-11-05", "2026-12-09T10:00", "34144900-7", "Leveringen", "Openbaar", "Gepubliceerd"),
    ("2026-0207", "Renovatie sporthal De Vijverhof", "Gemeente Waterland-Oost", "2026-11-06", "2027-01-14T14:00", "45212225-9", "Werken", "Openbaar", "Gepubliceerd"),
    ("2026-0212", "Schoonmaak gemeentelijke gebouwen", "Gemeente Duinmeer", "2026-11-09", "2026-12-18T12:00", "90911200-8", "Diensten", "Niet-openbaar", "Gepubliceerd"),
    ("2026-0215", "ICT-werkplekbeheer", "Provincie Noordkust", "2026-11-10", "2027-01-08T12:00", "72250000-2", "Diensten", "Openbaar", "Gepubliceerd"),
    ("2026-0220", "Baggerwerkzaamheden havenkom", "Waterschap Lage Landen", "2026-11-11", "2026-12-21T11:00", "45252124-1", "Werken", "Openbaar", "Gepubliceerd"),
    ("2026-0224", "Groenonderhoud wijkparken", "Gemeente Duinmeer", "2026-11-12", "2026-12-11T12:00", "77310000-6", "Diensten", "Onderhandelingsprocedure", "Gepubliceerd"),
    ("2026-0229", "Levering kantoormeubilair", "Gemeente Waterland-Oost", "2026-11-13", "2026-12-16T12:00", "39130000-2", "Leveringen", "Openbaar", "Gepubliceerd"),
    ("2026-0233", "Ontwerp fietsbrug Zuidkanaal", "Waterschap Lage Landen", "2026-11-16", "2027-02-02T12:00", "71322000-1", "Diensten", "Openbaar", "Gepubliceerd"),
    ("2026-0170", "Inzameling bedrijfsafval", "Gemeente Duinmeer", "2026-10-15", "2026-11-12T12:00", "90511000-2", "Diensten", "Openbaar", "Gesloten"),
    ("2026-0236", "Camerabewaking stationsgebied", "Gemeente Waterland-Oost", "2026-11-17", "2026-12-22T12:00", "35125300-2", "Leveringen", "Openbaar", "Ingetrokken"),
]
UUIDS = {r[0]: str(uuid.uuid5(uuid.NAMESPACE_URL, "nl-fixture/" + r[0])) for r in ROWS}
BY_UUID = {UUIDS[r[0]]: r for r in ROWS}
STATE = {"v2": False, "captcha": False, "login": False}
PAGE = 5


def pdf(s): return b"%PDF-1.4\n% fixture nl " + s.encode() + b"\n%%EOF\n"


def files(ref):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("Bijlagen/Programma van eisen.pdf", pdf(f"{ref}-PVE"))
        z.writestr("Bijlagen/Inschrijvingsbiljet.pdf", pdf(f"{ref}-IB"))
    return {"Aanbestedingsleidraad.pdf": pdf(f"{ref}-ALD"), "Nota van inlichtingen 1.pdf": pdf(f"{ref}-NVI"), "Aanbestedingsdocumenten.zip": b.getvalue()}


def api(r, full=False):
    u = UUIDS[r[0]]
    if STATE["v2"]:
        d = {"uuid": u, "referentie": r[0], "naam": r[1], "organisatie": {"naam": r[2]}, "datumPublicatie": r[3],
             "deadline": {"lokaal": r[4]}, "fase": r[8], "link": f"/aanbesteding/{u}"}
    else:
        d = {"id": u, "kenmerk": r[0], "titel": r[1], "aanbestedendeDienst": r[2], "publicatiedatum": r[3],
             "sluitingsdatum": r[4], "status": r[8], "url": f"/aanbesteding/{u}"}
    if full:
        d |= {"cpv": [r[5]], "typeOpdracht": r[6], "procedure": r[7],
              "documenten": [{"naam": n, "href": f"/api/{'v2' if STATE['v2'] else 'v1'}/documenten/{u}/{i}", "grootte": len(b)}
                             for i, (n, b) in enumerate(files(r[0]).items())]}
    return d


SHELL = """<!doctype html><html lang=nl><head><meta charset=utf-8><title>Aanbestedingsplatform Noordkust</title></head>
<body><header><a href="/aanbestedingen">Aanbestedingen</a> · <a href="/inloggen">Inloggen</a></header>
<main id="app"><p>Laden…</p></main><footer>Alle tijden zijn Nederlandse tijd (Amsterdam).</footer>
<script src="/static/app.js"></script></body></html>"""

APP_JS = r"""
const V = %s, app = document.getElementById('app');
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function lijst() {
  const p = Number(new URLSearchParams(location.search).get('pagina') || 0);
  const r = await fetch(`/api/${V}/aanbestedingen?pagina=${p}`, {headers: {Accept: 'application/json'}});
  const d = await r.json(), rows = d.inhoud || d.resultaten;
  app.innerHTML = `<h1>Lopende aanbestedingen</h1>` + rows.map(t => `<article><h2><a href="${t.url || t.link}">${esc(t.titel || t.naam)}</a></h2>
    <p>${esc(t.kenmerk || t.referentie)} · ${esc(t.aanbestedendeDienst || t.organisatie.naam)} · sluit ${esc(t.sluitingsdatum || t.deadline.lokaal)}</p></article>`).join('')
    + `<nav>${p > 0 ? `<a href="?pagina=${p-1}">Vorige</a>` : ''} ${(p+1)*5 < d.totaal ? `<a href="?pagina=${p+1}">Volgende</a>` : ''}</nav>`;
}
async function detail(id) {
  const t = await (await fetch(`/api/${V}/aanbestedingen/${id}`, {headers: {Accept: 'application/json'}})).json();
  app.innerHTML = `<h1>${esc(t.titel || t.naam)}</h1><dl><dt>Kenmerk</dt><dd>${esc(t.kenmerk || t.referentie)}</dd>
    <dt>Aanbestedende dienst</dt><dd>${esc(t.aanbestedendeDienst || t.organisatie.naam)}</dd>
    <dt>Sluitingsdatum</dt><dd>${esc(t.sluitingsdatum || t.deadline.lokaal)}</dd><dt>Status</dt><dd>${esc(t.status || t.fase)}</dd></dl>
    <h2>Documenten</h2><ul>${t.documenten.map(x => `<li><a href="${x.href}">${esc(x.naam)}</a></li>`).join('')}</ul>`;
}
const m = location.pathname.match(/^\/aanbesteding\/([\w-]+)/);
(m ? detail(m[1]) : lijst()).catch(e => { app.textContent = 'Fout bij laden'; });
"""

CAPTCHA = "<!doctype html><html lang=nl><body><h1>Beveiligingscontrole</h1><div class='g-recaptcha' data-sitekey='x'></div><p>Bevestig dat u geen robot bent.</p></body></html>"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        for k, v in extra: self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        p, _, q = self.path.partition("?")
        if p in ("/_v2", "/_captcha", "/_login", "/_reset"):
            if p == "/_reset": STATE.update(v2=False, captcha=False, login=False)
            else: STATE[p[2:]] = True
            return self.send(200, json.dumps(STATE), "application/json")
        if STATE["captcha"]:
            return self.send(403 if p.startswith("/api/") else 200, CAPTCHA)
        args = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
        parts = p.strip("/").split("/")
        ver = "v2" if STATE["v2"] else "v1"
        if p in ("/", "/aanbestedingen") or (len(parts) == 2 and parts[0] == "aanbesteding" and parts[1] in BY_UUID):
            return self.send(200, SHELL)
        if p == "/static/app.js":
            return self.send(200, APP_JS % json.dumps(ver), "application/javascript; charset=utf-8")
        if parts[:3] == ["api", ver, "aanbestedingen"]:
            if len(parts) == 3:
                n = args.get("pagina", "0")
                if not n.isdigit(): return self.send(400, json.dumps({"fout": "pagina"}), "application/json")
                rows = [api(r) for r in ROWS[int(n) * PAGE:(int(n) + 1) * PAGE]]
                key = "resultaten" if STATE["v2"] else "inhoud"
                return self.send(200, json.dumps({key: rows, "totaal": len(ROWS), "pagina": int(n), "grootte": PAGE}), "application/json")
            if len(parts) == 4 and parts[3] in BY_UUID:
                return self.send(200, json.dumps(api(BY_UUID[parts[3]], full=True)), "application/json")
        if parts[:3] == ["api", ver, "documenten"] and len(parts) == 5 and parts[3] in BY_UUID:
            if STATE["login"]:
                return self.send(401, "<!doctype html><html lang=nl><body><h1>Inloggen vereist</h1><form method=post action=/inloggen><input name=gebruiker><input type=password name=wachtwoord><button>Inloggen</button></form></body></html>")
            items = list(files(BY_UUID[parts[3]][0]).items())
            if parts[4].isdigit() and int(parts[4]) < len(items):
                n, b = items[int(parts[4])]
                from urllib.parse import quote
                return self.send(200, b, "application/zip" if n.endswith(".zip") else "application/pdf",
                                 [("Content-Disposition", f"attachment; filename*=UTF-8''{quote(n)}")])
        self.send(404, json.dumps({"fout": "niet gevonden"}), "application/json")


def check(path):
    got = json.load(open(path))
    by = {}
    for t in got:
        by[t["id"]] = t
        if t.get("referenceNumber"): by.setdefault(t["referenceNumber"], t)
    h = lambda b: hashlib.sha256(b).hexdigest()
    ok = bad = 0
    for r in ROWS:
        t = by.get(r[0]) or by.get(UUIDS[r[0]])
        if not t:
            print(f"{r[0]} MISSING"); bad += 1; continue
        want = {
            "title": r[1], "buyerName": r[2], "publicationDate": r[3], "submissionDeadline": r[4] + ":00+01:00",
            "status": {"Gepubliceerd": "open", "Gesloten": "closed", "Ingetrokken": "withdrawn"}[r[8]],
            "url": f"http://localhost:8094/aanbesteding/{UUIDS[r[0]]}",
            "docs": {h(pdf(f"{r[0]}-{k}")) for k in ("ALD", "NVI", "PVE", "IB")},
        }
        have = dict(t, docs={x["sha256"] for x in t["documents"]})
        miss = [f"{k}: want {v!r} got {have.get(k)!r}" for k, v in want.items() if have.get(k) != v]
        ok += len(want) - len(miss); bad += len(miss)
        print(f"{r[0]} id={t['id']!r}" + ("" if not miss else "\n    " + "\n    ".join(miss)))
    print(f"tenders {len(got)}/{len(ROWS)}  checks ok={ok} bad={bad}")


if __name__ == "__main__":
    if sys.argv[1] == "check": check(sys.argv[2])
    else: ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
