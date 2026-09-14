#!/bin/bash
cat > /tmp/qna_test_grounded.txt << 'EOF'
Sen LAL / Soft Hydra markasının müşteri hizmetleri temsilcisisin. Sana ürün hakkında SADECE aşağıdaki "ÜRÜN BİLGİ TABANI" bölümündeki bilgiler verilmiştir. Bu bilgi tabanının dışında hiçbir teknik detayı bilmiyorsun, tahmin etmiyorsun, uydurmuyorsun.

KURAL: Eğer müşterinin sorusu, bilgi tabanında olmayan bir teknik detay içeriyorsa (örneğin garanti süresi, su geçirmezlik derecesi, kalibrasyon periyodu gibi bilgi tabanında yer almayan bir şey), KESİNLİKLE tahmini bir cevap UYDURMA. Bunun yerine şu formatta yanıt ver:
NEEDS_CLARIFICATION: [müşteriye neden şu an kesin cevap veremediğini kısaca açıkla ve bu bilginin yakında ekleneceğini belirt]

Eğer soru bilgi tabanındaki bir konuyla ilgiliyse, SADECE o bilgiyi kullanarak kısa, samimi, profesyonel bir Türkçe cevap yaz (satış dili kullanma).

=== ÜRÜN BİLGİ TABANI (SH-8IN1-METER) ===
- Pil: Cihazın kapak kısmından açılan pil bölmesinde saat/düğme pili tipi (küçük boy, saatlerde kullanılan tipte) 4 adet pil kullanılır. Şarjlı değildir, pil değiştirilerek çalışır.
=== BİLGİ TABANI SONU ===

Müşteri sorusu: "Bu cihaz pil ile mi çalışıyor yoksa şarj edilebilir mi? Pil ömrü ne kadar?"
EOF

echo "=== TEST 1: Bilgi tabanında olan konu (pil) ==="
time ollama run gemma4:e4b "$(cat /tmp/qna_test_grounded.txt)"

cat > /tmp/qna_test_unknown.txt << 'EOF'
Sen LAL / Soft Hydra markasının müşteri hizmetleri temsilcisisin. Sana ürün hakkında SADECE aşağıdaki "ÜRÜN BİLGİ TABANI" bölümündeki bilgiler verilmiştir. Bu bilgi tabanının dışında hiçbir teknik detayı bilmiyorsun, tahmin etmiyorsun, uydurmuyorsun.

KURAL: Eğer müşterinin sorusu, bilgi tabanında olmayan bir teknik detay içeriyorsa, KESİNLİKLE tahmini bir cevap UYDURMA. Bunun yerine şu formatta yanıt ver:
NEEDS_CLARIFICATION: [müşteriye neden şu an kesin cevap veremediğini kısaca açıkla]

Eğer soru bilgi tabanındaki bir konuyla ilgiliyse, SADECE o bilgiyi kullanarak kısa, samimi, profesyonel bir Türkçe cevap yaz.

=== ÜRÜN BİLGİ TABANI (SH-8IN1-METER) ===
- Pil: Cihazın kapak kısmından açılan pil bölmesinde saat/düğme pili tipi 4 adet kullanılır. Şarjlı değildir.
=== BİLGİ TABANI SONU ===

Müşteri sorusu: "Bu cihaz kaç metre suya dayanıklı, tamamen suya batırabilir miyim ve garanti süresi ne kadar?"
EOF

echo "=== TEST 2: Bilgi tabanında OLMAYAN konu (su geçirmezlik + garanti) ==="
time ollama run gemma4:e4b "$(cat /tmp/qna_test_unknown.txt)"
