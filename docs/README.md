# Documentation

| Path | Contents |
| --- | --- |
| [`architecture.md`](architecture.md) | How the system is put together, and why |
| [`adr/`](adr/) | Ten architecture decision records, each naming the alternatives rejected |
| [`corpus/`](corpus/) | The regulatory documents indexed by the Compliance RAG agent |
| [`legacy/`](legacy/) | Retired code, kept readable outside the import path |

## The corpus

Three documents, committed so retrieval works on a fresh clone:

- **DOJ — Evaluation of Corporate Compliance Programs** (September 2024 revision)
- **SEC — Division of Enforcement Manual**
- **NIST — Cybersecurity Framework 2.0** (CSWP 29)

Run `python ingest_compliance.py` to build the vector index over them. See
[`../chroma_compliance/README.md`](../chroma_compliance/README.md).

## Where to start

Read [`architecture.md`](architecture.md) for the design, then
[`adr/README.md`](adr/README.md) for the decisions that produced it — including
the ones that were wrong the first time and had to be revisited.
