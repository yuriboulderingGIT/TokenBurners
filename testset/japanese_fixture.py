"""Small public procurement fixture and an independent byte oracle for an additional script."""
import hashlib
from http.server import BaseHTTPRequestHandler

TITLES = ("橋の点検業務", "図書館備品の購入", "公園の改修工事", "市立学校の給食設備")
STATES = ("受付中", "受付中", "受付終了", "取下げ")
ROOT = "http://localhost:8094"


def source_file(index, part):
    return f"%PDF-1.4\nnotice={index}, document={part}\n%%EOF".encode()


def errors(rows):
    by_id = {row["id"]: row for row in rows}
    problems = []
    for i, title in enumerate(TITLES, 1):
        key = f"JP-2099-{i:04}"
        row = by_id.get(key, {})
        expected = dict(title=title, buyerName="港市役所", url=f"{ROOT}/n/{i}", status=("open", "open", "closed", "withdrawn")[i-1],
                        submissionDeadline="2099-12-09T10:45:00+09:00")
        problems.extend(f"{key} {field}" for field, value in expected.items() if row.get(field) != value)
        hashes = {hashlib.sha256(source_file(i, j)).hexdigest() for j in (1, 2)}
        if len(row.get("documents", ())) != 2 or {d["sha256"] for d in row.get("documents", ())} != hashes:
            problems.append(f"{key} file hashes")
    if len(rows) != len(by_id) or len(rows) != len(TITLES):
        problems.append("notice count")
    return problems


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        mime = "text/html; charset=utf-8"
        if self.path == "/notices":
            content = '<h1>公開入札一覧</h1>' + "".join(
                f'<article><h2><a href="/n/{i}">{title}</a></h2></article>' for i, title in enumerate(TITLES, 1))
        elif len(parts) == 2 and parts[1].isdigit() and 1 <= int(parts[1]) <= len(TITLES):
            i = int(parts[1])
            if parts[0] == "n":
                content = (f'<h1>{TITLES[i-1]}</h1><dl><dt>案件番号</dt><dd>JP-2099-{i:04}</dd>'
                           f'<dt>発注機関</dt><dd>港市役所</dd><dt>締切日時</dt><dd>2099年12月09日 10時45分</dd>'
                           f'<dt>受付状況</dt><dd>{STATES[i-1]}</dd></dl><p>日本標準時（Asia/Tokyo）</p>'
                           f'<a href="/files/{i}">入札に関する資料</a>')
            elif parts[0] == "files":
                content = "".join(f'<a download href="/download/{i}/{j}.pdf">資料 {j}（PDF）</a>' for j in (1, 2))
            else:
                self.send_error(404)
                return
        elif len(parts) == 3 and parts[0] == "download" and parts[1].isdigit() and parts[2] in {"1.pdf","2.pdf"}:
            content = source_file(int(parts[1]), int(parts[2][0]))
            mime = "application/pdf"
        else:
            self.send_error(404)
            return
        if isinstance(content, str):
            content = f'<!doctype html><html lang="ja-JP"><meta charset="utf-8"><main>{content}</main></html>'.encode()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
