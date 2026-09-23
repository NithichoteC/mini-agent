# Status update

บันทึกความคืบหน้า ใหม่อยู่บน

---

## สัปดาห์ที่ 2 — tools, CLI, traceability

**สถานะ:** วางแผน

โจทย์: เครื่องมือค้นเว็บ/ดึงหน้าเว็บ, ทะเบียนเครื่องมือ + permission เป็น JSON, interface แบบ CLI,
trace database, ยังใช้ yaml เป็น config, และโฟลเดอร์ `dev_mem/` นี้

ยังไม่แตะโค้ดหลัก — กำลังสำรวจและทำ spec

---

## สัปดาห์ที่ 1 — mini agent

**สถานะ:** เสร็จ · [github.com/NithichoteC/mini-agent](https://github.com/NithichoteC/mini-agent)

ทำ agent ที่รับงานเป็นภาษาธรรมดา เลือก action เอง ทำงานในโฟลเดอร์ของตัวเอง แล้วมี reviewer ตัดสิน

- `main.py` `loop.py` `tools.py` `sandbox.py` `llm_handler.py` + `config/workflow.yaml`
- เครื่องมือ: `write_file` `read_file` `list_files` `run_python` `http_get` `final_answer`
- ตัวหยุด: งบ action, การทำ action ซ้ำ, และคำตัดสิน BLOCKED — ทุกกรณีส่งต่อให้คนช่วย
- agent ใช้ `qwen/qwen3.8-27b`, reviewer ใช้ `openai/gpt-oss-120b` (Groq)
- 31 offline tests รันได้โดยไม่ต้องมี API key, CI รันทุก push

ผลจริง: calculator+tip 4 actions/3,046 tokens · index.html 2/2,113 · fetch title 3/1,849 ·
"what is 3-10" 1/702 · ถอด write tool ออก → `blocked` ใน 3 actions (รุ่นแรกวน 8 actions/37,718 tokens)
