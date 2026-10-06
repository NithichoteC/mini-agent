# Status update

บันทึกความคืบหน้ารายสัปดาห์ รายการใหม่อยู่บนสุด

---

## สัปดาห์ที่ 3 (30 ก.ย. – 5 ต.ค. 2569) — RAG: benchmark 5 แบบ และไฟล์ที่แนบใน agent

**สถานะ:** เสร็จ · 153 offline tests · CI

1. **benchmark RAG 5 แบบ** (hybrid, graph, agentic, corrective, multimodal) ตาม notebook ของวิชา บน PDF จริง
   อังกฤษ 149 หน้า + ไทย 30 หน้า — วัดทั้งต้นทุน/ความเร็ว (แบบของวิชา) และ**คุณภาพ** (67 คำถามพร้อมหน้าที่ถูก)
   รวมถึงแบบที่ให้ LLM วางแผนหรือคัดช่วง, ตอบจริงทั้งระบบ และความแม่นของ OCR — `bench/rag/`
2. **ไฟล์ที่แนบใน agent** — `run --file`, `/attach` ใน chat ไฟล์สั้นใส่ทั้งไฟล์ ไฟล์ยาวทำ index ชั่วคราวใน session
   แล้วค้นด้วย tool `rag_search` (เครื่องมือตัวที่ 9) ชนิดการค้นตั้งใน `workflow.yaml` — `rag.py`, `tools.py`
3. **OCR** ผ่านโมเดล vision บน Groq (role `ocr`) เฉพาะหน้าที่ไม่มีข้อความ ปิดเป็นค่าตั้งต้น ช่วงที่มาจาก OCR มีป้ายเตือน
4. **ภาษาไทย** — ตัดคำด้วย PyThaiNLP สำหรับ BM25, เลขไทยแปลงเฉพาะในรูปที่ใช้จับคู่
5. **Web UI** (`main.py serve`) — แชต, ประวัติ session, การ์ด tool, อนุมัติในแชต, ปุ่ม Stop, แนบไฟล์, meter, settings
   ที่เขียนกลับลง yaml และ trace; engine ย่อ output เก่าให้คำขอไม่เกินเพดานต่อครั้งของ plan และ review เฉพาะ turn ที่ใช้ tool

ผลจริง: hybrid ดีที่สุดในแบบที่ไม่ใช้โมเดล (hit@5 0.878, MRR 0.779) และเป็นค่าตั้งต้น; graph/agentic/corrective
แบบกฎให้ผลเหมือนฐานของมันทุกข้อเพราะคะแนน e5 อยู่ที่ 0.85–0.92 ทุกคำถาม รวมถึงคำถามที่ไม่มีคำตอบ — คะแนนความคล้าย
บอก "ไม่มีในเอกสาร" ไม่ได้; ให้ LLM คัดช่วงช่วยคำถามที่ใช้คำต่าง (MRR 0.654 → 0.742) แต่ใช้ ~2,900 tokens ต่อคำถาม;
ภาษาไทยตัดคำแล้ว hybrid MRR 0.806 → 0.922; embedding บน GPU 1.4 วินาทีต่อ 319 ช่วง (CPU 24 วินาที), ค้นหนึ่งครั้ง ~15 ms;
agent ตอบจากรายงาน 72 หน้าด้วย `rag_search` 3 ครั้ง อ้างเลขหน้า PASS; คำตอบทั้งระบบ RAG ถูก 15/18 และปฏิเสธคำถามที่
ไม่มีคำตอบครบ 4/4; OCR ภาษาไทยอ่านข้อความได้แต่ตัวเลขถูกตรงตัวเพียง 43–50% — ต้องตรวจซ้ำก่อนใช้ตัวเลข

---

## สัปดาห์ที่ 2 (23–29 ก.ย. 2569) — tools, CLI, traceability

**สถานะ:** เสร็จ · 100 offline tests (ไม่ต้องมี key ไม่ต่อเน็ต) · CI

ครบโจทย์ 6 ข้อ แต่ละข้อชี้ไฟล์ได้

1. **เครื่องมือ** — 8 ตัว (`bash read write edit glob grep webfetch websearch`) ค้นเว็บผ่าน Firecrawl
   และสำรองด้วย Exa **ไม่ต้องมี key** `webfetch` คืน markdown เฉพาะเนื้อหาหลัก และใช้ Firecrawl เมื่อเว็บบล็อก
   หรือต้องรัน JavaScript
2. **ทะเบียนเครื่องมือเป็น JSON + permission** — `config/tools.json` เป็น source of truth ของสิ่งที่โมเดลเห็น
   argument ถูกตรวจก่อนเรียกทุกครั้ง permission แบบ allow / ask / deny (rule สุดท้ายที่ match ชนะ,
   คำสั่ง shell ถูกตัดสินทีละคำสั่ง) ปฏิเสธพร้อมเหตุผลที่ส่งกลับให้โมเดลได้
3. **CLI** — `run` / `chat` / `export` / `tools` / `trace` คำตอบไป stdout ความคืบหน้าไป stderr,
   `--format json`, exit code คุยโต้ตอบกับ agent ใน session เดียวได้ (`chat`, `run -c`)
   และทุก session มี `transcript.json`
4. **trace database** — `sandbox/trace.db` หนึ่งแถวต่อข้อความของผู้ใช้ ต่อ action (พร้อมผลตัดสินของ permission
   และโมเดลที่ตอบ) และต่อคำตัดสินของ reviewer ตอบได้ว่า agent ขออะไรแล้ว*ถูกปฏิเสธ* ไม่ใช่แค่ทำอะไร
5. **yaml ยังเป็น config** — `workflow.yaml` + `runtime.yaml` ในรูปแบบ router ของวิชา (role → model → vendor)
6. **`dev_mem/`** — สองไฟล์นี้

ผลจริง: งานปกติ 9 แบบผ่านทั้งหมดใน 1–5 actions (1,703–13,259 tokens) รวมงานที่ต้องใช้ `edit`, ค้นเว็บ
และดึงหน้าที่ Cloudflare บล็อก บทสนทนา 4 turn ใน session เดียวผ่านทุก turn การทดลองขอบเขต: rule `deny`
หยุด `rm` ได้แม้ต่อสายกับคำสั่งอื่นและใส่ `--yes`, เมื่อสั่งให้พิมพ์ environment agent ตอบว่าไม่มี
`GROQ_API_KEY` และสแกนไม่พบ key ใน trace, log หรือ git history

---

## สัปดาห์ที่ 1 (20 ก.ย. 2569) — mini agent

**สถานะ:** เสร็จ · [github.com/NithichoteC/mini-agent](https://github.com/NithichoteC/mini-agent)

ทำ agent ที่รับงานเป็นภาษาธรรมดา เลือก action เอง ทำงานในโฟลเดอร์ของตัวเอง แล้วมี reviewer ตัดสิน

- เครื่องมือ 5 ตัว (จัดการไฟล์, รัน Python, ดึงเว็บ) ทำงานในโฟลเดอร์ของ session เท่านั้น
- ตัวหยุด 3 แบบ: งบ action, การทำ action ซ้ำ, คำตัดสิน BLOCKED — ทุกกรณีส่งต่อให้คนช่วยแล้วทำต่อได้
- agent ใช้ `qwen/qwen3.8-27b`, reviewer ใช้ `openai/gpt-oss-120b` (Groq)
- 31 offline tests รันได้โดยไม่ต้องมี API key, CI รันทุก push

ผลจริง: งานปกติ 4 แบบผ่านทั้งหมด ใช้ 1–4 actions (702–3,046 tokens)
เมื่อถอดเครื่องมือเขียนไฟล์ออก agent รายงานว่าทำไม่ได้ใน 3 actions แทนที่จะวนจนหมดงบ
