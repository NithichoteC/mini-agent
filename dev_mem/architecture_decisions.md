# Architecture decisions

บันทึกการตัดสินใจเชิงสถาปัตยกรรมที่ทำแล้วจริง (ADR แบบสั้น) ADR-001 ถึง 007 มาจากโครง AI-Blue ของวิชาและนำมาใช้ทั้งหมด
ADR ที่ยอมรับแล้วไม่แก้ทับ ถ้าเปลี่ยนให้เพิ่ม ADR ใหม่แล้วระบุว่า `แทนที่โดย ADR-NNN`

| ADR | สรุป | สถานะ |
|---|---|---|
| 001 | แกนกลางอยู่ที่ `core/agentic_ai/` import ในชื่อ `agentic_ai` (pyproject: `where = ["core", "."]`) | ยอมรับ |
| 002 | สถาปัตยกรรมของเราเอง ไม่ใช้ LangChain / CrewAI / AutoGen / Semantic Kernel / LangGraph เป็นฐาน | ยอมรับ, test บังคับ |
| 003 | ทิศทาง dependency: frontend → backend → `agentic_ai`; แกนกลางไม่ import backend / frontend / launcher | ยอมรับ, test บังคับ |
| 004 | `dev_mem/` คือความจำของนักพัฒนา แยกจากความจำขณะทำงานของ agent (`agentic_ai/memory/`) | ยอมรับ |
| 005 | prompt และความรู้ของ agent เป็นไฟล์ ไม่ฝังในโค้ด | ยอมรับ — ADR-012 กำหนดรูปแบบไฟล์ |
| 006 | config, secret และ deployment แยกจาก source; `.env` ไม่ขึ้น git | ยอมรับ |
| 007 | tracing เป็นระบบหลักตั้งแต่ต้น | ยอมรับ — `tracing/tracedb.py` |
| 008 | เพิ่ม subpackage `retrieval/` | ยอมรับ |
| 009 | workspace ของโน้ตที่ใช้ร่วมกัน ไฟล์คือข้อมูลจริง | ยอมรับ |
| 010 | index เป็น SQLite ไฟล์เดียว dense search แบบ brute force | ยอมรับ |
| 011 | triple ของ knowledge graph ผูกกับ hash ของหัวข้อ และสกัดเมื่อสั่งเท่านั้น | ยอมรับ |
| 012 | prompt เป็น markdown หนึ่งไฟล์ต่อ prompt อ้างจาก yaml ด้วย `file:` | ยอมรับ |
| 013 | event มีชนิดที่ประกาศไว้ใน `messages/` เทียบกับ AG-UI แต่ไม่ขึ้นกับ AG-UI | ยอมรับ |
| 014 | backend ใช้ FastAPI, config ใช้ YAML (+ JSON สำหรับ tool registry) | ยอมรับ |

---

## ADR-008 — `retrieval/` เป็น subpackage ของแกนกลาง

**บริบท** โครงของวิชามีช่อง `integrations/knowledge_graph/` สำหรับระบบภายนอก แต่การค้นเอกสารและโน้ต (ตัดช่วง, embedding,
BM25, index) เป็นงานของแกนกลางเอง ไม่ใช่ adapter ไปหาระบบอื่น
**ตัดสินใจ** `agentic_ai/retrieval/` เก็บ `rag.py` (เอกสารที่แนบ), `workspace_index.py` และ `workspace_graph.py`;
library GraphRAG ของวิชาอยู่ใน `integrations/knowledge_graph/` ตามช่องเดิม และ `retrieval/` เรียกใช้
**ผล** โครงเพิ่มหนึ่งช่องโดยไม่ย้ายของเดิม; `knowledge_graph` ไม่รู้จัก SQLite หรือ workspace — ส่วนนั้นอยู่ใน `retrieval/`

## ADR-009 — workspace ของโน้ตที่ใช้ร่วมกัน ไฟล์คือข้อมูลจริง

**บริบท** ผลิตภัณฑ์เป็นพื้นที่ทำงานแบบโน้ตที่ agent ช่วยจัดการ ตัวเลือก: เก็บโน้ตในฐานข้อมูล (แบบ Notion) หรือเป็นไฟล์ (แบบ Obsidian)
**ตัดสินใจ** โน้ตเป็นไฟล์ markdown ใน `workspace.dir` ที่ทุก session ใช้ร่วมกัน; สิ่งที่ระบบสร้าง (index, graph, ถังขยะ) อยู่ใน
`.aiblue/`; บันทึกของ session อยู่ใน `sandbox/runs/` การเปลี่ยนไฟล์ทุกครั้งผ่านฟังก์ชันเครื่องมือชุดเดียว ทั้งจาก agent และจาก API
**ผล** เครื่องมือไฟล์เดิมใช้ได้ทันที, index สร้างใหม่จากไฟล์ได้เสมอ, หน้าเว็บกับ agent เห็นการเปลี่ยนแปลงแบบเดียวกัน
(`file.changed`); ต้องจัดการกรณีที่ agent แก้โน้ตที่ผู้ใช้กำลังพิมพ์ (sha ในการบันทึก, 409, ให้คนเลือก)

## ADR-010 — index เป็น SQLite ไฟล์เดียว dense search แบบ brute force

**บริบท** ต้องค้นต่อเนื่องข้าม session อัปเดตทีละไฟล์ ลบและย้ายได้ถูก และกรองตามโฟลเดอร์ ทางเลือกที่ดู: sqlite-vec, LanceDB,
Chroma, Qdrant, FAISS + sidecar
**ตัดสินใจ** stdlib `sqlite3`: ตาราง files / chunks (vector เป็น float32 BLOB) / FTS5; dense search คือผลคูณจุดด้วย numpy
บน vector ของโฟลเดอร์ที่ค้น; ไม่มี file watcher (ระบบอัปเดตเองเมื่อเขียน และ `scan()` ก่อนค้น)
**ผล** ไม่มี dependency ใหม่, โครงตารางย้ายไป PostgreSQL + pgvector ได้ตรง ๆ; ความเร็วพอสำหรับโน้ตของคนหนึ่งคน
ถ้าโตเกินระดับหมื่นหัวข้อให้เปลี่ยนเป็น vector index จริงโดยไม่เปลี่ยน `search()`

## ADR-011 — triple ผูกกับ hash ของหัวข้อ สกัดเมื่อสั่งเท่านั้น

**บริบท** การสกัด triple ใช้โมเดลทุกหัวข้อ บน free tier ที่ output ได้ 1,000 tokens ต่อนาที และมีโควตารายวัน
**ตัดสินใจ** จำว่าหัวข้อไหนอ่านแล้วด้วย hash ของ (หัวข้อ, ข้อความ) ไม่ใช่ path; สกัดเมื่อผู้ใช้สั่ง, หลายหัวข้อต่อคำขอ,
หยุดที่งบ token / เมื่อถูกสั่งหยุด / เมื่อคำขอล้มเหลว โดยหัวข้อที่เหลือรอครั้งถัดไป; ใช้ role `extractor` ที่มีโควตาแยก
**ผล** ย้ายโน้ตไม่มีต้นทุน แก้หัวข้อเดียวอ่านใหม่หัวข้อเดียว การใช้ token ไม่เกิดโดยผู้ใช้ไม่รู้; graph อาจตามโน้ตไม่ทันจนกว่าจะสั่งอ่าน
(หน้าเว็บแสดงจำนวนหัวข้อที่รออยู่) ส่วน `[[link]]` อ่านจากข้อความทุกครั้งจึงไม่ล้าหลัง

## ADR-012 — prompt เป็นไฟล์ markdown

**ตัดสินใจ** หนึ่ง prompt ต่อหนึ่งไฟล์ใน `agentic_ai/prompts/`; `workflow.yaml` อ้างด้วย `file:<ชื่อ>.md` (สตริงธรรมดายังเป็น
prompt ได้สำหรับ workflow เฉพาะกิจ); placeholder `{key}` เติมจาก state ของ session โดย `runtime.loop.render`

## ADR-013 — event ที่ประกาศชนิดไว้ เทียบกับ AG-UI

**บริบท** CLI (`--format json`), SSE ของ backend และ trace ใช้ event ชุดเดียวกัน AG-UI เป็นมาตรฐานเปิดของ stream ระหว่าง
agent กับหน้าเว็บ
**ตัดสินใจ** `messages/events.py` ประกาศชนิดและ field ที่ต้องมีของทุก event พร้อม `validate()` และตารางเทียบกับ event ของ AG-UI
ชื่อ event ยังเป็นของเรา ไม่เพิ่ม dependency
**ผล** test ตรวจทุก event ที่ session ตัวอย่างส่งออก; ถ้าต้องการ frontend แบบ AG-UI ภายหลังทำเป็น adapter บาง ๆ ได้

## ADR-014 — FastAPI และ YAML

**ตัดสินใจ** backend ใช้ FastAPI (SSE ผ่าน `StreamingResponse`, ทดสอบด้วย `TestClient`); config เป็น YAML ตามรูปแบบ
`runtime.yaml` ของวิชา, tool registry เป็น JSON; หน้าเว็บแก้ YAML ด้วย ruamel.yaml ซึ่งเก็บ comment ไว้
