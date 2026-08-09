# Kalan Adımlar — Manuel Doğrulama Gerektirenler

Bu ortamın Hepsiburada API'sine ağ erişimi yok, bu yüzden aşağıdaki iki
madde ancak sizin tarafınızda, gerçek kimlik bilgileriyle test edilebilir.

## Madde 4 — HB settlement-only quantity backfill testi

`app.py` içindeki `backfill_hb_settlement_only_quantities()` fonksiyonu
canlı HB kimlik bilgileriyle hiç test edilmedi.

**Test adımları:**
1. `.env` dosyanızda `HEPSIBURADA_MERCHANT_ID/USERNAME/PASSWORD` dolu olsun.
2. Önce kuyrukta kaç kayıt olduğuna bakın:
   ```
   GET /api/backfill-hb-quantities/status
   ```
3. Küçük bir örneklemle deneyin (limit=3-5):
   ```
   POST /api/backfill-hb-quantities?limit=5
   ```
4. Dönen `updated_lines` / `failed` sayılarını kontrol edin. `failed=0` ve
   `updated_lines > 0` ise `order_lines` tablosunda ilgili
   `shipment_package_id` satırlarını sorgulayıp quantity/price alanlarının
   makul göründüğünü (ör. 0 veya null olmadığını) doğrulayın.
5. Sorun yoksa limit'i kademeli artırarak (50, 100...) kuyruğu boşaltın —
   tek seferde tüm geçmişi çekmeyin (rate-limit riski).

## Madde 6 — HB stok endpoint'i offset/limit doğrulaması

`stock_client.py` → `fetch_hepsiburada_stock()` içindeki `offset` parametresi
HB dokümantasyonundan değil, yaygın konvansiyondan varsayıldı.

**Test adımları:**
1. `.env`'de `HEPSIBURADA_ENV=SIT` olduğundan emin olun (test ortamı).
2. `POST /api/stock-sync/hepsiburada` çağırın.
3. Dönen `synced` sayısını, Hepsiburada Merchant Panel'deki gerçek ürün
   sayısıyla karşılaştırın:
   - Sayılar eşleşiyorsa (veya çok yakınsa) → offset/limit doğru çalışıyor.
   - `synced` sayısı toplam ürün sayısından belirgin şekilde azsa → sayfalama
     sessizce erken kesiliyor olabilir (`totalCount` alanı yanlış okunuyor
     veya offset artışı yanlış), `fetch_hepsiburada_stock()` içindeki
     döngüyü debug edin (`total_count` ve `offset` değerlerini loglayın).
4. Doğrulandıktan sonra `HEPSIBURADA_ENV=PROD`'a geçip aynı kontrolü bir kez
   daha yapmanız önerilir (SIT/PROD şema farkı ihtimaline karşı).

---

## Hâlâ blocked olan madde: HB gelecek hakediş scraping (Madde 1)

`external_payout_scraper.py` → `fetch_hb_upcoming_payments()` hâlâ
`NotImplementedError` fırlatıyor. Bunu tamamlamak için tarayıcı DevTools'tan:

1. Hepsiburada satıcı panelinde "Ödemeler"/"Hakediş" ekranına gidin.
2. Network sekmesinde arama kutusuna `payment` veya `upcoming` yazın.
3. İlgili isteği bulup:
   - **Tam URL**'sini,
   - **Request header'larını** (özellikle Cookie),
   - **Örnek response JSON'unu** (Preview/Response sekmesi)

   paylaşın. Bunlarla, Trendyol tarafında (`fetch_ty_upcoming_payments`)
   yapıldığı gibi HB tarafını da tamamlayabilirim.

## 🧾 Otomatik Fatura Kesme (e-Fatura / e-Arşiv) — PLANLANIYOR

**Durum:** Henüz başlanmadı. Backend'e entegre edilecek.

**Amaç:** LAL üzerinden Trendyol/Hepsiburada siparişleri geldiğinde devletin
onayladığı formatta (UBL-TR) otomatik e-Fatura/e-Arşiv kesilmesi. İade
durumlarında otomatik iade faturası (credit note) kesilmesi.

**Yöntem:** GİB'e doğrudan bağlanmak yerine GİB onaylı bir özel entegratör
(Paraşüt / Faturex / EDM / Foriba vb.) REST API'si üzerinden entegrasyon.

**Akış (taslak):**
1. Sipariş/ödeme onayı alındığında webhook tetiklenir
2. Alıcı VKN/TCKN sorgulanır → e-Fatura mükellefi mi, e-Arşiv mi kesilecek belirlenir
3. Sipariş kalemleri + KDV + kargo + iskonto UBL-TR formatında hazırlanır
4. Entegratör API'sine gönderilir, XML+PDF arşivlenir
5. İade akışında aynı mekanizma credit note için tetiklenir
6. Kesilen fatura linki `siparisler.html` ile ilişkilendirilip veritabanına kaydedilir

**Yasal not:** Yıllık ciro eşiği ve pazaryeri sipariş adedi eşikleri (Trendyol/
Hepsiburada) e-belge zorunluluğunu tetikleyebilir — güncel GİB tebliğinden
teyit edilmeli.

**Sonraki adım:** Entegratör seçimi (fiyat/hacim karşılaştırması) → Flask
tarafında yeni bir servis modülü (`invoice_client.py` benzeri) tasarımı.
