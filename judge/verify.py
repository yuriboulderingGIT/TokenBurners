import json, sys, hashlib, importlib.util
spec = importlib.util.spec_from_file_location("fx", sys.argv[2]); fx = importlib.util.module_from_spec(spec); sys.argv_backup = sys.argv; spec.loader.exec_module(fx)
got = {t["id"]: t for t in json.load(open(sys.argv[1]))}
h = lambda b: hashlib.sha256(b).hexdigest()
ok = bad = 0
def chk(name, want, have):
    global ok, bad
    if want == have: ok += 1
    else: bad += 1; print(f"  MISMATCH {name}: want {want!r} got {have!r}")
for r in fx.T:
    tid = r[0]; t = got.get(tid)
    print(tid, "present" if t else "MISSING")
    if not t: bad += 1; continue
    d, mo, y = r[4].split("."); chk("publicationDate", f"{y}-{mo}-{d}", t["publicationDate"])
    dd, tt = r[5].split(); d, mo, y = dd.split("."); chk("deadline", f"{y}-{mo}-{d}T{tt}:00+01:00", t["submissionDeadline"])
    chk("title", r[2], t["title"]); chk("buyer", r[3], t["buyerName"]); chk("ref", r[1], t.get("referenceNumber"))
    chk("cpv", [r[6].split("-")[0]], t.get("cpvCodes")); chk("status", "closed" if tid.endswith("0398") else "open", t["status"])
    chk("url", f"http://localhost:8092/vergabe/{tid}", t["url"])
    want = {h(fx.pdf(tid+"-BM")), h(fx.pdf(tid+"-LV")), h(fx.pdf(tid+"-AS"))}
    chk("doc hashes", want, {x["sha256"] for x in t["documents"]})
    for k in ("procedureType", "contractNature", "placeOfPerformance"): print(f"  {k}: {t.get(k)}")
print(f"checks ok={ok} bad={bad}")
