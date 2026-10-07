"""Builds the compliance policy vector store from ./docs/corpus.

`ingest.py` indexes ./datasets — which holds the OFAC SDN name lists, not policy
prose — so the compliance agent had no regulatory text to retrieve and fell
through to scraping the open web. This script indexes the frameworks in
`docs/corpus/`
(ECCP, the NIST cybersecurity profile, the enforcement manual) into a separate,
small collection that the RAG tool queries.

Embeddings are the same free local MiniLM model the rest of the project uses, so
no API key is involved.

    python ingest_compliance.py
"""

import os
import re
import warnings
from pathlib import Path

from dotenv import load_dotenv

warnings.filterwarnings("ignore")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
load_dotenv()

from langchain_community.document_loaders import PyPDFLoader, TextLoader  # noqa: E402
from langchain_community.vectorstores import Chroma  # noqa: E402
from langchain_huggingface import HuggingFaceEmbeddings  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "docs" / "corpus"
PERSIST_DIR = BASE_DIR / "chroma_compliance"
COLLECTION = "compliance_policy"
EMBED_MODEL = "all-MiniLM-L6-v2"

# Human-facing names for the frameworks, so citations read like a policy
# reference rather than a filename.
FRAMEWORK_TITLES = {
    "ECCP Revision 2024 0922 (FINAL CLEAN).pdf": "DOJ Evaluation of Corporate Compliance Programs (2024)",
    "NIST.CSWP.29.pdf": "NIST Cybersecurity Framework 2.0",
    "enforcementmanual.pdf": "SEC Division of Enforcement Manual",
    "ADR-001.md": "Sentinel ADR-001 — Architecture Decision Record",
}


# Running headers repeat on every page and would otherwise dominate each chunk.
RUNNING_HEADERS = (
    r"U\.?\s*S\.?\s*Department of Justice\s+Criminal Division\s+Evaluation of Corporate "
    r"Compliance Programs\s*\(Updated September 2024\)",
    r"SEC Division of Enforcement\s+Enforcement Manual",
)


def is_substantive(text: str) -> bool:
    """Keeps passages that state rules, drops reference and citation blocks."""
    body = text.strip()
    if len(body) < 240:
        return False
    # A passage that is mostly URLs and "available at ..." pointers cites other
    # sources rather than stating an obligation of its own.
    pointers = len(re.findall(r"https?://|available at|see, e\.g\.,|\bId\.", body, flags=re.I))
    return pointers * 90 < len(body)


def tidy(text: str) -> str:
    """Removes the layout artefacts PDF extraction leaves behind."""
    # Smart quotes and dashes that failed to decode arrive as replacement chars.
    text = text.replace("\ufffd", "'").replace("­", "")
    # Rejoin words split across a line break by hyphenation.
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    # Page furniture: bare page numbers and "Page 4 of 92" runners.
    text = re.sub(r"\n\s*(?:page\s+)?\d+\s*(?:of\s+\d+)?\s*\n", "\n", text, flags=re.I)
    # PDF bullet glyphs, including the stray "o" sub-bullet Word exports leave.
    text = re.sub(r"[•●▪]\s*", "\n• ", text)
    text = re.sub(r"(?<=[.:;?!\n])\s+o\s+(?=[A-Z])", "\n• ", text)
    # Unwrap soft line breaks inside a sentence, keep paragraph breaks.
    text = re.sub(r"(?<![.:;?!\n])\n(?![\n•])", " ", text)
    # Justified-text damage: "risk- based" -> "risk-based", "T hird" -> "Third".
    text = re.sub(r"(\w)-\s+(\w)", r"\1-\2", text)
    text = re.sub(r"\b([B-HJ-Z]) ([a-z]{2,})", r"\1\2", text)
    for header in RUNNING_HEADERS:
        text = re.sub(header, " ", text, flags=re.I)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_documents():
    documents = []
    if not DOCS_DIR.is_dir():
        raise SystemExit(f"No docs directory at {DOCS_DIR}")

    for path in sorted(DOCS_DIR.iterdir()):
        if path.suffix.lower() not in {".pdf", ".md", ".txt"}:
            continue

        loader = PyPDFLoader(str(path)) if path.suffix.lower() == ".pdf" else TextLoader(str(path), encoding="utf-8")
        try:
            pages = loader.load()
        except Exception as exc:
            print(f"  !  skipped {path.name}: {exc}")
            continue

        framework = FRAMEWORK_TITLES.get(path.name, path.stem)
        kept = 0
        for page in pages:
            page.page_content = tidy(page.page_content)
            # Cover pages and separators carry no retrievable obligation.
            if len(page.page_content) < 200:
                continue
            page.metadata = {
                "framework": framework,
                "filename": path.name,
                "page": int(page.metadata.get("page", 0)) + 1,
            }
            documents.append(page)
            kept += 1
        print(f"  +  {path.name}: {kept} usable page(s) of {len(pages)}")

    return documents


def main() -> None:
    print("Building the compliance policy index from ./docs/corpus")
    documents = load_documents()
    if not documents:
        raise SystemExit("No policy text extracted — nothing to index.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=180,
        separators=["\n\n", "\n• ", "\n", ". ", " "],
    )
    chunks = splitter.split_documents(documents)
    # Fragments this short are headers or stray captions, never a citable rule.
    chunks = [chunk for chunk in chunks if is_substantive(chunk.page_content)]
    print(f"\n{len(documents)} pages -> {len(chunks)} retrievable passages")

    print(f"Embedding with {EMBED_MODEL} (local, no API key)…")
    embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)

    Chroma.from_documents(
        chunks,
        embeddings,
        persist_directory=str(PERSIST_DIR),
        collection_name=COLLECTION,
    )
    print(f"\nDone. Collection '{COLLECTION}' written to {PERSIST_DIR}")


if __name__ == "__main__":
    main()
