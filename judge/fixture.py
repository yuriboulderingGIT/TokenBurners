"""Judge's private fixture portal (unseen by the team). stdlib only.

Run: python3 -I fixture.py 8091
  /ausschreibungen?seite=N   listing, 3 per page, 7 tenders (one already closed -> must not be 'open')
  /notice/<id>               summary page (trap: no documents here)
  /vergabe/<id>              real tender page with documents
  /_v2                       layout change (different markup + labels, ids unchanged)
  /_captcha                  every page shows a captcha
  /_reset
"""
import hashlib, io, sys, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T = [  # id, ref, title, buyer, pub, deadline(local Berlin), cpv, nature
    ("VG-2026-0412", "4.1-0412/26", "Sanierung Dach Grundschule Lindenweg", "Stadt Hohenfeld, Hochbauamt", "03.11.2026", "08.12.2026 10:00", "45261000-4", "Bauleistung"),
    ("VG-2026-0419", "2.3-0419/26", "Lieferung von 40 Notebooks für die Verwaltung", "Landkreis Ostmark", "05.11.2026", "01.12.2026 12:00", "30213100-6", "Lieferleistung"),
    ("VG-2026-0423", "6.0-0423/26", "Winterdienst Gemeindestraßen 2026/27", "Gemeinde Bachtal", "07.11.2026", "27.11.2026 23:59", "90620000-9", "Dienstleistung"),
    ("VG-2026-0431", "4.1-0431/26", "Neubau Feuerwehrgerätehaus Nord", "Stadt Hohenfeld, Hochbauamt", "10.11.2026", "15.01.2027 11:00", "45216121-8", "Bauleistung"),
    ("VG-2026-0440", "3.2-0440/26", "Reinigung Rathaus und Bürgerbüro", "Gemeinde Bachtal", "12.11.2026", "10.12.2026 09:30", "90911200-8", "Dienstleistung"),
    ("VG-2026-0447", "2.3-0447/26", "Rahmenvertrag Druckerverbrauchsmaterial", "Landkreis Ostmark", "14.11.2026", "05.01.2027 10:00", "30125100-2", "Lieferleistung"),
    ("VG-2026-0398", "6.0-0398/26", "Grünflächenpflege Stadtpark (abgelaufen)", "Gemeinde Bachtal", "20.10.2026", "14.11.2026 10:00", "77313000-7", "Dienstleistung"),
]
STATE = {"v2": False, "captcha": False, "easy": "easy" in sys.argv}
PAGE = 3


def pdf(name):
    return (b"%PDF-1.4\n% judge fixture " + name.encode() + b"\n%%EOF\n")


def zipped(tid):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("Leistungsverzeichnis.pdf", pdf(tid + "-LV"))
        z.writestr("Formblaetter/Angebotsschreiben.pdf", pdf(tid + "-AS"))
    return b.getvalue()


def files(tid):
    return {"Bekanntmachung.pdf": pdf(tid + "-BM"), "Vergabeunterlagen.zip": zipped(tid)}


def wrap(body):
    return f"<!doctype html><html lang=de><head><meta charset=utf-8><title>Vergabeportal Hohenfeld</title></head><body><nav><a href='/'>Start</a> <a href='/impressum'>Impressum</a></nav>{body}<footer>Vergabeportal Hohenfeld · Fixture</footer></body></html>"


def listing(page):
    rows = T[(page - 1) * PAGE: page * PAGE]
    pages = (len(T) + PAGE - 1) // PAGE
    if STATE["v2"]:
        items = "".join(f"<article class='card' data-ref='{r[1]}'><h3><a href='/vergabe/{r[0]}'>{r[2]}</a></h3><dl><dt>Vergabenummer</dt><dd>{r[0]}</dd><dt>Auftraggeber</dt><dd>{r[3]}</dd><dt>Ende der Angebotsfrist</dt><dd>{r[5]} Uhr</dd></dl></article>" for r in rows)
        nav = f"<div class='pager'>Seite {page} von {pages} " + (f"<a rel=next href='/ausschreibungen?seite={page+1}'>weiter &raquo;</a>" if page < pages else "") + "</div>"
        return wrap(f"<main><h1>Laufende Verfahren</h1><section class=results>{items}</section>{nav}</main>")
    items = "".join(f"<tr><td>{r[0]}</td><td><a href='/{"vergabe" if STATE["easy"] else "notice"}/{r[0]}'>{r[2]}</a></td><td>{r[3]}</td><td>{r[4]}</td><td>{r[5]}</td></tr>" for r in rows)
    nav = " ".join(f"<a href='/ausschreibungen?seite={p}'>{p}</a>" for p in range(1, pages + 1))
    if STATE["easy"] and page < pages:
        nav += f" <a rel=next href='/ausschreibungen?seite={page+1}'>Nächste Seite</a>"
    return wrap(f"<h1>Aktuelle Ausschreibungen</h1><table class=liste><tr><th>Kennung</th><th>Bezeichnung</th><th>Vergabestelle</th><th>Veröffentlicht</th><th>Angebotsfrist</th></tr>{items}</table><p class=seiten>Seiten: {nav}</p>")


def summary(r):  # trap: summary page links on to the real tender page
    return wrap(f"<h1>Kurzinfo {r[0]}</h1><p>{r[2]}</p><p>Die vollständige Bekanntmachung und die Vergabeunterlagen finden Sie <a href='/vergabe/{r[0]}'>auf der Verfahrensseite</a>.</p>")


def detail(r):
    docs = "".join(f"<li><a href='/dl/{r[0]}/{n}'>{n}</a></li>" for n in files(r[0]))
    label = ("Angebotsfrist", "Vergabestelle", "Aktenzeichen") if not STATE["v2"] else ("Schlusstermin", "Öffentlicher Auftraggeber", "Az.")
    return wrap(f"<h1>{r[2]}</h1><table class=meta><tr><th>Kennung</th><td>{r[0]}</td></tr><tr><th>{label[2]}</th><td>{r[1]}</td></tr><tr><th>{label[1]}</th><td>{r[3]}</td></tr><tr><th>Veröffentlicht am</th><td>{r[4]}</td></tr><tr><th>{label[0]}</th><td>{r[5]} Uhr (Ortszeit Berlin)</td></tr><tr><th>Verfahrensart</th><td>Offenes Verfahren</td></tr><tr><th>Art</th><td>{r[7]}</td></tr><tr><th>CPV</th><td>{r[6]}</td></tr><tr><th>Erfüllungsort</th><td>12345 Hohenfeld, DE</td></tr></table><h2>Vergabeunterlagen</h2><ul class=docs>{docs}</ul>")


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        p, _, q = self.path.partition("?")
        if p in ("/_v2", "/_captcha", "/_reset"):
            if p == "/_v2": STATE["v2"] = True
            if p == "/_captcha": STATE["captcha"] = True
            if p == "/_reset": STATE.update(v2=False, captcha=False)
            return self.send(200, str(STATE))
        if STATE["captcha"]:
            return self.send(200, wrap("<h1>Sicherheitsprüfung</h1><form><div class='g-recaptcha' data-sitekey='x'></div><p>Bitte bestätigen Sie, dass Sie kein Roboter sind.</p></form>"))
        by = {r[0]: r for r in T}
        if p in ("/", "/ausschreibungen"):
            page = int(dict(x.split("=") for x in q.split("&") if "=" in x).get("seite", 1))
            return self.send(200, listing(page))
        parts = p.strip("/").split("/")
        if parts[0] == "notice" and len(parts) == 2 and parts[1] in by: return self.send(200, summary(by[parts[1]]))
        if parts[0] == "vergabe" and len(parts) == 2 and parts[1] in by: return self.send(200, detail(by[parts[1]]))
        if parts[0] == "dl" and len(parts) == 3 and parts[1] in by and parts[2] in files(parts[1]):
            data = files(parts[1])[parts[2]]
            return self.send(200, data, "application/zip" if parts[2].endswith(".zip") else "application/pdf")
        self.send(404, wrap("<h1>Nicht gefunden</h1>"))


def truth():
    for r in T:
        yield r[0], {n: hashlib.sha256(b).hexdigest() for n, b in files(r[0]).items()}


if __name__ == "__main__":
    if sys.argv[1:] == ["truth"]:
        for t in truth(): print(t)
    else:
        ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
