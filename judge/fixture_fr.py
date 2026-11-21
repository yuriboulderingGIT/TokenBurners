"""Second private judge fixture (French, unseen by the team when written). stdlib only.

Run:    python3 -I fixture_fr.py 8093
Check:  python3 -I fixture_fr.py check tenders.json      (tenders.json = GET /portals/{id}/tenders)

  /consultations?start=N   cards, 5 per page, 11 notices; pager is "‹ 1 2 3 ›" (icon only, title="Suivant")
  /avis/<num>              notice page with facts in <dl>; links on to the DCE page (two hops to files)
  /dce/<num>               files: RC pdf, CCTP pdf, DCE zip (accented UTF-8 names inside)
  /_v2  /_captcha  /_login (DCE pages ask for a login)  /_reset
"""
import hashlib, io, json, sys, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# num, reference, title, buyer, published, deadline (Paris local), cpv, nature, procedure, statut
T = [
    (48211, "2026-AO-0173", "Maintenance des ascenseurs des bâtiments communaux", "Ville de Montclair", "02/11/2026", "15/12/2026 à 12h00", "50750000-7", "Services", "Appel d'offres ouvert", "En cours"),
    (48230, "2026-AO-0174", "Fourniture de mobilier scolaire", "Communauté d'agglomération du Val-Vert", "04/11/2026", "08/12/2026 à 17h00", "39160000-1", "Fournitures", "Appel d'offres ouvert", "En cours"),
    (48244, "2026-MAPA-0412", "Réfection de la toiture du gymnase Jean-Moulin", "Ville de Montclair", "05/11/2026", "11/12/2026 à 12h00", "45261210-9", "Travaux", "Procédure adaptée", "En cours"),
    (48251, "2026-AO-0175", "Prestations de nettoyage des locaux administratifs", "Département de la Vallée", "06/11/2026", "05/01/2027 à 12h00", "90910000-9", "Services", "Appel d'offres ouvert", "En cours"),
    (48263, "2026-MAPA-0418", "Acquisition de véhicules électriques utilitaires", "Ville de Montclair", "09/11/2026", "18/12/2026 à 16h00", "34144900-7", "Fournitures", "Procédure adaptée", "En cours"),
    (48270, "2026-AO-0176", "Assistance à maîtrise d'ouvrage pour la rénovation énergétique", "Département de la Vallée", "10/11/2026", "12/01/2027 à 12h00", "71541000-2", "Services", "Procédure avec négociation", "En cours"),
    (48288, "2026-MAPA-0421", "Entretien des espaces verts 2027", "Communauté d'agglomération du Val-Vert", "12/11/2026", "14/12/2026 à 12h00", "77310000-6", "Services", "Procédure adaptée", "En cours"),
    (48295, "2026-AO-0177", "Construction d'une crèche de 40 berceaux", "Ville de Montclair", "13/11/2026", "29/01/2027 à 12h00", "45214100-1", "Travaux", "Appel d'offres ouvert", "En cours"),
    (48301, "2026-MAPA-0425", "Fourniture de repas pour la restauration scolaire", "Département de la Vallée", "16/11/2026", "21/12/2026 à 12h00", "55523100-3", "Services", "Procédure adaptée", "En cours"),
    (48190, "2026-MAPA-0398", "Location de bennes à déchets", "Ville de Montclair", "20/10/2026", "13/11/2026 à 12h00", "90512000-9", "Services", "Procédure adaptée", "Clôturée"),
    (48305, "2026-AO-0178", "Système de vidéoprotection urbaine", "Ville de Montclair", "17/11/2026", "22/12/2026 à 12h00", "35125300-2", "Fournitures", "Appel d'offres ouvert", "Annulée"),
]
STATE = {"v2": False, "captcha": False, "login": False}
PAGE = 5


def pdf(s): return b"%PDF-1.4\n% fixture fr " + s.encode() + b"\n%%EOF\n"


def files(num):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:  # Python sets the UTF-8 flag for non-ASCII names
        z.writestr("Pièces/Cahier des clauses administratives.pdf", pdf(f"{num}-CCAP"))
        z.writestr("Pièces/Bordereau des prix.pdf", pdf(f"{num}-BPU"))
    return {"Règlement de consultation.pdf": pdf(f"{num}-RC"), "CCTP.pdf": pdf(f"{num}-CCTP"), "DCE.zip": b.getvalue()}


def wrap(body):
    return ("<!doctype html><html lang=fr><head><meta charset=utf-8><title>Plateforme des marchés publics de la Vallée</title></head>"
            f"<body><header><a href='/'>Accueil</a> · <a href='/aide'>Aide</a> · <a href='/connexion'>Se connecter</a></header>{body}"
            "<footer>Plateforme des marchés · mentions légales</footer></body></html>")


def listing(start):
    rows = T[start:start + PAGE]
    if STATE["v2"]:
        cards = "".join(f"<li class='result-item'><span class='ref'>{r[1]}</span> — <a class='titre' href='/avis/{r[0]}'>{r[2]}</a><br><small>{r[3]} · clôture le {r[5]}</small></li>" for r in rows)
        body = f"<main><h2>Avis en cours</h2><ul class='results'>{cards}</ul>"
    else:
        cards = "".join(f"<div class='avis'><h3><a href='/avis/{r[0]}'>{r[2]}</a></h3><p class='meta'>Réf. {r[1]} — {r[3]}</p><p class='dl'>Date limite : {r[5]}</p></div>" for r in rows)
        body = f"<main><h1>Consultations</h1><p>{len(T)} consultations</p>{cards}"
    pages = range(0, len(T), PAGE)
    nav = "".join(f"<a href='/consultations?start={s}'>{i + 1}</a> " for i, s in enumerate(pages))
    if start + PAGE < len(T):
        nav += f"<a href='/consultations?start={start + PAGE}' title='Suivant'>›</a>"
    return wrap(f"{body}<nav class='pagination'>{nav}</nav></main>")


def notice(r):
    labels = (("Référence", "Acheteur", "Date de publication", "Date limite de remise des offres", "Type de procédure", "Nature du marché", "Code CPV principal", "Lieu d'exécution", "Statut")
              if not STATE["v2"] else
              ("N° de consultation", "Organisme", "Mise en ligne", "Date et heure limite", "Procédure", "Catégorie", "CPV", "Lieu", "État"))
    vals = (r[1], r[3], r[4], r[5], r[8], r[7], r[6], "75011 Montclair, FR", r[9])
    facts = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in zip(labels, vals))
    return wrap(f"<main><h1>{r[2]}</h1><dl class='fiche'>{facts}</dl><p>Objet : {r[2]}.</p>"
                f"<p><a href='/dce/{r[0]}'>Consulter le dossier de consultation (DCE)</a></p></main>")


def dce(r):
    if STATE["login"]:
        return wrap("<main><h1>Accès au DCE</h1><form method=post action='/connexion'><label>Identifiant <input name=u></label>"
                    "<label>Mot de passe <input type=password name=p></label><button>Se connecter</button></form></main>")
    items = "".join(f"<li><a href='/telecharger/{r[0]}/{i}'>{n}</a></li>" for i, n in enumerate(files(r[0])))
    return wrap(f"<main><h1>DCE — {r[1]}</h1><ul>{items}</ul></main>")


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
            return self.send(200, wrap("<main><h1>Vérification</h1><div class='h-captcha' data-sitekey='x'></div><p>Veuillez confirmer que vous n'êtes pas un robot.</p></main>"))
        by = {str(r[0]): r for r in T}
        args = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
        parts = p.strip("/").split("/")
        if p in ("/", "/consultations"):
            start = args.get("start", "0")
            return self.send(200, listing(int(start))) if start.isdigit() else self.send(400, wrap("bad start"))
        if len(parts) == 2 and parts[0] == "avis" and parts[1] in by: return self.send(200, notice(by[parts[1]]))
        if len(parts) == 2 and parts[0] == "dce" and parts[1] in by: return self.send(200, dce(by[parts[1]]))
        if len(parts) == 3 and parts[0] == "telecharger" and parts[1] in by and not STATE["login"]:
            names = list(files(int(parts[1])))
            if parts[2].isdigit() and int(parts[2]) < len(names):
                n = names[int(parts[2])]
                return self.send(200, files(int(parts[1]))[n], "application/zip" if n.endswith(".zip") else "application/pdf",
                                 [("Content-Disposition", "attachment; filename*=UTF-8''" + __import__("urllib.parse").parse.quote(n))])
        self.send(404, wrap("<h1>Page introuvable</h1>"))


def check(path):
    got = {t["id"]: t for t in json.load(open(path))}
    by_ref = {t.get("referenceNumber"): t for t in got.values()}
    h = lambda b: hashlib.sha256(b).hexdigest()
    ok = bad = 0
    for r in T:
        t = got.get(r[1]) or got.get(str(r[0])) or by_ref.get(r[1])
        if not t:
            print(f"{r[1]} MISSING"); bad += 1; continue
        d, m, y = r[4].split("/"); dd, hh = r[5].split(" à "); D, M, Y = dd.split("/")
        want = {
            "title": r[2], "buyerName": r[3], "publicationDate": f"{y}-{m}-{d}",
            "submissionDeadline": f"{Y}-{M}-{D}T{hh.replace('h', ':')}:00+01:00",
            "status": {"En cours": "open", "Clôturée": "closed", "Annulée": "withdrawn"}[r[9]],
            "url": f"http://localhost:8093/avis/{r[0]}",
            "docs": {h(pdf(f"{r[0]}-{k}")) for k in ("RC", "CCTP", "CCAP", "BPU")},
            "cpvCodes": [r[6][:8]], "contractNature": {"Services": "services", "Fournitures": "supplies", "Travaux": "works"}[r[7]],
        }
        have = dict(t, docs={x["sha256"] for x in t["documents"]})
        miss = [f"{k}: want {v!r} got {have.get(k)!r}" for k, v in want.items() if have.get(k) != v]
        ok += len(want) - len(miss); bad += len(miss)
        print(f"{r[1]} id={t['id']!r}" + ("" if not miss else "\n    " + "\n    ".join(miss)))
    print(f"tenders {len(got)}/{len(T)}  checks ok={ok} bad={bad}")


if __name__ == "__main__":
    if sys.argv[1] == "check": check(sys.argv[2])
    else: ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
