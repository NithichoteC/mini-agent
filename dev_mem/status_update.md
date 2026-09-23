# Status update

บันทึกความคืบหน้ารายสัปดาห์ รายการใหม่อยู่บนสุด

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

- เครื่องมือ 5 ตัว (จัดการไฟล์, รัน Python, ดึงเว็บ) ทำงานในโฟลเดอร์ของ session เท่านั้น
- ตัวหยุด 3 แบบ: งบ action, การทำ action ซ้ำ, คำตัดสิน BLOCKED — ทุกกรณีส่งต่อให้คนช่วยแล้วทำต่อได้
- agent ใช้ `qwen/qwen3.8-27b`, reviewer ใช้ `openai/gpt-oss-120b` (Groq)
- 31 offline tests รันได้โดยไม่ต้องมี API key, CI รันทุก push

ผลจริง: งานปกติ 4 แบบผ่านทั้งหมด ใช้ 1–4 actions (702–3,046 tokens)
เมื่อถอดเครื่องมือเขียนไฟล์ออก agent รายงานว่าทำไม่ได้ใน 3 actions แทนที่จะวนจนหมดงบ
