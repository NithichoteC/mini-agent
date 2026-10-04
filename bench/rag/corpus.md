# RAG benchmark corpus (English)

The PDFs are not in the repo. `run.py` downloads the World Bank files into `corpus/` and checks the
sha256 below; `aura.pdf` is course material from the instructor - copy it into `corpus/` yourself.

| file | pages | MB | title | source | sha256 |
|---|---|---|---|---|---|
| `aura.pdf` | 9 | 0.22 | *Against the Exponential Aura: Why AI Guardrails Need Proportionality, Not Singularity Inflation* (Kan Yuenyong, 2026) | course material | `9279474636d7e9501d3a93db69744954fccbd6dcfc070c473a002255fc59cdd4` |
| `tem-2025-02.pdf` | 72 | 3.51 | Thailand Economic Monitor, Feb 2025 - *Unleashing Growth: Innovation, SMEs and Startups* | https://documents1.worldbank.org/curated/en/099021125051038392/pdf/P5080791f9e0bc03b1ab8019753dc6998d3.pdf | `4c308c3e606490ab3b4d02ddfd717ca47498b947e4470c902fe1487ee855ee63` |
| `tem-2023-12.pdf` | 64 | 3.65 | Thailand Economic Monitor, Dec 2023 - *Thailand's Pathway to Carbon Neutrality: The Role of Carbon Pricing* | https://documents1.worldbank.org/curated/en/099121223123018912/pdf/P5010091ef52cc09d1b46c1af1a43820def.pdf | `f7af28e3c576088c0799a95d8ab29efd4e36a6a48a855b5247e987a4db46a544` |
| `monthly-2026-03.pdf` | 4 | 0.61 | Thailand Monthly Economic Monitor, Mar 2026 | https://documents1.worldbank.org/curated/en/099923303202623923/pdf/IDU-61588b11-5f8e-40ac-9320-840ae8597ecc.pdf | `a7166abb8c5070fa258c906dba936eb3c63118dc0404eb5ffe5babf07e3306db` |

Total: 149 pages, 7.99 MB, about 58,000 words, 53 tables (PyMuPDF `find_tables`).

## Thai

| file | pages | MB | title | source | sha256 |
|---|---|---|---|---|---|
| `budget-2569-statement.pdf` | 30 | 0.44 | คำแถลงประกอบงบประมาณรายจ่ายประจำปีงบประมาณ พ.ศ. 2569 (สำนักงบประมาณ) | https://www.bb.go.th/download.php?id=31908 | `fa77339c3d5bd1043984099449f2e3ddfada957ab95438181a669b22773249cd` |
