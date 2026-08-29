"""
qna_ai_engine.py
------------------
Müşteri sorularına AI taslak cevabı üreten motor. Yerel Ollama (Gemma4:e4b)
kullanır -- Anthropic API veya başka bir ücretli sağlayıcı DEĞİL (29.08.2026
kararı: "ücretsiz/açık kaynak, kendi sunucusunda çalışan model" istendi).

MİMARİ KARAR (29.08.2026 Faz 0, ampirik test):
  Grounding olmadan (yalnızca ürün adı verilip serbest bırakıldığında)
  hem qwen3.5:9b hem gemma4:e4b HALÜSİNASYON yaptı -- ikisi de birbiriyle
  ÇELİŞEN, uydurma pil/şarj bilgisi üretti. Bu KABUL EDİLEMEZ bir risk
  (müşteriye yanlış teknik bilgi = iade/şikayet sebebi).

  Çözüm: "grounding" prompt deseni. Modele SADECE product_knowledge_facts
  tablosundaki fact'ler veriliyor, ve promptta açıkça "bu bilginin dışına
  çıkma, bilmiyorsan NEEDS_CLARIFICATION: [açıklama] formatıyla yanıt ver"
  talimatı var. Bu desen M3/16GB üzerinde gemma4:e4b ile 2 testte (bilinen
  konu + bilinmeyen konu) DOĞRU çalıştığı doğrulandı: model hem eldeki
  bilgiyi doğru kullandı hem de bilmediği bir konuda (su geçirmezlik,
  garanti) uydurmadan NEEDS_CLARIFICATION döndü.

  ÖNEMLİ SINIR: Bu, "model bir daha asla halüsinasyon yapmaz" garantisi
  DEĞİL -- sadece bu iki test senaryosunda gözlemlenen davranış. Üretimde
  gelen gerçek soru çeşitliliğiyle davranış İZLENMELİ.

MODEL SEÇİMİ: gemma4:e4b, qwen3.5:9b'ye göre ~3x daha hızlı ölçüldü
(40sn vs 2dk 05sn, aynı prompt) ve kalite farkı gözlenmedi -- bu yüzden
gemma4:e4b seçildi. Günlük 5-15 soru hacmi için bu hız zaten fazlasıyla
yeterli (taslak, soru sync edilirken arka planda üretiliyor, kullanıcı
beklemiyor).

THINKING BLOĞU: gemma4:e4b "Thinking...\n...\n...done thinking.\n\n<cevap>"
formatında çıktı üretiyor (Ollama'nın thinking-mode modelleri ortak
davranışı). parse_model_response bu bloğu STRIP eder -- müşteriye asla
ham akıl yürütme metni gitmemeli.
"""

import re

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "gemma4:e4b"

_CLARIFICATION_PREFIX = "NEEDS_CLARIFICATION:"

# "Thinking...\n ... \n...done thinking.\n" bloğunu (varsa) baştan siler.
# re.DOTALL: bu blok çok satırlı olabiliyor. Blok yoksa hiçbir şey değişmez.
_THINKING_BLOCK_RE = re.compile(
    r"^\s*Thinking\.\.\..*?\.\.\.done thinking\.\s*\n*",
    re.DOTALL,
)


def build_grounded_prompt(sku, question_text, facts):
    """facts: [{"topic": ..., "fact_text": ...}, ...] -- boş liste de
    geçerli (henüz hiç fact yoksa), bu durumda model her şeyi
    NEEDS_CLARIFICATION olarak işaretlemeye zorlanmış olur çünkü bilgi
    tabanı boş görünür."""
    if facts:
        facts_block = "\n".join(f"- {f['topic']}: {f['fact_text']}" for f in facts)
    else:
        facts_block = "(Bu ürün için henüz kayıtlı bilgi yok.)"

    return f"""Sen LAL / Soft Hydra markasının müşteri hizmetleri temsilcisisin. Sana ürün hakkında SADECE aşağıdaki "ÜRÜN BİLGİ TABANI" bölümündeki bilgiler verilmiştir. Bu bilgi tabanının dışında hiçbir teknik detayı bilmiyorsun, tahmin etmiyorsun, uydurmuyorsun.

KURAL: Eğer müşterinin sorusu, bilgi tabanında olmayan bir teknik detay içeriyorsa, KESİNLİKLE tahmini bir cevap UYDURMA. Bunun yerine şu formatta yanıt ver:
{_CLARIFICATION_PREFIX} [müşteriye neden şu an kesin cevap veremediğini kısaca açıkla]

Eğer soru bilgi tabanındaki bir konuyla ilgiliyse, SADECE o bilgiyi kullanarak kısa, samimi, profesyonel bir Türkçe cevap yaz (satış dili kullanma, 10-2000 karakter arası).

=== ÜRÜN BİLGİ TABANI ({sku}) ===
{facts_block}
=== BİLGİ TABANI SONU ===

Müşteri sorusu: "{question_text}"
"""


def parse_model_response(raw_text):
    """Ollama'nın ham çıktısını {needs_clarification, draft_text,
    clarification_prompt} sözlüğüne çevirir. Thinking bloğunu strip eder."""
    if not raw_text or not raw_text.strip():
        raise ValueError("Model boş yanıt döndürdü, parse edilecek bir şey yok.")

    cleaned = _THINKING_BLOCK_RE.sub("", raw_text).strip()

    if cleaned.startswith(_CLARIFICATION_PREFIX):
        clarification = cleaned[len(_CLARIFICATION_PREFIX):].strip()
        return {
            "needs_clarification": True,
            "draft_text": None,
            "clarification_prompt": clarification,
        }

    return {
        "needs_clarification": False,
        "draft_text": cleaned,
        "clarification_prompt": None,
    }


def generate_draft_answer(sku, question_text, facts):
    """Ollama'ya senkron bir istek atar ve parse edilmiş sonucu döner.
    Ollama servisi ayakta değilse (ConnectionError) İSTEĞE BAĞLI OLARAK
    yutulmaz -- çağıran taraf (sync_task) bunu loglayıp o soruyu bir
    sonraki çalıştırmaya bırakmalı (no silent data absence)."""
    prompt = build_grounded_prompt(sku=sku, question_text=question_text, facts=facts)

    response = requests.post(
        OLLAMA_URL,
        json={"model": MODEL_NAME, "prompt": prompt, "stream": False},
        timeout=180,
    )
    response.raise_for_status()
    raw_text = response.json()["response"]

    parsed = parse_model_response(raw_text)
    parsed["model_used"] = MODEL_NAME
    return parsed
