"""Document pipeline. OWNER: A2.

Stream each document through the Fetcher, skip anything over 250 MB (ZIPs included),
store it under DATA_DIR/files, compute sha256, unpack ZIPs (also nested), and register
every file in the documents table so /files/{fileId} can serve it.
"""

from app.fetcher import PortalFetcher
from app.models import Document, Tender


async def store_documents(
    portal_id: str, tender: Tender, links: list[str], fetch: PortalFetcher
) -> list[Document]:
    # TODO(A2, P7): real pipeline. Use fetch.stream(url, MAX_DOC_BYTES, FILES_DIR / portal_id),
    #   then db.document_add(...) and return Document(path=f"/files/{file_id}", ...).
    #   Skip links already downloaded in an earlier read (same URL) so refreshes stay cheap.
    #   Guard ZIP bombs: count the UNPACKED size against the 250 MB cap too.
    return []
